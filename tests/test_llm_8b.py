"""
tests/test_llm_8b.py
Stage 7 Verification: Tests for 8B LLM extraction, mock client execution,
429 retries, and status transitions.
"""

import pytest

from app.schemas.product import (
    AttributeValue,
    ExtractionSource,
    ProductIdentity,
    ProductRecord,
    RawInputData,
    RowStatus,
)
from app.services.llm_8b import (
    _is_tool_use_failure,
    _parse_retry_after,
    ExtractedField,
    LLMExtractionOutput,
    LLM8BExtractor,
)
from app.services.rate_limiter import AdaptiveRateLimiter


class MockGroqClient:
    """Mock LLM client simulating an Instructor structured-output response."""

    def __init__(self, return_fields=None):
        self.return_fields = return_fields or [
            ExtractedField(
                field_name="voltage",
                raw_value="24V",
                normalized_value="24",
                unit="V",
                confidence=0.9,
            ),
            ExtractedField(
                field_name="material",
                raw_value="Stainless Steel",
                normalized_value="Stainless Steel",
                confidence=0.85,
            ),
        ]

    async def extract(self, prompt, response_model):
        return LLMExtractionOutput(attributes=self.return_fields)


class _FakeResponse:
    """Minimal stand-in for an httpx-style exception response."""

    def __init__(self, headers):
        self.headers = headers


class _RateLimitedError(RuntimeError):
    """429-style exception carrying a Retry-After header."""

    def __init__(self, headers=None):
        super().__init__("429 rate limit exceeded")
        self.response = _FakeResponse(headers or {})


class FlakyMockClient:
    """Raises a 429 on the first call, succeeds on the second."""

    def __init__(self, retry_after: str | None = None):
        self.calls = 0
        self.retry_after = retry_after

    async def extract(self, prompt, response_model):
        self.calls += 1
        if self.calls == 1:
            headers = (
                {"retry-after": self.retry_after} if self.retry_after else {}
            )
            raise _RateLimitedError(headers)
        return LLMExtractionOutput(
            attributes=[
                ExtractedField(
                    field_name="voltage",
                    raw_value="24V",
                    normalized_value="24",
                    unit="V",
                    confidence=0.9,
                )
            ]
        )


@pytest.mark.asyncio
async def test_extract_specs_mock_client() -> None:
    limiter = AdaptiveRateLimiter(max_rpm=10, max_tpm=5000)
    extractor = LLM8BExtractor(api_keys=["key1", "key2"], rate_limiter=limiter)

    identity = ProductIdentity(
        row_id=1,
        mfg_part_number="775L",
        manufacturer="3M",
        raw_description="3M Disc 24V",
    )
    record = ProductRecord(
        identity=identity, raw_data=RawInputData(original_row_index=0)
    )

    mock_client = MockGroqClient()
    extracted = await extractor.extract_specs(
        record, text_context="Voltage: 24V DC", client_override=mock_client
    )

    assert "voltage" in extracted
    assert extracted["voltage"].normalized_value == "24"
    assert extracted["voltage"].unit == "V"
    assert extracted["voltage"].source == ExtractionSource.LLM_8B
    assert record.processing.llm_8b_time_ms > 0.0
    assert record.processing.tokens_consumed > 0


@pytest.mark.asyncio
async def test_process_record_transitions_to_validating_8b() -> None:
    limiter = AdaptiveRateLimiter(max_rpm=10, max_tpm=5000)
    extractor = LLM8BExtractor(api_keys=["key1"], rate_limiter=limiter)

    identity = ProductIdentity(
        row_id=1,
        mfg_part_number="775L",
        manufacturer="3M",
        raw_description="3M Disc",
    )
    record = ProductRecord(
        identity=identity, raw_data=RawInputData(original_row_index=0)
    )
    record.retrieval.reduced_text_snippet = "3M 775L Disc 24V Stainless Steel"

    mock_client = MockGroqClient()
    processed = await extractor.process_record(record, client_override=mock_client)

    assert processed.status == RowStatus.VALIDATING_8B
    assert "voltage" in processed.attributes
    assert "material" in processed.attributes
    assert processed.attributes["voltage"].source == ExtractionSource.LLM_8B


@pytest.mark.asyncio
async def test_merge_skips_lower_confidence_existing_attribute() -> None:
    """A high-confidence REGEX attribute survives the LLM merge."""
    limiter = AdaptiveRateLimiter(max_rpm=10, max_tpm=5000)
    extractor = LLM8BExtractor(api_keys=["key1"], rate_limiter=limiter)

    identity = ProductIdentity(
        row_id=1,
        mfg_part_number="775L",
        manufacturer="3M",
        raw_description="3M Disc",
    )
    record = ProductRecord(
        identity=identity, raw_data=RawInputData(original_row_index=0)
    )
    record.attributes["voltage"] = AttributeValue(
        field_name="voltage",
        raw_value="24V",
        normalized_value="24",
        unit="V",
        confidence=0.99,
        source=ExtractionSource.REGEX,
    )

    mock_client = MockGroqClient()  # offers voltage with confidence 0.9
    processed = await extractor.process_record(record, client_override=mock_client)

    assert processed.attributes["voltage"].source == ExtractionSource.REGEX
    assert processed.attributes["voltage"].confidence == 0.99
    # The brand-new material field is still merged in.
    assert processed.attributes["material"].source == ExtractionSource.LLM_8B


@pytest.mark.asyncio
async def test_extract_specs_429_retries_and_rotates_key() -> None:
    """A 429 with a Retry-After header pauses, rotates the key, and retries."""
    limiter = AdaptiveRateLimiter(max_rpm=10, max_tpm=5000)
    extractor = LLM8BExtractor(api_keys=["key1", "key2"], rate_limiter=limiter)

    identity = ProductIdentity(
        row_id=1,
        mfg_part_number="775L",
        manufacturer="3M",
        raw_description="3M Disc",
    )
    record = ProductRecord(
        identity=identity, raw_data=RawInputData(original_row_index=0)
    )

    mock_client = FlakyMockClient(retry_after="0.05")
    extracted = await extractor.extract_specs(
        record, "Voltage: 24V DC", client_override=mock_client
    )

    assert mock_client.calls == 2
    assert "voltage" in extracted
    assert extractor._get_current_key() == "key2"  # rotated after the 429
    assert record.errors == []  # eventual success -> nothing recorded


def test_key_rotation_failover() -> None:
    extractor = LLM8BExtractor(api_keys=["key1", "key2", "key3"])
    assert extractor._get_current_key() == "key1"
    assert extractor._rotate_key() == "key2"
    assert extractor._rotate_key() == "key3"
    assert extractor._rotate_key() == "key1"


def test_parse_retry_after_reads_groq_message_body() -> None:
    """The TPM message hint ("Please try again in X.XXXs") is honored even
    when no Retry-After header is present."""
    err = RuntimeError(
        "Rate limit reached for model llama-3.1-70b-versatile on tokens per "
        "minute (TPM): Limit 12000, Used 11898, Requested 886. "
        "Please try again in 3.455s."
    )
    assert _parse_retry_after(err) == 3.455


class _GroqBadRequest:
    """Minimal stand-in for groq.BadRequestError (status_code + body)."""

    def __init__(self, status_code: int | None, body: dict | None) -> None:
        self.status_code = status_code
        self.body = body


def test_is_tool_use_failure_detects_groq_400() -> None:
    """A 400 with code/message mentioning tool_use is flagged; other 400s and
    429s are not."""
    tool_use_err = _GroqBadRequest(
        400,
        {"error": {"code": "tool_use_failed", "message": "Failed to call a function."}},
    )
    assert _is_tool_use_failure(tool_use_err) is True

    other_400 = _GroqBadRequest(400, {"error": {"message": "invalid category"}})
    assert _is_tool_use_failure(other_400) is False

    rate_limited = _GroqBadRequest(429, {"error": {"message": "rate limit reached"}})
    assert _is_tool_use_failure(rate_limited) is False

    no_signal = RuntimeError("boom")
    assert _is_tool_use_failure(no_signal) is False


def test_build_prompt_contains_identity_and_truncated_context() -> None:
    extractor = LLM8BExtractor(api_keys=["key1"])
    identity = ProductIdentity(
        row_id=1,
        mfg_part_number="775L",
        manufacturer="3M",
        raw_description="3M Disc",
    )
    record = ProductRecord(
        identity=identity, raw_data=RawInputData(original_row_index=0)
    )

    long_context = "Voltage: 24V DC, Diameter: 5 in. " * 300  # ~4,800 chars
    prompt = extractor.build_prompt(record, long_context)

    assert "3M" in prompt
    assert "775L" in prompt
    assert "Voltage: 24V DC" in prompt
    assert len(prompt) < 2000  # context truncated to 1,500 chars


def test_coerce_field_filters_sdk_metadata_keys() -> None:
    """SDK metadata keys (id, choices, etc.) must never be coerced into ExtractedField."""
    for key in ("id", "choices", "created", "model", "object", "system_fingerprint", "usage", "service_tier"):
        assert LLM8BExtractor._coerce_field({"field_name": key, "raw_value": "123"}) is None
        assert LLM8BExtractor._coerce_field((key, "123")) is None

    valid = LLM8BExtractor._coerce_field({"field_name": "voltage", "raw_value": "120V"})
    assert valid is not None
    assert valid.field_name == "voltage"

