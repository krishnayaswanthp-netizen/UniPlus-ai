"""
tests/test_deterministic.py
Stage 3 Verification: Tests for regex extraction, coverage calculation, and
short-circuiting.
"""

from app.schemas.product import (
    ExtractionSource,
    ProductIdentity,
    ProductRecord,
    RawInputData,
    RowStatus,
)
from app.services.deterministic import DeterministicEngine


def test_extract_attributes_voltage_and_dimensions() -> None:
    engine = DeterministicEngine()
    desc = "Freud 6 in x 1/8 in Cut-Off Wheel 24V"
    extracted = engine.extract_attributes(desc)

    assert "voltage" in extracted
    assert extracted["voltage"].normalized_value == "24"
    assert extracted["voltage"].unit == "V"
    assert extracted["voltage"].source == ExtractionSource.REGEX
    assert extracted["voltage"].confidence == 0.99

    assert "dimensions" in extracted
    assert extracted["dimensions"].normalized_value == "6 x 1/8"
    assert extracted["dimensions"].unit == "in"


def test_extract_attributes_grit_and_material() -> None:
    engine = DeterministicEngine()
    desc = "3M 775L Stikit Film P120 Cubitron II SST"
    extracted = engine.extract_attributes(desc)

    assert "grit" in extracted
    assert extracted["grit"].normalized_value == "P120"

    assert "material" in extracted
    assert extracted["material"].normalized_value == "Stainless Steel"


def test_calculate_coverage_ratio() -> None:
    engine = DeterministicEngine()
    desc = "3M P120 5 in x 1/8 in Stainless Steel"
    extracted = engine.extract_attributes(desc)

    coverage = engine.calculate_coverage(extracted, category="Abrasives")
    # All 3 required fields (grit, dimensions, material) found!
    assert coverage == 1.0


def test_process_record_short_circuits_high_coverage() -> None:
    engine = DeterministicEngine()
    identity = ProductIdentity(
        row_id=1,
        mfg_part_number="775L",
        manufacturer="3M",
        raw_description="3M P120 5 in x 1/8 in Stainless Steel",
        category="Abrasives",
    )
    record = ProductRecord(
        identity=identity, raw_data=RawInputData(original_row_index=0)
    )

    processed = engine.process_record(record, coverage_threshold=0.8)

    assert processed.status == RowStatus.COMPLETED
    assert processed.quality.coverage_ratio == 1.0
    assert "grit" in processed.attributes


def test_process_record_transitions_to_cache_check_if_coverage_low() -> None:
    engine = DeterministicEngine()
    identity = ProductIdentity(
        row_id=2,
        mfg_part_number="UNKNOWN-ITEM",
        manufacturer="Generic",
        raw_description="Generic Industrial Item",
        category="General",
    )
    record = ProductRecord(
        identity=identity, raw_data=RawInputData(original_row_index=1)
    )

    processed = engine.process_record(record, coverage_threshold=0.8)

    assert processed.status == RowStatus.CACHE_CHECK
    assert processed.quality.coverage_ratio == 0.0


def test_extract_attributes_empty_description() -> None:
    """Blank input short-circuits to an empty attribute map."""
    engine = DeterministicEngine()
    assert engine.extract_attributes("") == {}
    assert engine.extract_attributes(None) == {}


def test_calculate_coverage_partial_general() -> None:
    """General requires 4 fields; 2 present yields 0.5 coverage."""
    engine = DeterministicEngine()
    extracted = engine.extract_attributes("120V 800W Fan")
    assert engine.calculate_coverage(extracted, category="General") == 0.5


def test_material_pvc_keeps_canonical_case() -> None:
    """\"PVC\" stays \"PVC\" (str.capitalize() would produce \"Pvc\")."""
    engine = DeterministicEngine()
    extracted = engine.extract_attributes("PVC Conduit 1 in x 10 ft")
    assert extracted["material"].normalized_value == "PVC"
    assert extracted["dimensions"].normalized_value == "1 x 10"
    assert extracted["dimensions"].unit == "ft"
