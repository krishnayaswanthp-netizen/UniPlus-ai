"""
tests/test_provenance.py
Stage 10 Verification: Tests for attribute conflict resolution, provenance priority, and export formatting.
"""

import pytest
from app.schemas.product import (
    ProductRecord,
    ProductIdentity,
    RawInputData,
    RowStatus,
    AttributeValue,
    ExtractionSource,
)
from app.services.provenance import ProvenanceMerger


def test_resolve_attribute_conflict_regex_beats_llm_8b():
    merger = ProvenanceMerger()
    llm_attr = AttributeValue(
        field_name="voltage",
        raw_value="24V",
        confidence=0.9,
        source=ExtractionSource.LLM_8B,
    )
    regex_attr = AttributeValue(
        field_name="voltage",
        raw_value="24V",
        confidence=0.99,
        source=ExtractionSource.REGEX,
    )

    resolved = merger.resolve_attribute_conflict(llm_attr, regex_attr)
    assert resolved.source == ExtractionSource.REGEX
    assert resolved.confidence == 0.99


def test_resolve_attribute_conflict_higher_confidence_wins_on_same_source():
    merger = ProvenanceMerger()
    attr_low = AttributeValue(
        field_name="material",
        raw_value="Steel",
        confidence=0.6,
        source=ExtractionSource.LLM_8B,
    )
    attr_high = AttributeValue(
        field_name="material",
        raw_value="Stainless Steel",
        confidence=0.85,
        source=ExtractionSource.LLM_8B,
    )

    resolved = merger.resolve_attribute_conflict(attr_low, attr_high)
    assert resolved.raw_value == "Stainless Steel"
    assert resolved.confidence == 0.85


def test_merge_provenance_completes_record():
    merger = ProvenanceMerger()
    identity = ProductIdentity(
        row_id=1,
        mfg_part_number="775L",
        manufacturer="3M",
        raw_description="3M Disc",
    )
    record = ProductRecord(
        identity=identity, raw_data=RawInputData(original_row_index=0)
    )
    record.attributes["grit"] = AttributeValue(
        field_name="grit",
        raw_value="P120",
        confidence=0.99,
        source=ExtractionSource.REGEX,
    )

    merged = merger.merge_provenance(record)
    assert merged.status == RowStatus.COMPLETED
    assert merged.processing.end_time is not None


def test_merge_provenance_preserves_manual_review_status():
    merger = ProvenanceMerger()
    identity = ProductIdentity(
        row_id=2,
        mfg_part_number="X",
        manufacturer="M",
        raw_description="D",
    )
    record = ProductRecord(
        identity=identity, raw_data=RawInputData(original_row_index=0)
    )
    record.status = RowStatus.MANUAL_REVIEW

    merged = merger.merge_provenance(record)
    assert merged.status == RowStatus.MANUAL_REVIEW


def test_build_provenance_export_dict_contains_explicit_columns():
    merger = ProvenanceMerger()
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
    record.attributes["voltage"] = AttributeValue(
        field_name="voltage",
        raw_value="24V",
        normalized_value="24",
        unit="V",
        confidence=0.99,
        source=ExtractionSource.REGEX,
    )

    export_data = merger.build_provenance_export_dict(record)

    assert export_data["row_id"] == 1
    assert export_data["sku_id"] == "3M-775L"
    assert export_data["attr_voltage_value"] == "24"
    assert export_data["attr_voltage_confidence"] == 0.99
    assert export_data["attr_voltage_source"] == "regex"
