"""
tests/test_llm_70b.py
Stage 9 Verification: Tests for 70B fallback extraction, targeted repair prompts, re-validation, and manual review routing.
"""

import pytest
from app.schemas.product import ProductRecord, ProductIdentity, RawInputData, RowStatus, ExtractionSource
from app.services.rate_limiter import AdaptiveRateLimiter
from app.services.validator import ValidationEngine
from app.services.llm_8b import ExtractedField, LLMExtractionOutput
from app.services.llm_70b import LLM70BFallbackExtractor


class Mock70BClient:
    """Mock 70B client repairing failed fields."""

    def __init__(self, repaired_fields=None):
        self.repaired_fields = repaired_fields or [
            ExtractedField(
                field_name="voltage",
                raw_value="24V",
                normalized_value="24",
                unit="V",
                confidence=0.95,
            ),
            # ``grit`` is required for the Abrasives category — included in the
            # default repair set so the re-validation tri-signal gate passes
            # with completeness 1.0 for ``category="Abrasives"``.
            ExtractedField(
                field_name="grit",
                raw_value="P120",
                normalized_value="P120",
                confidence=0.95,
            ),
            ExtractedField(
                field_name="dimensions",
                raw_value="5 in",
                normalized_value="5",
                unit="in",
                confidence=0.95,
            ),
            ExtractedField(
                field_name="material",
                raw_value="Stainless Steel",
                normalized_value="Stainless Steel",
                confidence=0.95,
            ),
        ]

    async def extract(self, prompt, response_model):
        return LLMExtractionOutput(attributes=self.repaired_fields)


class _FakeResponse:
    """Minimal stand-in for an httpx-style exception response."""

    def __init__(self, headers):
        self.headers = headers


class _RateLimitedError(RuntimeError):
    """429-style exception carrying a Retry-After header."""

    def __init__(self, headers=None):
        super().__init__("429 rate limit exceeded")
        self.response = _FakeResponse(headers or {})


class FlakyMock70BClient:
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
                    confidence=0.95,
                )
            ]
        )


@pytest.mark.asyncio
async def test_extract_fallback_repairs_attributes():
    limiter = AdaptiveRateLimiter(max_rpm=10, max_tpm=5000)
    extractor = LLM70BFallbackExtractor(api_keys=["key1"], rate_limiter=limiter)

    identity = ProductIdentity(
        row_id=1,
        mfg_part_number="775L",
        manufacturer="3M",
        raw_description="3M Disc",
        category="Abrasives",
    )
    record = ProductRecord(
        identity=identity, raw_data=RawInputData(original_row_index=0)
    )
    record.quality.validation_flags = ["INVALID_VOLTAGE_NEGATIVE"]

    mock_client = Mock70BClient()
    extracted = await extractor.extract_fallback(
        record, text_context="Voltage: 24V", client_override=mock_client
    )

    assert "voltage" in extracted
    assert extracted["voltage"].source == ExtractionSource.LLM_70B_FALLBACK
    assert record.processing.llm_70b_time_ms > 0.0


@pytest.mark.asyncio
async def test_process_record_revalidates_and_routes_to_provenance_merge():
    limiter = AdaptiveRateLimiter(max_rpm=10, max_tpm=5000)
    extractor = LLM70BFallbackExtractor(api_keys=["key1"], rate_limiter=limiter)

    identity = ProductIdentity(
        row_id=1,
        mfg_part_number="775L",
        manufacturer="3M",
        raw_description="3M Disc",
        category="Abrasives",
    )
    record = ProductRecord(
        identity=identity, raw_data=RawInputData(original_row_index=0)
    )

    mock_client = Mock70BClient()
    processed = await extractor.process_record(record, client_override=mock_client)

    assert processed.status == RowStatus.PROVENANCE_MERGE
    assert processed.quality.validity is True
    assert processed.quality.completeness == 1.0


@pytest.mark.asyncio
async def test_process_record_routes_to_manual_review_on_persistent_failure():
    limiter = AdaptiveRateLimiter(max_rpm=10, max_tpm=5000)
    extractor = LLM70BFallbackExtractor(api_keys=["key1"], rate_limiter=limiter)

    identity = ProductIdentity(
        row_id=2,
        mfg_part_number="X",
        manufacturer="M",
        raw_description="D",
        category="Electrical",
    )
    record = ProductRecord(
        identity=identity, raw_data=RawInputData(original_row_index=0)
    )

    # Mock client returning low confidence that fails re-validation
    failing_client = Mock70BClient(
        repaired_fields=[
            ExtractedField(
                field_name="voltage",
                raw_value="24V",
                normalized_value="24",
                unit="V",
                confidence=0.3,
            )
        ]
    )

    processed = await extractor.process_record(
        record, client_override=failing_client
    )

    assert processed.status == RowStatus.MANUAL_REVIEW
    assert processed.quality.overall_confidence < 0.8


@pytest.mark.asyncio
async def test_extract_fallback_429_retries_and_rotates_key():
    """A 429 with a Retry-After header pauses, rotates the key, and retries."""
    limiter = AdaptiveRateLimiter(max_rpm=10, max_tpm=5000)
    extractor = LLM70BFallbackExtractor(
        api_keys=["key1", "key2"], rate_limiter=limiter
    )

    identity = ProductIdentity(
        row_id=1,
        mfg_part_number="775L",
        manufacturer="3M",
        raw_description="3M Disc",
        category="Abrasives",
    )
    record = ProductRecord(
        identity=identity, raw_data=RawInputData(original_row_index=0)
    )

    mock_client = FlakyMock70BClient(retry_after="0.05")
    extracted = await extractor.extract_fallback(
        record, "Voltage: 24V", client_override=mock_client
    )

    assert mock_client.calls == 2
    assert "voltage" in extracted
    assert extractor._get_current_key() == "key2"  # rotated after the 429
    assert record.errors == []  # eventual success -> nothing recorded


def test_key_rotation_failover():
    extractor = LLM70BFallbackExtractor(api_keys=["key1", "key2", "key3"])
    assert extractor._get_current_key() == "key1"
    assert extractor._rotate_key() == "key2"
    assert extractor._rotate_key() == "key3"
    assert extractor._rotate_key() == "key1"


def test_build_targeted_prompt_contains_flags_and_truncated_context():
    extractor = LLM70BFallbackExtractor(api_keys=["key1"])
    identity = ProductIdentity(
        row_id=1,
        mfg_part_number="775L",
        manufacturer="3M",
        raw_description="3M Disc",
        category="Abrasives",
    )
    record = ProductRecord(
        identity=identity, raw_data=RawInputData(original_row_index=0)
    )
    record.quality.validation_flags = ["INVALID_VOLTAGE_NEGATIVE"]

    long_context = "Voltage: 24V DC, Diameter: 5 in. " * 300  # ~4,800 chars
    prompt = extractor.build_targeted_prompt(record, long_context)

    assert "INVALID_VOLTAGE_NEGATIVE" in prompt
    assert "3M" in prompt
    assert "775L" in prompt
    # Context is truncated to 1,500 chars; the small amount of added prompt
    # guidance (unique-field / plain-JSON rules) keeps the total under 2,200.
    # Without truncation a 4,800-char context would make the prompt ~5,300.
    assert len(prompt) < 2200


def test_repair_json_string():
    from app.services.llm_70b import repair_json_string

    raw1 = '<function={"attributes": [{"field_name" = "voltage", "raw_value": "120V",}]}></function>'
    repaired1 = repair_json_string(raw1)
    assert '<function' not in repaired1
    assert '"field_name": "voltage"' in repaired1
    assert '"raw_value": "120V"' in repaired1

    raw2 = '```json\n{"tasks" = [{"field_name" = "power", "raw_value" = "500W"}]}\n```'
    repaired2 = repair_json_string(raw2)
    assert '```' not in repaired2
    assert '"tasks":' in repaired2
    assert '"field_name": "power"' in repaired2
    assert '"raw_value": "500W"' in repaired2


@pytest.mark.asyncio
async def test_extract_fallback_tpd_exhaustion_switches_model():
    limiter = AdaptiveRateLimiter(max_rpm=10, max_tpm=5000)
    extractor = LLM70BFallbackExtractor(api_keys=["key1"], rate_limiter=limiter)
    assert extractor.model_name == "openai/gpt-oss-120b"

    identity = ProductIdentity(
        row_id=1,
        mfg_part_number="775L",
        manufacturer="3M",
        raw_description="3M Disc",
    )
    record = ProductRecord(
        identity=identity, raw_data=RawInputData(original_row_index=0)
    )

    class TPDErrorClient:
        async def extract(self, prompt, response_model):
            raise RuntimeError(
                "Rate limit reached on tokens per day (TPD): Limit 100000, Used 99954. Please try again in 10m34s."
            )

    tpd_client = TPDErrorClient()
    await extractor.extract_fallback(record, "Voltage: 24V", client_override=tpd_client)

    # Upon TPD exhaustion, the model name switches to openai/gpt-oss-20b
    assert extractor.model_name == "openai/gpt-oss-20b"
    assert len(record.errors) > 0


@pytest.mark.asyncio
async def test_extract_fallback_repairs_failed_generation():
    limiter = AdaptiveRateLimiter(max_rpm=10, max_tpm=5000)
    extractor = LLM70BFallbackExtractor(api_keys=["key1"], rate_limiter=limiter)

    identity = ProductIdentity(
        row_id=1,
        mfg_part_number="775L",
        manufacturer="3M",
        raw_description="3M Disc",
    )
    record = ProductRecord(
        identity=identity, raw_data=RawInputData(original_row_index=0)
    )

    class FailedGenError(RuntimeError):
        def __init__(self, failed_gen):
            super().__init__("400 tool_use_failed JSON generation failed")
            self.status_code = 400
            self.failed_generation = failed_gen

    class FailedGenClient:
        async def extract(self, prompt, response_model):
            raise FailedGenError(
                '<function={"attributes": [{"field_name" = "voltage", "raw_value" = "24V"}]}></function>'
            )

    client = FailedGenClient()
    extracted = await extractor.extract_fallback(record, "Voltage: 24V", client_override=client)

    assert "voltage" in extracted
    assert extracted["voltage"].raw_value == "24V"


def test_map_response_to_attributes_skips_null_fields():
    extractor = LLM70BFallbackExtractor(api_keys=["key1"])
    identity = ProductIdentity(
        row_id=1,
        mfg_part_number="775L",
        manufacturer="3M",
        raw_description="3M Disc",
    )
    record = ProductRecord(
        identity=identity, raw_data=RawInputData(original_row_index=0)
    )

    response = LLMExtractionOutput(
        attributes=[
            ExtractedField(field_name=None, raw_value="24V"),
            ExtractedField(field_name="voltage", raw_value=None),
            ExtractedField(field_name="power", raw_value="500W"),
        ]
    )

    mapped = extractor._map_response_to_attributes(response, "Context", record)
    assert "power" in mapped
    assert None not in mapped
    assert "voltage" not in mapped
    assert len(mapped) == 1


def test_coerce_field_filters_sdk_metadata_keys_70b() -> None:
    """SDK metadata keys (id, choices, etc.) must never be coerced into ExtractedField."""
    for key in ("id", "choices", "created", "model", "object", "system_fingerprint", "usage", "service_tier"):
        assert LLM70BFallbackExtractor._coerce_field({"field_name": key, "raw_value": "123"}) is None
        assert LLM70BFallbackExtractor._coerce_field((key, "123")) is None

    valid = LLM70BFallbackExtractor._coerce_field({"field_name": "voltage", "raw_value": "120V"})
    assert valid is not None
    assert valid.field_name == "voltage"

