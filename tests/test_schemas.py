"""
tests/test_schemas.py
Stage 1 Verification: Test schema instantiation, SKU generation, and status tracking.
"""

from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from app.schemas.product import (
    ProductRecord,
    ProductIdentity,
    RawInputData,
    RowStatus,
    ExtractionSource,
    AttributeValue,
)


def test_product_record_instantiation_and_sku():
    identity = ProductIdentity(
        row_id=1,
        mfg_part_number="775L",
        manufacturer="3M Inc",
        raw_description="3M 775L Stikit Film P120 5 in 24V",
        category="Abrasives",
    )
    raw = RawInputData(original_row_index=0, raw_headers={"Part": "775L"})

    record = ProductRecord(identity=identity, raw_data=raw)

    assert record.identity.sku_id == "3M_Inc-775L"
    assert record.status == RowStatus.ROW_READY
    assert len(record.attributes) == 0


def test_attribute_value_and_error_tracking():
    identity = ProductIdentity(
        row_id=2,
        mfg_part_number="24V-RELAY",
        manufacturer="Omron",
        raw_description="Omron 24V Power Relay",
    )
    record = ProductRecord(identity=identity, raw_data=RawInputData(original_row_index=1))

    record.attributes["voltage"] = AttributeValue(
        field_name="voltage",
        raw_value="24V",
        normalized_value="24",
        unit="V",
        confidence=0.99,
        source=ExtractionSource.REGEX,
    )

    record.record_error(RowStatus.EXTRACTING_8B, ValueError("Rate limit test"))

    assert record.attributes["voltage"].source == "regex"
    assert len(record.errors) == 1
    assert record.errors[0].stage == RowStatus.EXTRACTING_8B


def _naive_utc_now() -> datetime:
    """Naive-UTC ``datetime.now`` (avoids the 3.12 ``utcnow`` deprecation)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _make_record(row_id: int, part: str, mfr: str) -> ProductRecord:
    identity = ProductIdentity(
        row_id=row_id,
        mfg_part_number=part,
        manufacturer=mfr,
        raw_description=f"{mfr} {part}",
    )
    return ProductRecord(
        identity=identity, raw_data=RawInputData(original_row_index=row_id - 1)
    )


def test_mark_completed_sets_status_and_timing():
    record = _make_record(3, "X1", "Bosch")
    record.processing.start_time = _naive_utc_now() - timedelta(seconds=2)
    record.mark_completed()
    assert record.status == RowStatus.COMPLETED
    assert record.processing.end_time is not None
    assert record.processing.total_time_ms >= 2000.0


def test_product_record_defaults():
    record = _make_record(4, "Y2", "Grainger")
    assert record.identity.category == "General"
    assert record.identity.schema_version == "1.0.0"
    assert record.identity.sku_id == "Grainger-Y2"
    assert record.quality.validity is False
    assert record.quality.overall_confidence == 0.0
    assert record.retrieval.cache_hit is False
    assert record.processing.start_time is not None
    assert record.processing.end_time is None
    assert record.processing.total_time_ms == 0.0
    assert record.errors == []
    assert record.attributes == {}


def test_attribute_confidence_is_bounded():
    with pytest.raises(ValidationError):
        AttributeValue(
            field_name="voltage",
            raw_value="24V",
            confidence=1.5,  # > 1.0 -> rejected
            source=ExtractionSource.REGEX,
        )


def test_enum_wire_values():
    assert RowStatus.EXTRACTING_8B.value == "8B_EXTRACTION"
    assert RowStatus.ESCALATED_70B.value == "70B_PENDING"
    assert RowStatus.EXTRACTING_70B.value == "70B_EXTRACTION"
    assert ExtractionSource.LLM_8B.value == "llm_8b"
    assert ExtractionSource.LLM_70B_FALLBACK.value == "llm_70b_fallback"


def test_extracted_field_null_coercion():
    from app.schemas.product import ExtractedField

    # None, null, empty strings, 'n/a', 'none' -> coerced to None
    field1 = ExtractedField(
        field_name="voltage",
        raw_value="null",
        normalized_value="none",
        unit="n/a",
    )
    assert field1.field_name == "voltage"
    assert field1.raw_value is None
    assert field1.normalized_value is None
    assert field1.unit is None

    # Empty instantiation has all optional fields defaulted to None
    field2 = ExtractedField()
    assert field2.field_name is None
    assert field2.raw_value is None
    assert field2.normalized_value is None
    assert field2.unit is None

    # Non-string coerces to string
    field3 = ExtractedField(field_name="voltage", raw_value=120)
    assert field3.raw_value == "120"