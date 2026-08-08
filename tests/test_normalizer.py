"""Tests for the UniPulse AI normalizer service."""

import pytest

from app.services.normalizer import UnitNormalizer


@pytest.fixture
def normalizer() -> UnitNormalizer:
    return UnitNormalizer()


def test_millimeter_conversion(normalizer: UnitNormalizer) -> None:
    value, unit = normalizer.normalize_field("10mm")
    assert value == "10"
    assert unit == "mm"


def test_fractional_inch_conversion(normalizer: UnitNormalizer) -> None:
    value, unit = normalizer.normalize_field("1/2 inch")
    assert value == "0.5"
    assert unit == "in"


def test_metric_length_to_millimeters(normalizer: UnitNormalizer) -> None:
    value, unit = normalizer.normalize_field("1 m")
    assert value == "1000"
    assert unit == "mm"


def test_imperial_length_to_inches(normalizer: UnitNormalizer) -> None:
    value, unit = normalizer.normalize_field("2 ft")
    assert value == "24"
    assert unit == "in"


def test_vac_unit_extraction(normalizer: UnitNormalizer) -> None:
    value, unit = normalizer.normalize_field("120 VAC")
    assert value == "120"
    assert unit == "V"


def test_vdc_unit_extraction(normalizer: UnitNormalizer) -> None:
    value, unit = normalizer.normalize_field("24 VDC")
    assert value == "24"
    assert unit == "V"


def test_voltage_range_fallback(normalizer: UnitNormalizer) -> None:
    value, unit = normalizer.normalize_field("120/240 V")
    assert value == "120/240"
    assert unit == "V"


def test_voltage_dash_range_preserves_separator(normalizer: UnitNormalizer) -> None:
    value, unit = normalizer.normalize_field("120-277 VAC")
    assert value == "120-277"
    assert unit == "V"


def test_cfm_airflow(normalizer: UnitNormalizer) -> None:
    value, unit = normalizer.normalize_field("800 CFM")
    assert value == "800"
    assert unit == "CFM"


def test_cfm_with_trailing_qualifier_passthrough(normalizer: UnitNormalizer) -> None:
    """Trailing qualifiers are not silently truncated."""
    text = "800 CFM @ 0.5 in. wc"
    value, unit = normalizer.normalize_field(text)
    assert value == text
    assert unit is None


def test_thread_spec_fallback(normalizer: UnitNormalizer) -> None:
    value, unit = normalizer.normalize_field("1/2-14 NPT")
    assert value == "0.5-14 NPT"
    assert unit is None


def test_tonnage_fallback(normalizer: UnitNormalizer) -> None:
    value, unit = normalizer.normalize_field("3.5 tons")
    assert value == "42000"
    assert unit == "BTU/h"


def test_fuzzy_unit_alias(normalizer: UnitNormalizer) -> None:
    value, unit = normalizer.normalize_field("100 millmeters")
    assert value == "100"
    assert unit == "mm"


def test_unparseable_value_passthrough(normalizer: UnitNormalizer) -> None:
    value, unit = normalizer.normalize_field("PN-1234-A")
    assert value == "PN-1234-A"
    assert unit is None


def test_empty_value(normalizer: UnitNormalizer) -> None:
    value, unit = normalizer.normalize_field("   ")
    assert value == ""
    assert unit is None