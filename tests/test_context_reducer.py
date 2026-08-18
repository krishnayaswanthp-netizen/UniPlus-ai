"""
tests/test_context_reducer.py
Stage 5 Verification: Tests for HTML cleaning, sentence spec scoring, context
reduction, and cache integration.
"""

from pathlib import Path

from app.db.checkpoint_store import CheckpointStore
from app.schemas.product import ProductIdentity, ProductRecord, RawInputData, RowStatus
from app.services.context_reducer import ContextReducer


def test_clean_raw_text_strips_html_and_scripts() -> None:
    reducer = ContextReducer()
    raw_html = (
        "<html><head><script>var x = 10;</script></head>"
        "<body><h1>3M Disc</h1><p>Voltage: 24V</p></body></html>"
    )
    cleaned = reducer.clean_raw_text(raw_html)

    assert "<script>" not in cleaned
    assert "3M Disc" in cleaned
    assert "Voltage: 24V" in cleaned


def test_clean_raw_text_unescapes_entities() -> None:
    reducer = ContextReducer()
    cleaned = reducer.clean_raw_text("<p>3M &amp; Co &nbsp; 775L &lt;NEW&gt;</p>")
    assert "3M & Co" in cleaned
    assert "775L" in cleaned
    assert "<NEW>" in cleaned


def test_score_sentence_spec_density() -> None:
    reducer = ContextReducer()
    spec_sentence = "Operating Voltage: 120 V, Current: 15 A, Material: Stainless Steel."
    boilerplate = "Copyright 2026 All Rights Reserved. Add to cart now."

    spec_score = reducer.score_sentence(spec_sentence, mfg="3M", part_num="775L")
    boiler_score = reducer.score_sentence(boilerplate)

    assert spec_score > 5.0
    assert boiler_score < 0.0


def test_reduce_context_truncates_large_text_retaining_specs() -> None:
    reducer = ContextReducer()
    large_text = (
        "Home > Products > Abrasives > Shopping Cart (0 items)\n"
        "3M 775L Stikit Film Disc P120 is designed for high performance.\n"
        "Specifications: Voltage: 24V DC, Diameter: 5 in, Material: Stainless Steel.\n"
        "Free shipping on orders over $50. Customer service hours are 9am - 5pm.\n"
    ) * 10

    reduced = reducer.reduce_context(
        large_text, max_chars=300, mfg="3M", part_num="775L"
    )

    assert len(reduced) <= 300
    assert "Voltage: 24V" in reduced
    assert "Copyright" not in reduced


def test_reduce_context_keeps_identity_sentence() -> None:
    """A line-based page whose boilerplate line ends in ')' must not merge
    with the identity line — the part-number sentence survives reduction."""
    reducer = ContextReducer()
    # Two blocks (~455 chars) so the input exceeds max_chars and reduction
    # actually runs (a single block is <= 300 and short-circuits intact).
    block = (
        "Home > Products > Abrasives > Shopping Cart (0 items)\n"
        "3M 775L Stikit Film Disc P120 is designed for high performance.\n"
        "Specifications: Voltage: 24V DC, Diameter: 5 in, Material: Stainless Steel.\n"
        "Free shipping on orders over $50.\n"
    )
    large_text = block * 2

    reduced = reducer.reduce_context(
        large_text, max_chars=300, mfg="3M", part_num="775L"
    )

    assert "3M 775L" in reduced
    assert "Voltage: 24V" in reduced
    assert "Shopping Cart" not in reduced


def test_reduce_context_empty_input() -> None:
    reducer = ContextReducer()
    assert reducer.reduce_context("") == ""
    assert reducer.reduce_context("   \n \n ") == ""


def test_process_retrieval_with_checkpoint_cache(tmp_path: Path) -> None:
    db_file = tmp_path / "test_reducer_checkpoint.db"
    store = CheckpointStore(db_path=str(db_file))
    reducer = ContextReducer()

    identity = ProductIdentity(
        row_id=1,
        mfg_part_number="775L",
        manufacturer="3M",
        raw_description="3M 775L Disc",
    )
    record = ProductRecord(
        identity=identity, raw_data=RawInputData(original_row_index=0)
    )

    url = "https://example.com/3m-775l"
    scraped_html = (
        "<html><body><h1>3M 775L Disc</h1>"
        "<p>Voltage: 24V, Diameter: 5 in</p></body></html>"
    )

    # 1. First run: populates cache & reduces context
    processed = reducer.process_retrieval(
        record, scraped_html, source_url=url, checkpoint_store=store
    )
    assert processed.status == RowStatus.LLM_PENDING
    assert processed.retrieval.cache_hit is False
    assert "Voltage: 24V" in processed.retrieval.reduced_text_snippet

    # 2. Second run: triggers cache hit
    record2 = ProductRecord(
        identity=identity, raw_data=RawInputData(original_row_index=0)
    )
    processed2 = reducer.process_retrieval(
        record2, scraped_html, source_url=url, checkpoint_store=store
    )
    assert processed2.status == RowStatus.LLM_PENDING
    assert processed2.retrieval.cache_hit is True


def test_process_retrieval_without_store() -> None:
    """Without a checkpoint store there is no caching, but reduction still runs."""
    reducer = ContextReducer()
    identity = ProductIdentity(
        row_id=2,
        mfg_part_number="775L",
        manufacturer="3M",
        raw_description="3M 775L Disc",
    )
    record = ProductRecord(
        identity=identity, raw_data=RawInputData(original_row_index=0)
    )

    processed = reducer.process_retrieval(
        record,
        "<p>Voltage: 24V, Diameter: 5 in</p>",
        source_url="https://example.com/3m-775l",
    )

    assert processed.status == RowStatus.LLM_PENDING
    assert processed.retrieval.cache_hit is False
    assert processed.retrieval.reduced_text_snippet == "Voltage: 24V, Diameter: 5 in"
    assert processed.retrieval.selected_source_url == "https://example.com/3m-775l"
