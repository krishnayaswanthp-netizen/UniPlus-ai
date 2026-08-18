"""
tests/test_checkpoint_store.py
Stage 4 Verification: Tests for SQLite checkpoint store, enrichment cache,
and scrape cache.
"""

from pathlib import Path

import pytest

from app.db.checkpoint_store import CheckpointStore
from app.schemas.product import (
    AttributeValue,
    ExtractionSource,
    ProductIdentity,
    ProductRecord,
    RawInputData,
    RowStatus,
)


@pytest.fixture
def temp_store(tmp_path: Path) -> CheckpointStore:
    db_file = tmp_path / "test_checkpoint.db"
    return CheckpointStore(db_path=str(db_file))


def test_checkpoint_save_and_restore(temp_store: CheckpointStore) -> None:
    identity = ProductIdentity(
        row_id=1,
        mfg_part_number="775L",
        manufacturer="3M",
        raw_description="3M P120 5 in",
    )
    record = ProductRecord(
        identity=identity, raw_data=RawInputData(original_row_index=0)
    )
    record.status = RowStatus.COMPLETED
    record.quality.coverage_ratio = 1.0

    temp_store.save_checkpoint(record)
    restored = temp_store.get_checkpoint(1)

    assert restored is not None
    assert restored.identity.row_id == 1
    assert restored.status == RowStatus.COMPLETED
    assert restored.quality.coverage_ratio == 1.0


def test_get_completed_row_ids(temp_store: CheckpointStore) -> None:
    rec1 = ProductRecord(
        identity=ProductIdentity(
            row_id=1, mfg_part_number="A", manufacturer="M", raw_description="D"
        ),
        raw_data=RawInputData(original_row_index=0),
    )
    rec1.status = RowStatus.COMPLETED

    rec2 = ProductRecord(
        identity=ProductIdentity(
            row_id=2, mfg_part_number="B", manufacturer="M", raw_description="D"
        ),
        raw_data=RawInputData(original_row_index=1),
    )
    rec2.status = RowStatus.CACHE_CHECK

    temp_store.save_checkpoint(rec1)
    temp_store.save_checkpoint(rec2)

    completed_ids = temp_store.get_completed_row_ids()
    assert completed_ids == {1}


def test_enrichment_cache_save_and_get(temp_store: CheckpointStore) -> None:
    identity = ProductIdentity(
        row_id=1,
        mfg_part_number="775L",
        manufacturer="3M",
        raw_description="3M P120 5 in",
    )
    record = ProductRecord(
        identity=identity, raw_data=RawInputData(original_row_index=0)
    )
    record.attributes["grit"] = AttributeValue(
        field_name="grit",
        raw_value="P120",
        normalized_value="P120",
        confidence=0.99,
        source=ExtractionSource.REGEX,
    )

    temp_store.save_enrichment_cache(record)
    cached_attrs = temp_store.get_enrichment_cache(record)

    assert cached_attrs is not None
    assert "grit" in cached_attrs
    assert cached_attrs["grit"].normalized_value == "P120"
    assert cached_attrs["grit"].source == ExtractionSource.REGEX


def test_scrape_cache_save_and_get(temp_store: CheckpointStore) -> None:
    url = "https://example.com/spec-sheet"
    text = "3M 775L Disc 5 in P120 24V"

    temp_store.save_scrape_cache(url, text)
    cached_text = temp_store.get_scrape_cache(url)

    assert cached_text == text


def test_checkpoint_upsert_overwrites_previous_state(
    temp_store: CheckpointStore,
) -> None:
    """Saving the same row_id twice keeps only the latest state."""
    rec1 = ProductRecord(
        identity=ProductIdentity(
            row_id=7, mfg_part_number="X", manufacturer="M", raw_description="D"
        ),
        raw_data=RawInputData(original_row_index=0),
    )
    rec1.status = RowStatus.CACHE_CHECK
    temp_store.save_checkpoint(rec1)

    rec2 = ProductRecord(
        identity=ProductIdentity(
            row_id=7, mfg_part_number="X", manufacturer="M", raw_description="D"
        ),
        raw_data=RawInputData(original_row_index=0),
    )
    rec2.status = RowStatus.COMPLETED
    rec2.quality.coverage_ratio = 1.0
    temp_store.save_checkpoint(rec2)

    restored = temp_store.get_checkpoint(7)
    assert restored is not None
    assert restored.status == RowStatus.COMPLETED
    assert restored.quality.coverage_ratio == 1.0


def test_enrichment_cache_miss_returns_none(temp_store: CheckpointStore) -> None:
    identity = ProductIdentity(
        row_id=8,
        mfg_part_number="NOPE",
        manufacturer="None",
        raw_description="No specs here",
    )
    record = ProductRecord(
        identity=identity, raw_data=RawInputData(original_row_index=0)
    )
    assert temp_store.get_enrichment_cache(record) is None


def test_scrape_cache_miss_returns_none(temp_store: CheckpointStore) -> None:
    assert temp_store.get_scrape_cache("https://example.com/missing") is None


def test_enrichment_cache_key_is_versioned_and_deterministic() -> None:
    """Identical identities share a key; a schema-version bump changes it."""
    identity = ProductIdentity(
        row_id=1,
        mfg_part_number="775L",
        manufacturer="3M",
        raw_description="3M P120 5 in",
    )
    record = ProductRecord(
        identity=identity, raw_data=RawInputData(original_row_index=0)
    )
    key_v1 = CheckpointStore.generate_enrichment_cache_key(record)

    # Same identity (different row_id) -> same cache key.
    twin = ProductRecord(
        identity=identity.model_copy(update={"row_id": 99}),
        raw_data=RawInputData(original_row_index=0),
    )
    assert CheckpointStore.generate_enrichment_cache_key(twin) == key_v1

    # Bumped schema version -> different cache key (old entries invalidated).
    upgraded = ProductRecord(
        identity=identity.model_copy(update={"schema_version": "1.1.0"}),
        raw_data=RawInputData(original_row_index=0),
    )
    assert CheckpointStore.generate_enrichment_cache_key(upgraded) != key_v1
