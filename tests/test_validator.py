"""
tests/test_validator.py
Stage 8 Verification: Tests for business rule validation, tri-signal
scoring, and 70B escalation routing.
"""

from app.schemas.product import (
    AttributeValue,
    ExtractionSource,
    ProductIdentity,
    ProductRecord,
    RawInputData,
    RowStatus,
)
from app.services.validator import ValidationEngine


def test_validate_business_rules_catches_negative_voltage() -> None:
    engine = ValidationEngine()
    attrs = {
        "voltage": AttributeValue(
            field_name="voltage",
            raw_value="-20V",
            normalized_value="-20",
            confidence=0.9,
            source=ExtractionSource.LLM_8B,
        )
    }
    is_valid, flags = engine.validate_business_rules(attrs)

    assert is_valid is False
    assert any("INVALID_VOLTAGE_NEGATIVE" in flag for flag in flags)


def test_validate_business_rules_flags_invalid_grit_format() -> None:
    engine = ValidationEngine()
    attrs = {
        "grit": AttributeValue(
            field_name="grit",
            raw_value="G120",
            normalized_value="G120",
            confidence=0.9,
            source=ExtractionSource.LLM_8B,
        )
    }
    is_valid, flags = engine.validate_business_rules(attrs)

    assert is_valid is False
    assert any("INVALID_GRIT_FORMAT" in flag for flag in flags)


def test_validate_business_rules_flags_non_numeric_voltage() -> None:
    """A voltage value with no numeric component is a format failure."""
    engine = ValidationEngine()
    attrs = {
        "voltage": AttributeValue(
            field_name="voltage",
            raw_value="AC only",
            normalized_value="AC only",
            confidence=0.9,
            source=ExtractionSource.LLM_8B,
        )
    }
    is_valid, flags = engine.validate_business_rules(attrs)

    assert is_valid is False
    assert any("INVALID_VOLTAGE_FORMAT" in flag for flag in flags)


def test_evaluate_tri_signal_passes_clean_record() -> None:
    engine = ValidationEngine()
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

    record.attributes = {
        "grit": AttributeValue(
            field_name="grit",
            raw_value="P120",
            normalized_value="P120",
            confidence=0.9,
            source=ExtractionSource.LLM_8B,
        ),
        "dimensions": AttributeValue(
            field_name="dimensions",
            raw_value="5 in",
            normalized_value="5",
            unit="in",
            confidence=0.95,
            source=ExtractionSource.LLM_8B,
        ),
        "material": AttributeValue(
            field_name="material",
            raw_value="Stainless Steel",
            normalized_value="Stainless Steel",
            confidence=0.85,
            source=ExtractionSource.LLM_8B,
        ),
    }

    processed = engine.evaluate_tri_signal(
        record, confidence_threshold=0.8, completeness_threshold=0.75
    )

    assert processed.status == RowStatus.PROVENANCE_MERGE
    assert processed.quality.validity is True
    assert processed.quality.completeness == 1.0
    assert processed.quality.overall_confidence >= 0.85


def test_evaluate_tri_signal_escalates_to_70b_on_low_confidence() -> None:
    engine = ValidationEngine()
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

    record.attributes = {
        "voltage": AttributeValue(
            field_name="voltage",
            raw_value="24V",
            normalized_value="24",
            confidence=0.5,
            source=ExtractionSource.LLM_8B,
        ),
        "power": AttributeValue(
            field_name="power",
            raw_value="100W",
            normalized_value="100",
            confidence=0.4,
            source=ExtractionSource.LLM_8B,
        ),
        "frequency": AttributeValue(
            field_name="frequency",
            raw_value="60Hz",
            normalized_value="60",
            confidence=0.5,
            source=ExtractionSource.LLM_8B,
        ),
    }

    processed = engine.evaluate_tri_signal(
        record, confidence_threshold=0.8, completeness_threshold=0.75
    )

    assert processed.status == RowStatus.ESCALATED_70B
    assert processed.quality.overall_confidence < 0.8


def test_evaluate_tri_signal_escalates_on_incomplete_fields() -> None:
    """High confidence alone cannot pass an incomplete category record."""
    engine = ValidationEngine()
    identity = ProductIdentity(
        row_id=3,
        mfg_part_number="Y",
        manufacturer="M",
        raw_description="D",
        category="Abrasives",
    )
    record = ProductRecord(
        identity=identity, raw_data=RawInputData(original_row_index=0)
    )
    record.attributes = {
        "grit": AttributeValue(
            field_name="grit",
            raw_value="P120",
            normalized_value="P120",
            confidence=0.95,
            source=ExtractionSource.LLM_8B,
        ),
    }

    processed = engine.evaluate_tri_signal(record)

    assert processed.status == RowStatus.ESCALATED_70B
    assert processed.quality.completeness == round(1 / 3, 2)  # 0.33


def test_calculate_overall_confidence_empty() -> None:
    engine = ValidationEngine()
    assert engine.calculate_overall_confidence({}) == 0.0
