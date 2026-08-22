"""Tests for LLM extraction edge-case handling (empty results, truncation).

Covers the two observability signals added to ``StructuredExtractor``:

- an empty extraction (model returned valid output but zero attributes) is
  flagged with a warning instead of passing silently;
- input text longer than ``_MAX_INPUT_CHARS`` logs a truncation warning in
  ``_build_messages``.

The LLM is never called — ``_request_attributes`` is monkeypatched where
needed, and the tests run fully offline.
"""

from __future__ import annotations

import logging
from typing import Any

import groq
import httpx
import pytest

from app.schemas.enrichment import IndustrialAttribute
from app.services.extractor import (
    StructuredExtractor,
    _MAX_INPUT_CHARS,
    _sanitize_aggressive,
    _sanitize_dimension_quotes,
)

_EXTRACTOR_LOGGER = "app.services.extractor"


@pytest.fixture
def extractor() -> StructuredExtractor:
    return StructuredExtractor(api_key="test-key")


# ---------------------------------------------------------------------------
# Empty extraction flag
# ---------------------------------------------------------------------------


def test_empty_extraction_logs_warning(
    extractor: StructuredExtractor,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A model response with zero attributes warns instead of silent success."""
    monkeypatch.setattr(
        StructuredExtractor, "_request_attributes", lambda self, messages: []
    )

    with caplog.at_level(logging.WARNING, logger=_EXTRACTOR_LOGGER):
        result = extractor.extract_product_specs(
            "Voltage: 120 VAC", "https://example.com/spec", "HVAC"
        )

    assert result == []
    warning_records = [
        record for record in caplog.records if record.levelno >= logging.WARNING
    ]
    assert warning_records
    assert any(
        "empty" in record.message.lower() or "no attributes" in record.message.lower()
        for record in warning_records
    )


def test_empty_extraction_warning_carries_source_and_category(
    extractor: StructuredExtractor,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The empty-extraction warning names the source and category."""
    monkeypatch.setattr(
        StructuredExtractor, "_request_attributes", lambda self, messages: []
    )

    with caplog.at_level(logging.WARNING, logger=_EXTRACTOR_LOGGER):
        extractor.extract_product_specs("x", "https://acme.com/spec.pdf", "Plumbing")

    message = " ".join(record.message for record in caplog.records).lower()
    assert "https://acme.com/spec.pdf" in message
    assert "plumbing" in message


def test_blank_input_does_not_log_empty_warning(
    extractor: StructuredExtractor,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The flag fires only for model-returned emptiness, not blank short-circuits."""
    with caplog.at_level(logging.WARNING, logger=_EXTRACTOR_LOGGER):
        result = extractor.extract_product_specs("   ", "https://example.com", "HVAC")

    assert result == []
    assert not any(
        "empty" in record.message.lower() or "no attributes" in record.message.lower()
        for record in caplog.records
    )


def test_non_empty_extraction_does_not_log_warning(
    extractor: StructuredExtractor,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Normal extractions with attributes produce no empty-extraction warning."""
    attribute = IndustrialAttribute(
        field_name="voltage",
        raw_value="120 VAC",
        normalized_value="120",
        unit="V",
        confidence_score=0.9,
        source_url="https://example.com/spec",
    )
    monkeypatch.setattr(
        StructuredExtractor,
        "_request_attributes",
        lambda self, messages: [attribute],
    )

    with caplog.at_level(logging.WARNING, logger=_EXTRACTOR_LOGGER):
        result = extractor.extract_product_specs(
            "Voltage: 120 VAC", "https://example.com/spec", "HVAC"
        )

    assert len(result) == 1
    assert not any(
        "empty" in record.message.lower() or "no attributes" in record.message.lower()
        for record in caplog.records
    )


# ---------------------------------------------------------------------------
# Character truncation signal
# ---------------------------------------------------------------------------


def test_build_messages_logs_truncation_warning(
    extractor: StructuredExtractor,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Text over the char limit logs a warning; the prompt is truncated."""
    long_text = "x" * (_MAX_INPUT_CHARS + 1)

    with caplog.at_level(logging.WARNING, logger=_EXTRACTOR_LOGGER):
        messages = extractor._build_messages(long_text, "https://example.com", "HVAC")

    assert len(messages) == 2
    # The document body is truncated to exactly the char limit inside the
    # prompt (the category/source prefix is unaffected).
    assert messages[1]["content"].endswith("x" * _MAX_INPUT_CHARS)
    assert any(
        "truncat" in record.message.lower() for record in caplog.records
    )


def test_build_messages_silent_at_limit(
    extractor: StructuredExtractor,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Text exactly at the limit is not truncated and logs nothing."""
    at_limit = "x" * _MAX_INPUT_CHARS

    with caplog.at_level(logging.WARNING, logger=_EXTRACTOR_LOGGER):
        extractor._build_messages(at_limit, "https://example.com", "HVAC")

    assert not any(
        "truncat" in record.message.lower() for record in caplog.records
    )


# ---------------------------------------------------------------------------
# Input sanitization (tool-use JSON safety)
# ---------------------------------------------------------------------------


def test_sanitize_dimension_quotes() -> None:
    """Feet/inches quote notation becomes explicit unit words."""
    assert _sanitize_dimension_quotes("16' long") == "16 ft long"
    assert _sanitize_dimension_quotes('1/2" NPT') == "1/2 in NPT"
    assert _sanitize_dimension_quotes('1 1/2" pipe') == "1 1/2 in pipe"
    assert _sanitize_dimension_quotes('12.5" hose') == "12.5 in hose"
    assert _sanitize_dimension_quotes('Height 6\' 2"') == "Height 6 ft 2 in"
    # Word apostrophes are untouched (the patterns require a digit).
    assert (
        _sanitize_dimension_quotes("manufacturer's part")
        == "manufacturer's part"
    )
    # Text without quote notation passes through unchanged.
    assert _sanitize_dimension_quotes("120 VAC, 800 CFM") == "120 VAC, 800 CFM"


def test_sanitize_aggressive() -> None:
    """The retry-level sanitizer converts digit-adjacent quotes and strips the
    rest, so the prompt can no longer corrupt tool-use JSON generation."""
    assert _sanitize_aggressive("it's a 12\" hose") == "its a 12 in hose"
    assert _sanitize_aggressive('1/2" NPT "threaded"') == "1/2 in NPT threaded"


def test_extract_product_specs_sanitizes_quotes_before_request(
    extractor: StructuredExtractor,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The LLM never sees raw feet/inches quote characters: sanitization runs
    before ``_build_messages``."""
    captured: dict[str, str] = {}

    def fake_request_attributes(
        self: StructuredExtractor, messages: list[dict[str, str]]
    ) -> list[IndustrialAttribute]:
        captured["content"] = messages[1]["content"]
        return []

    monkeypatch.setattr(
        StructuredExtractor, "_request_attributes", fake_request_attributes
    )
    extractor.extract_product_specs(
        "Pipe 16' long, 1/2\" NPT", "https://example.com", "Plumbing"
    )

    assert "16 ft long" in captured["content"]
    assert "1/2 in NPT" in captured["content"]
    assert "'" not in captured["content"]


# ---------------------------------------------------------------------------
# 400 tool_use_failed -> Mode.JSON fallback
# ---------------------------------------------------------------------------


class _FakeCompletions:
    """Nested stand-in for ``client.chat.completions``."""

    def __init__(self, owner: Any) -> None:
        self.owner = owner

    def create(self, **kwargs: object) -> list[IndustrialAttribute]:
        return self.owner._create(**kwargs)


class _FakeChat:
    """Nested stand-in for ``client.chat``."""

    def __init__(self, owner: Any) -> None:
        self.completions = _FakeCompletions(owner)


def _tool_use_error() -> groq.BadRequestError:
    """A real ``groq.BadRequestError`` carrying the ``tool_use_failed`` code."""
    request = httpx.Request(
        "POST", "https://api.groq.com/openai/v1/chat/completions"
    )
    response = httpx.Response(
        400,
        request=request,
        json={
            "error": {
                "code": "tool_use_failed",
                "message": "Failed to call a function. Please adjust your prompt.",
            }
        },
    )
    return groq.BadRequestError(
        "400 Bad Request", response=response, body=response.json()
    )


def _rate_limit_error(message: str) -> groq.RateLimitError:
    """A real ``groq.RateLimitError`` carrying a TPM message body."""
    request = httpx.Request(
        "POST", "https://api.groq.com/openai/v1/chat/completions"
    )
    response = httpx.Response(
        429, request=request, json={"error": {"message": message}}
    )
    return groq.RateLimitError(
        "429 Too Many Requests", response=response, body=response.json()
    )


class _ToolUseFlakyClient:
    """Fake instructor client: raises ``tool_use_failed`` twice (raw TOOLS +
    sanitized TOOLS), then succeeds once routed through the JSON fallback."""

    def __init__(self, attribute: IndustrialAttribute) -> None:
        self.calls = 0
        self.attribute = attribute
        self.last_messages: list[dict[str, str]] = []

    @property
    def chat(self) -> _FakeChat:
        return _FakeChat(self)

    def _create(self, **kwargs: object) -> list[IndustrialAttribute]:
        self.calls += 1
        self.last_messages = list(kwargs["messages"])  # type: ignore[arg-type]
        if self.calls <= 2:
            raise _tool_use_error()
        return [self.attribute]


def _sample_attribute() -> IndustrialAttribute:
    return IndustrialAttribute(
        field_name="voltage",
        raw_value="120 VAC",
        normalized_value="120",
        unit="V",
        confidence_score=0.9,
        source_url="https://example.com/spec",
    )


def test_tool_use_failure_falls_back_to_json_mode(
    extractor: StructuredExtractor,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A 400 ``tool_use_failed`` first retries the same model on a sanitized
    prompt, then falls back to the Mode.JSON client instead of failing the
    extraction."""
    fake = _ToolUseFlakyClient(_sample_attribute())
    extractor._clients = [fake]
    # The JSON fallback client is built lazily by ``_json_client``; point it at
    # the same fake so no real instructor/Groq client is constructed.
    monkeypatch.setattr(extractor, "_json_client", lambda index: fake)

    result = extractor._request_attributes(
        [{"role": "user", "content": "Pipe 16' long, 1/2\" NPT"}]
    )

    # Attempts: raw TOOLS (fail) -> sanitized TOOLS (fail) -> JSON (succeed).
    assert fake.calls == 3
    assert result == [fake.attribute]
    # The retry USER prompt was aggressively sanitized (quote characters
    # stripped); the system prompt is left untouched by the sanitizer.
    retry_user_content = " ".join(
        message.get("content", "")
        for message in fake.last_messages
        if message.get("role") == "user"
    )
    assert "'" not in retry_user_content and '"' not in retry_user_content


def test_tool_use_failure_exhausted_raises(
    extractor: StructuredExtractor,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Persistent ``tool_use_failed`` across TOOLS-sanitize and JSON modes
    eventually raises after both models are exhausted."""
    fake = _ToolUseFlakyClient(_sample_attribute())

    def always_fail(**kwargs: object) -> list[IndustrialAttribute]:
        fake.calls += 1
        raise _tool_use_error()

    fake._create = always_fail  # type: ignore[method-assign]
    extractor._clients = [fake]
    monkeypatch.setattr(extractor, "_json_client", lambda index: fake)

    with pytest.raises(RuntimeError, match="Structured extraction failed"):
        extractor._request_attributes([{"role": "user", "content": "x"}])

    # 3 attempts per model (TOOLS raw, TOOLS sanitized, JSON) x 2 models.
    assert fake.calls == 6


def test_429_sleeps_with_parsed_message_backoff(
    extractor: StructuredExtractor,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A single-key 429 honors Groq's "Please try again in X.XXXs" message:
    the parsed window is buffered (1.25x + 0.5s) before the retry."""
    fake = _ToolUseFlakyClient(_sample_attribute())

    def rate_limit_then_succeed(**kwargs: object) -> list[IndustrialAttribute]:
        fake.calls += 1
        if fake.calls == 1:
            raise _rate_limit_error(
                "Rate limit reached for model llama-3.3-70b-versatile on tokens "
                "per minute (TPM): Limit 12000, Used 11898, Requested 886. "
                "Please try again in 1.2s."
            )
        return [fake.attribute]

    fake._create = rate_limit_then_succeed  # type: ignore[method-assign]
    extractor._clients = [fake]
    sleeps: list[float] = []
    monkeypatch.setattr("app.services.extractor.time.sleep", sleeps.append)

    result = extractor._request_attributes([{"role": "user", "content": "x"}])

    assert fake.calls == 2
    assert result == [fake.attribute]
    # 1.2s * 1.25 + 0.5s safety buffer, exactly one sleep before the retry.
    assert sleeps == [pytest.approx(1.2 * 1.25 + 0.5)]


def test_chat_completion_does_not_leak_top_level_fields(
    extractor: StructuredExtractor,
) -> None:
    """ChatCompletion response with id and choices metadata must NEVER produce
    attribute rows for 'id' or 'choices'."""
    import json
    class FakeChoice:
        def __init__(self, content: str) -> None:
            self.message = type("Msg", (), {"content": content})()
            self.finish_reason = "stop"

    class FakeChatCompletion:
        def __init__(self, content: str) -> None:
            self.id = "chatcmpl-c16c0067-test-id"
            self.object = "chat.completion"
            self.created = 1740000000
            self.model = "openai/gpt-oss-20b"
            self.choices = [FakeChoice(content)]

        def __iter__(self):
            # Emulate Pydantic/OpenAI model iteration yielding field pairs
            yield ("id", self.id)
            yield ("object", self.object)
            yield ("created", self.created)
            yield ("model", self.model)
            yield ("choices", self.choices)

    raw_json = json.dumps({
        "attributes": [
            {
                "field_name": "voltage",
                "raw_value": "120V",
                "normalized_value": "120",
                "unit": "V",
                "confidence_score": 0.98,
            }
        ]
    })
    chat_resp = FakeChatCompletion(raw_json)
    parsed = extractor._parse_completion_response(chat_resp)

    assert len(parsed) == 1
    assert parsed[0].field_name == "voltage"
    assert parsed[0].raw_value == "120V"
    assert not any(a.field_name in ("id", "choices", "object", "model", "created") for a in parsed)

