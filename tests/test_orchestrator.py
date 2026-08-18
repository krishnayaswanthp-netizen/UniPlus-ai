"""
tests/test_orchestrator.py
Stage 13 Verification: Tests for end-to-end pipeline execution, checkpoint resumption, and multi-tab export.
"""

import os
import pytest
from app.schemas.product import RowStatus, ExtractionSource
from app.pipeline.orchestrator import UniPulsePipeline
from tests.test_llm_8b import MockGroqClient


@pytest.fixture
def temp_pipeline(tmp_path):
    db_file = str(tmp_path / "test_pipeline_orchestrator.db")
    return UniPulsePipeline(db_path=db_file)


@pytest.mark.asyncio
async def test_end_to_end_single_record_deterministic_short_circuit(temp_pipeline):
    raw_row = {
        "Part_Manuf": "3M",
        "Mfg_Part_Num": "775L",
        "Part_Desc": "3M P120 5 in x 1/8 in Stainless Steel",
        "Category": "Abrasives",
    }
    record = temp_pipeline.normalizer.normalize_row(
        raw_row, row_id=1, original_index=0
    )

    processed = await temp_pipeline.process_single_record(record)

    assert processed.status == RowStatus.COMPLETED
    assert processed.quality.coverage_ratio == 1.0
    assert "grit" in processed.attributes
    assert processed.attributes["grit"].source == ExtractionSource.REGEX


@pytest.mark.asyncio
async def test_end_to_end_batch_processing_with_mock_llm(temp_pipeline):
    raw_rows = [
        {
            "Part_Manuf": "3M",
            "Mfg_Part_Num": "775L",
            "Part_Desc": "3M P120 5 in x 1/8 in Stainless Steel",
            "Category": "Abrasives",
        },
        {
            "Part_Manuf": "Omron",
            "Mfg_Part_Num": "MY4N",
            "Part_Desc": "General Relay 24V",
            "Category": "Electrical",
        },
    ]

    mock_client = MockGroqClient()
    records = await temp_pipeline.process_batch(raw_rows, client_override=mock_client)

    assert len(records) == 2
    assert records[0].identity.row_id == 1
    assert records[1].identity.row_id == 2
    assert records[0].status == RowStatus.COMPLETED
    # Row 2 (Electrical, 1/3 completeness) escalates to the 70B mock, fails
    # re-validation, and lands in MANUAL_REVIEW.
    assert records[1].status == RowStatus.MANUAL_REVIEW

    snapshot = temp_pipeline.metrics_tracker.get_summary_snapshot()
    assert snapshot["completed_rows"] >= 1
    assert snapshot["manual_review_count"] >= 1


@pytest.mark.asyncio
async def test_batch_resumption_skips_completed_rows(tmp_path):
    db_file = str(tmp_path / "test_resumption.db")
    pipeline1 = UniPulsePipeline(db_path=db_file)

    raw_rows = [
        {
            "Part_Manuf": "3M",
            "Mfg_Part_Num": "775L",
            "Part_Desc": "3M P120 5 in x 1/8 in Stainless Steel",
            "Category": "Abrasives",
        }
    ]

    # Run 1: completes and checkpoints row 1
    records1 = await pipeline1.process_batch(raw_rows)
    assert records1[0].status == RowStatus.COMPLETED

    # Run 2: new pipeline instance pointing to same DB skips row 1
    pipeline2 = UniPulsePipeline(db_path=db_file)
    records2 = await pipeline2.process_batch(raw_rows)

    assert len(records2) == 1
    assert records2[0].identity.row_id == 1
    assert records2[0].status == RowStatus.COMPLETED


@pytest.mark.asyncio
async def test_export_results_creates_excel_and_json(temp_pipeline, tmp_path):
    raw_rows = [
        {
            "Part_Manuf": "3M",
            "Mfg_Part_Num": "775L",
            "Part_Desc": "3M P120 5 in x 1/8 in Stainless Steel",
            "Category": "Abrasives",
        }
    ]
    records = await temp_pipeline.process_batch(raw_rows)

    excel_out = str(tmp_path / "final_catalog.xlsx")
    json_out = str(tmp_path / "final_catalog.json")

    paths = temp_pipeline.export_results(records, excel_out, json_path=json_out)

    assert os.path.exists(paths["excel"])
    assert os.path.exists(paths["json"])
