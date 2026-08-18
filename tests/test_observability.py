"""
tests/test_observability.py
Stage 11 Verification: Tests for metrics tracking, token accounting, bypass ratio calculation, and judge demo formatting.
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
from app.services.observability import PipelineMetricsTracker


def test_tracker_records_deterministic_completion():
    tracker = PipelineMetricsTracker()
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
    record.status = RowStatus.COMPLETED
    record.processing.total_time_ms = 15.0

    tracker.record_transition(record)
    snap = tracker.get_summary_snapshot()

    assert snap["total_rows"] == 1
    assert snap["completed_rows"] == 1
    assert snap["deterministic_resolved"] == 1
    assert snap["llm_bypass_ratio"] == 1.0


def test_tracker_records_70b_fallback_completion():
    tracker = PipelineMetricsTracker()
    identity = ProductIdentity(
        row_id=2,
        mfg_part_number="X",
        manufacturer="M",
        raw_description="D",
    )
    record = ProductRecord(
        identity=identity, raw_data=RawInputData(original_row_index=0)
    )

    record.attributes["voltage"] = AttributeValue(
        field_name="voltage",
        raw_value="24V",
        confidence=0.95,
        source=ExtractionSource.LLM_70B_FALLBACK,
    )
    record.status = RowStatus.COMPLETED
    record.processing.tokens_consumed = 350

    tracker.record_transition(record)
    snap = tracker.get_summary_snapshot()

    assert snap["completed_rows"] == 1
    assert snap["llm_70b_count"] == 1
    assert snap["total_tokens_consumed"] == 350
    assert snap["llm_bypass_ratio"] == 0.0


def test_format_judge_demo_status():
    tracker = PipelineMetricsTracker()
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
    record.status = RowStatus.COMPLETED

    tracker.record_transition(record)
    demo_str = tracker.format_judge_demo_status()

    assert "Completed: 1" in demo_str
    assert "Regex: 1" in demo_str
    assert "70B Fallbacks: 0" in demo_str


# --- Supplementary coverage for the remaining routing branches -------------


def test_tracker_records_cache_hit_completion():
    """A COMPLETED record resolved via ``retrieval.cache_hit`` counts as a cache hit."""
    tracker = PipelineMetricsTracker()
    identity = ProductIdentity(
        row_id=3, mfg_part_number="Y", manufacturer="M", raw_description="D"
    )
    record = ProductRecord(
        identity=identity, raw_data=RawInputData(original_row_index=0)
    )
    record.status = RowStatus.COMPLETED
    record.retrieval.cache_hit = True

    tracker.record_transition(record)
    snap = tracker.get_summary_snapshot()

    assert snap["cache_hits"] == 1
    assert snap["llm_bypass_ratio"] == 1.0


def test_tracker_records_8b_completion():
    """A COMPLETED record with LLM_8B attributes counts toward the 8B bucket."""
    tracker = PipelineMetricsTracker()
    identity = ProductIdentity(
        row_id=4, mfg_part_number="Z", manufacturer="M", raw_description="D"
    )
    record = ProductRecord(
        identity=identity, raw_data=RawInputData(original_row_index=0)
    )
    record.status = RowStatus.COMPLETED
    record.attributes["voltage"] = AttributeValue(
        field_name="voltage",
        raw_value="24V",
        confidence=0.9,
        source=ExtractionSource.LLM_8B,
    )

    tracker.record_transition(record)
    snap = tracker.get_summary_snapshot()

    assert snap["llm_8b_count"] == 1
    assert snap["llm_bypass_ratio"] == 0.0


def test_tracker_counts_manual_review_and_failed():
    """MANUAL_REVIEW and FAILED records never count as completed."""
    tracker = PipelineMetricsTracker()
    for row_id, status in [
        (5, RowStatus.MANUAL_REVIEW),
        (6, RowStatus.FAILED),
    ]:
        identity = ProductIdentity(
            row_id=row_id,
            mfg_part_number=f"P{row_id}",
            manufacturer="M",
            raw_description="D",
        )
        record = ProductRecord(
            identity=identity, raw_data=RawInputData(original_row_index=0)
        )
        record.status = status
        tracker.record_transition(record)

    snap = tracker.get_summary_snapshot()
    assert snap["manual_review_count"] == 1
    assert snap["failed_count"] == 1
    assert snap["completed_rows"] == 0


def test_tracker_dedupes_total_rows_by_row_id():
    """Re-transitioning the same row_id increments totals once, counters per call."""
    tracker = PipelineMetricsTracker()
    identity = ProductIdentity(
        row_id=1, mfg_part_number="775L", manufacturer="3M", raw_description="3M Disc"
    )
    record = ProductRecord(
        identity=identity, raw_data=RawInputData(original_row_index=0)
    )
    record.status = RowStatus.COMPLETED
    record.attributes["grit"] = AttributeValue(
        field_name="grit",
        raw_value="P120",
        confidence=0.99,
        source=ExtractionSource.REGEX,
    )

    tracker.record_transition(record)
    tracker.record_transition(record)
    snap = tracker.get_summary_snapshot()

    assert snap["total_rows"] == 1  # dedupe guards total_rows only
    assert snap["completed_rows"] == 2
