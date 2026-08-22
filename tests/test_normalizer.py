"""Tests for the UniPulse AI normalizer service."""

import pytest

from app.schemas.product import RowStatus
from app.services.normalizer import InputNormalizer, UnitNormalizer


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


def test_compound_vac_dc_unit_keeps_split(normalizer: UnitNormalizer) -> None:
    """A "VAC/DC" suffix must not be silently truncated to "V" NOR dropped:
    the universal fallback keeps the value/unit split intact."""
    value, unit = normalizer.normalize_field("20-30 VAC/DC")
    assert value == "20-30"
    assert unit == "VAC/DC"


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


def test_mixed_number_fraction(normalizer: UnitNormalizer) -> None:
    value, unit = normalizer.normalize_field("1 1/2 inch")
    assert value == "1.5"
    assert unit == "in"


def test_generic_unit_range(normalizer: UnitNormalizer) -> None:
    value, unit = normalizer.normalize_field("20-30 A")
    assert value == "20-30"
    assert unit == "A"


def test_generic_range_converts_both_endpoints(normalizer: UnitNormalizer) -> None:
    value, unit = normalizer.normalize_field("1-2 m")
    assert value == "1000-2000"
    assert unit == "mm"


def test_word_range_normalizes_to_dash_form(normalizer: UnitNormalizer) -> None:
    """Prose ranges ("37 to 102 deg F") normalize to the canonical dash form."""
    value, unit = normalizer.normalize_field("37 to 102 deg F")
    assert value == "37-102"
    assert unit == "°F"


def test_negative_word_range_normalizes(normalizer: UnitNormalizer) -> None:
    """Negative lower bounds parse in prose ranges ("-40 to 185 deg F")."""
    value, unit = normalizer.normalize_field("-40 to 185 deg F")
    assert value == "-40-185"
    assert unit == "°F"


def test_negative_dash_range_normalizes(normalizer: UnitNormalizer) -> None:
    """Negative lower bounds parse in dash ranges ("-40-185 deg F")."""
    value, unit = normalizer.normalize_field("-40-185 deg F")
    assert value == "-40-185"
    assert unit == "°F"


def test_thousands_separator_removed(normalizer: UnitNormalizer) -> None:
    value, unit = normalizer.normalize_field("1,200 CFM")
    assert value == "1200"
    assert unit == "CFM"


def test_psi_unit_alias(normalizer: UnitNormalizer) -> None:
    value, unit = normalizer.normalize_field("1000 PSI")
    assert value == "1000"
    assert unit == "psi"


def test_bar_unit_alias(normalizer: UnitNormalizer) -> None:
    value, unit = normalizer.normalize_field("2 bar")
    assert value == "2"
    assert unit == "bar"


def test_degf_unit_alias(normalizer: UnitNormalizer) -> None:
    value, unit = normalizer.normalize_field("102 deg F")
    assert value == "102"
    assert unit == "°F"


def test_unicode_minus_normalized(normalizer: UnitNormalizer) -> None:
    value, unit = normalizer.normalize_field("−40 °F")
    assert value == "-40"
    assert unit == "°F"


def test_fraction_still_parses_after_range_rule(normalizer: UnitNormalizer) -> None:
    """Dash-only range regex must not break slash fractions."""
    value, unit = normalizer.normalize_field("1/2 inch")
    assert value == "0.5"
    assert unit == "in"


def test_range_with_unresolvable_unit_keeps_split(normalizer: UnitNormalizer) -> None:
    """The universal fallback keeps the range split even for unrecognized
    units instead of discarding it ("20-30 XYZ" -> "20-30", "XYZ")."""
    value, unit = normalizer.normalize_field("20-30 XYZ")
    assert value == "20-30"
    assert unit == "XYZ"


def test_european_decimal_comma_not_misread_as_thousands(
    normalizer: UnitNormalizer,
) -> None:
    """The thousands-separator rule must not corrupt European decimal commas."""
    value, unit = normalizer.normalize_field("1,5 mm")
    assert value == "1,5 mm"
    assert unit is None


def test_multi_group_thousands_separator(normalizer: UnitNormalizer) -> None:
    value, unit = normalizer.normalize_field("1,234,567 CFM")
    assert value == "1234567"
    assert unit == "CFM"


def test_area_square_inches(normalizer: UnitNormalizer) -> None:
    """Split an area spec into its numeric value and canonical unit."""
    value, unit = normalizer.normalize_field("5.4 sq in")
    assert value == "5.4"
    assert unit == "sq in"


def test_area_square_feet(normalizer: UnitNormalizer) -> None:
    value, unit = normalizer.normalize_field("12 sq ft")
    assert value == "12"
    assert unit == "sq ft"


def test_area_square_meters(normalizer: UnitNormalizer) -> None:
    value, unit = normalizer.normalize_field("10 sq m")
    assert value == "10"
    assert unit == "sq m"


def test_area_square_centimeters(normalizer: UnitNormalizer) -> None:
    value, unit = normalizer.normalize_field("250 sq cm")
    assert value == "250"
    assert unit == "sq cm"


def test_area_long_form_spelling(normalizer: UnitNormalizer) -> None:
    """Long-form "square feet" maps to the canonical "sq ft" symbol."""
    value, unit = normalizer.normalize_field("2.5 square feet")
    assert value == "2.5"
    assert unit == "sq ft"


def test_area_case_insensitive(normalizer: UnitNormalizer) -> None:
    """Unit tokens are matched case-insensitively (IGNORECASE + lower())."""
    value, unit = normalizer.normalize_field("5.4 SQ IN")
    assert value == "5.4"
    assert unit == "sq in"


def test_area_does_not_misread_plain_length(normalizer: UnitNormalizer) -> None:
    """A plain length ("10 m") must not be treated as an area — it still
    converts to millimeters through the generic length path."""
    value, unit = normalizer.normalize_field("10 m")
    assert value == "10000"
    assert unit == "mm"


# ---------------------------------------------------------------------------
# Slash ranges across all units (non-voltage)
# ---------------------------------------------------------------------------


def test_slash_range_across_units(normalizer: UnitNormalizer) -> None:
    """"10/16 mm" is a size range, not a fraction — normalized lo-hi."""
    value, unit = normalizer.normalize_field("10/16 mm")
    assert value == "10-16"
    assert unit == "mm"


def test_slash_range_non_length_unit(normalizer: UnitNormalizer) -> None:
    """Slash ranges work for non-length units too ("20/30 A")."""
    value, unit = normalizer.normalize_field("20/30 A")
    assert value == "20-30"
    assert unit == "A"


def test_slash_range_converts_both_endpoints(normalizer: UnitNormalizer) -> None:
    """Slash-range endpoints convert like dash-range endpoints ("1/2 m")."""
    value, unit = normalizer.normalize_field("1/2 m")
    assert value == "1000-2000"
    assert unit == "mm"


def test_inch_fraction_not_misread_as_slash_range(
    normalizer: UnitNormalizer,
) -> None:
    """Inch/foot fractions keep their fraction semantics ("3/4 in")."""
    value, unit = normalizer.normalize_field("3/4 in")
    assert value == "0.75"
    assert unit == "in"


# ---------------------------------------------------------------------------
# Expanded alias registry: kVA, degC, percent
# ---------------------------------------------------------------------------


def test_kva_alias(normalizer: UnitNormalizer) -> None:
    value, unit = normalizer.normalize_field("120 kVA")
    assert value == "120"
    assert unit == "kVA"


def test_kva_range(normalizer: UnitNormalizer) -> None:
    value, unit = normalizer.normalize_field("50-100 kVA")
    assert value == "50-100"
    assert unit == "kVA"


def test_degc_alias(normalizer: UnitNormalizer) -> None:
    value, unit = normalizer.normalize_field("25 deg C")
    assert value == "25"
    assert unit == "°C"


def test_degc_symbol_alias(normalizer: UnitNormalizer) -> None:
    value, unit = normalizer.normalize_field("25 °C")
    assert value == "25"
    assert unit == "°C"


def test_degc_range(normalizer: UnitNormalizer) -> None:
    value, unit = normalizer.normalize_field("20-30 deg C")
    assert value == "20-30"
    assert unit == "°C"


def test_percent_alias(normalizer: UnitNormalizer) -> None:
    value, unit = normalizer.normalize_field("40%")
    assert value == "40"
    assert unit == "%"


def test_percent_range(normalizer: UnitNormalizer) -> None:
    value, unit = normalizer.normalize_field("20-30 %")
    assert value == "20-30"
    assert unit == "%"


# ---------------------------------------------------------------------------
# Field-aware angular degrees ("rotation: 90 deg" must not become °F)
# ---------------------------------------------------------------------------


def test_angular_degree_field_preserved(normalizer: UnitNormalizer) -> None:
    """Rotational fields keep a bare "deg" as angular degrees, not °F."""
    value, unit = normalizer.normalize_field("90 deg", field_name="rotation_angle")
    assert value == "90"
    assert unit == "deg"


def test_angular_degree_range_preserved(normalizer: UnitNormalizer) -> None:
    """Angular ranges keep degrees too ("90-180 deg" rotation)."""
    value, unit = normalizer.normalize_field("90-180 deg", field_name="rotation")
    assert value == "90-180"
    assert unit == "deg"


def test_angular_degree_other_field_terms(normalizer: UnitNormalizer) -> None:
    """"angle"/"stroke"/"position" fields behave like rotation fields."""
    for field_name in ("mounting_angle", "valve_stroke", "switch_position"):
        value, unit = normalizer.normalize_field("45 deg", field_name=field_name)
        assert value == "45"
        assert unit == "deg"


def test_non_angular_deg_keeps_fahrenheit_mapping(
    normalizer: UnitNormalizer,
) -> None:
    """Without a spatial field name, bare "deg" keeps its fuzzy °F mapping."""
    value, unit = normalizer.normalize_field("90 deg")
    assert value == "90"
    assert unit == "°F"


# ---------------------------------------------------------------------------
# Generalized grammar-first normalization: fractions, ranges, novel units
# ---------------------------------------------------------------------------


def test_fraction_with_unknown_unit_stays_fraction(normalizer: UnitNormalizer) -> None:
    """A proper fraction with an unrecognized unit reads as a fraction, not
    a 1-3 range ("1/3 HP" -> "0.333 HP")."""
    value, unit = normalizer.normalize_field("1/3 HP")
    assert value == "0.333"
    assert unit == "HP"


def test_scalar_with_unknown_unit_fallback(normalizer: UnitNormalizer) -> None:
    """Unrecognized single-token units fall back to the value/unit split
    instead of being discarded ("1075 RPM")."""
    value, unit = normalizer.normalize_field("1075 RPM")
    assert value == "1075"
    assert unit == "RPM"


def test_inch_fraction_with_npt_qualifier(normalizer: UnitNormalizer) -> None:
    """Noise qualifiers (NPT) are stripped during canonical resolution
    ("3/4 in NPT" -> "0.75 in")."""
    value, unit = normalizer.normalize_field("3/4 in NPT")
    assert value == "0.75"
    assert unit == "in"


def test_compound_unknown_unit_fallback(normalizer: UnitNormalizer) -> None:
    """Compound unknown units survive the fallback ("150 lb-in")."""
    value, unit = normalizer.normalize_field("150 lb-in")
    assert value == "150"
    assert unit == "lb-in"


def test_kiloampere_unknown_unit_fallback(normalizer: UnitNormalizer) -> None:
    """Multiplier prefixes on unrecognized units are preserved ("10 kA")."""
    value, unit = normalizer.normalize_field("10 kA")
    assert value == "10"
    assert unit == "kA"


def test_lux_unknown_unit_fallback(normalizer: UnitNormalizer) -> None:
    """An unseen unit (lux) passes through the value/unit split."""
    value, unit = normalizer.normalize_field("1500 lux")
    assert value == "1500"
    assert unit == "lux"


def test_centistokes_unknown_unit_fallback(normalizer: UnitNormalizer) -> None:
    """An unseen compound unit (cSt) passes through the value/unit split."""
    value, unit = normalizer.normalize_field("25 cSt")
    assert value == "25"
    assert unit == "cSt"


def test_bare_word_range_normalizes_to_dash(normalizer: UnitNormalizer) -> None:
    """A bare word range ("20 to 25") normalizes to the hyphenated form."""
    value, unit = normalizer.normalize_field("20 to 25")
    assert value == "20-25"
    assert unit is None


def test_bare_dash_range_normalizes(normalizer: UnitNormalizer) -> None:
    """A bare dash range ("1-10") keeps the hyphenated form with no unit."""
    value, unit = normalizer.normalize_field("1-10")
    assert value == "1-10"
    assert unit is None


def test_din_qualifier_stripped(normalizer: UnitNormalizer) -> None:
    """DIN is stripped like other noise qualifiers ("10 mm DIN")."""
    value, unit = normalizer.normalize_field("10 mm DIN")
    assert value == "10"
    assert unit == "mm"


def test_mount_qualifier_stripped(normalizer: UnitNormalizer) -> None:
    """Mount is stripped from area suffixes ("5.4 sq in mount")."""
    value, unit = normalizer.normalize_field("5.4 sq in mount")
    assert value == "5.4"
    assert unit == "sq in"


def test_bare_fraction_converts_to_decimal(normalizer: UnitNormalizer) -> None:
    """A fraction with no unit suffix still converts to its numeric form
    ("3/4" -> "0.75")."""
    value, unit = normalizer.normalize_field("3/4")
    assert value == "0.75"
    assert unit is None


def test_terminating_fraction_preserves_precision(normalizer: UnitNormalizer) -> None:
    """Terminating fractions keep full precision ("1/16 inch" -> "0.0625 in"
    rather than rounding to "0.062")."""
    value, unit = normalizer.normalize_field("1/16 inch")
    assert value == "0.0625"
    assert unit == "in"


def test_negative_fraction_normalizes(normalizer: UnitNormalizer) -> None:
    """Signed fractions normalize like positive ones ("-1/3" -> "-0.333")."""
    value, unit = normalizer.normalize_field("-1/3")
    assert value == "-0.333"
    assert unit is None


# ---------------------------------------------------------------------------
# Stage 2: InputNormalizer — header alias mapping & row normalization
# ---------------------------------------------------------------------------


def test_clean_manufacturer_name_strips_erp_codes() -> None:
    normalizer = InputNormalizer()
    assert normalizer.clean_manufacturer_name("3M Inc (2435)") == "3M Inc"
    assert (
        normalizer.clean_manufacturer_name("Mirka Abrasives (MIRUS)")
        == "Mirka Abrasives"
    )
    assert normalizer.clean_manufacturer_name("Freud Inc") == "Freud Inc"


def test_clean_placeholder_filters_unbranded_strings() -> None:
    normalizer = InputNormalizer()
    assert normalizer.clean_placeholder("-- Unbranded --") is None
    assert normalizer.clean_placeholder("-- No Unilog Brand --") is None
    assert normalizer.clean_placeholder("3M") == "3M"
    assert normalizer.clean_placeholder("   ") is None


def test_map_header_aliases_official_hackathon_headers() -> None:
    normalizer = InputNormalizer()
    raw_row = {
        "Part_Manuf": "Freud Inc (9928)",
        "Mfg_Part_Num": "DCB518ASTS06G",
        "Part_Desc": "6 in x 1/8 in Cut-Off Wheel 24V",
        "Unilog_Brand": "-- No Unilog Brand --",
    }
    canonical = normalizer.map_header_aliases(raw_row)

    assert canonical["manufacturer"] == "Freud Inc"
    assert canonical["mfg_part_number"] == "DCB518ASTS06G"
    assert canonical["raw_description"] == "6 in x 1/8 in Cut-Off Wheel 24V"
    assert canonical["category"] == "General"


def test_normalize_row_produces_valid_product_record() -> None:
    normalizer = InputNormalizer()
    raw_row = {
        "Vendor": "Omron (JPN)",
        "SKU": "MY4N-DC24",
        "Description": "General Purpose Relay 24VDC",
        "Dept": "Electrical",
    }

    record = normalizer.normalize_row(
        raw_row, row_id=1, original_index=0, file_source="catalog.csv"
    )

    assert record.identity.row_id == 1
    assert record.identity.manufacturer == "Omron"
    assert record.identity.mfg_part_number == "MY4N-DC24"
    assert record.identity.sku_id == "Omron-MY4N-DC24"
    assert record.identity.category == "Electrical"
    assert record.status == RowStatus.ROW_READY


def test_clean_manufacturer_name_null_and_placeholder_fallback() -> None:
    """Null and placeholder manufacturer inputs fall back to "Unknown"."""
    normalizer = InputNormalizer()
    assert normalizer.clean_manufacturer_name(None) == "Unknown"
    assert normalizer.clean_manufacturer_name("-- Unbranded --") == "Unknown"


def test_missing_part_number_falls_back_to_unknown() -> None:
    """A row with no part-number column yields the "UNKNOWN" fallback."""
    normalizer = InputNormalizer()
    canonical = normalizer.map_header_aliases(
        {"Vendor": "3M Inc", "Description": "Stikit Tape"}
    )
    assert canonical["manufacturer"] == "3M Inc"
    assert canonical["mfg_part_number"] == "UNKNOWN"
    assert canonical["raw_description"] == "Stikit Tape"
    assert canonical["category"] == "General"


def test_direct_unit_and_normalized_value_preserved(normalizer: UnitNormalizer) -> None:
    """When the LLM extractor provides clean normalized_value and unit, they are preserved."""
    val, unit = normalizer.normalize_field(
        raw_value="37 to 102 °F (2.8 to 38.9 °C)",
        unit="°F",
        normalized_value="37 to 102",
    )
    assert val == "37 to 102"
    assert unit == "°F"


def test_direct_unit_canonical_standardization(normalizer: UnitNormalizer) -> None:
    """When unit is provided, canonical symbols are standardized (e.g. VAC -> V)."""
    val, unit = normalizer.normalize_field(
        raw_value="120 VAC 60Hz",
        unit="VAC",
        normalized_value="120",
    )
    assert val == "120"
    assert unit == "V"