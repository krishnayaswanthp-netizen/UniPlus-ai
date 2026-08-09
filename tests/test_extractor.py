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

import pytest

from app.schemas.enrichment import IndustrialAttribute
from app.services.extractor import StructuredExtractor, _MAX_INPUT_CHARS

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
