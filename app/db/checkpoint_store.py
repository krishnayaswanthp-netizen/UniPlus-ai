"""
app/db/checkpoint_store.py
Stage 4: SQLite Checkpoint Store & Dual Caching Layer for UniPulse AI.

Persistence layer built on the stdlib ``sqlite3`` module (no extra
dependency; blocking calls run off the event loop via ``asyncio.to_thread``
exactly like the rest of the pipeline):

1. **Checkpoint store** — row-level pipeline state, so a batch run is
   resumable: every processed row is upserted with its status, coverage and
   full ``ProductRecord`` JSON.
2. **Enrichment cache** — hash-keyed extracted attributes keyed on the
   product identity + ``schema_version``; a cache hit returns the attributes
   with zero LLM tokens consumed.
3. **Scrape cache** — URL-hash-keyed cleaned webpage text, so re-processing
   the same catalog does not re-scrape the web.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import sqlite3
from contextlib import contextmanager
from typing import Iterator

from app.schemas.product import AttributeValue, ProductRecord, RowStatus

_DEFAULT_DB_PATH = str(Path(__file__).resolve().parent.parent.parent / "unipulse_checkpoint.db")


class CheckpointStore:
    """SQLite persistence store for job checkpointing, versioned enrichment
    caching, and URL scrape caching.
    """

    def __init__(self, db_path: str = _DEFAULT_DB_PATH) -> None:
        if not db_path or db_path == "unipulse_checkpoint.db":
            db_path = _DEFAULT_DB_PATH
        self.db_path = os.path.abspath(db_path)
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=15.0)
        conn.row_factory = sqlite3.Row
        return conn

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        """Open a connection that commits on success and always closes.

        ``sqlite3``'s ``with conn`` context manager only commits/rolls back —
        it never closes the connection, so the spec's
        ``with self._get_connection()`` pattern would leak an open file
        handle per operation over long batch runs. Every method routes
        through this instead.
        """
        conn = self._get_connection()
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _init_db(self) -> None:
        """Create the checkpoint, enrichment-cache and scrape-cache tables."""
        with self._connection() as conn:
            cursor = conn.cursor()
            cursor.execute("PRAGMA journal_mode = WAL")
            cursor.execute("PRAGMA synchronous = NORMAL")
            cursor.execute("PRAGMA busy_timeout = 10000")

            # 1. Checkpoint Table
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS checkpoints (
                    row_id INTEGER PRIMARY KEY,
                    sku_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    coverage_ratio REAL DEFAULT 0.0,
                    record_json TEXT NOT NULL,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            # Resumable batch runs call get_completed_row_ids() once per run;
            # index the status filter so the lookup stays O(log n).
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_checkpoints_status "
                "ON checkpoints (status)"
            )

            # 2. Enrichment Cache Table
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS enrichment_cache (
                    cache_key TEXT PRIMARY KEY,
                    sku_id TEXT NOT NULL,
                    attributes_json TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
                """
            )

            # 3. Scrape Cache Table
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS scrape_cache (
                    url_hash TEXT PRIMARY KEY,
                    url TEXT NOT NULL,
                    cleaned_text TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
                """
            )

    # --- CHECKPOINT OPERATIONS ---

    def save_checkpoint(self, record: ProductRecord) -> None:
        """Persist/update a ``ProductRecord``'s pipeline state in SQLite."""
        record_json = record.model_dump_json()
        with self._connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO checkpoints (row_id, sku_id, status, coverage_ratio, record_json, updated_at)
                VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(row_id) DO UPDATE SET
                    sku_id=excluded.sku_id,
                    status=excluded.status,
                    coverage_ratio=excluded.coverage_ratio,
                    record_json=excluded.record_json,
                    updated_at=CURRENT_TIMESTAMP
                """,
                (
                    record.identity.row_id,
                    record.identity.sku_id,
                    record.status.value,
                    record.quality.coverage_ratio,
                    record_json,
                ),
            )

    def get_checkpoint(self, row_id: int) -> ProductRecord | None:
        """Retrieve a saved ``ProductRecord`` checkpoint by *row_id*."""
        with self._connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT record_json FROM checkpoints WHERE row_id = ?",
                (row_id,),
            )
            row = cursor.fetchone()
            if row:
                return ProductRecord.model_validate_json(row["record_json"])
        return None

    def get_completed_row_ids(self) -> set[int]:
        """Return the set of ``row_id``\\ s that reached ``COMPLETED`` status."""
        with self._connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT row_id FROM checkpoints WHERE status = ?",
                (RowStatus.COMPLETED.value,),
            )
            rows = cursor.fetchall()
            return {row["row_id"] for row in rows}

    # --- ENRICHMENT CACHE OPERATIONS ---

    @staticmethod
    def generate_enrichment_cache_key(record: ProductRecord) -> str:
        """Generate the versioned MD5 hash key for enrichment caching.

        Keyed on manufacturer + part number + raw description + schema
        version, so a bumped schema version automatically invalidates old
        cache entries.
        """
        mfg = (record.identity.manufacturer or "").strip().lower()
        pn = (record.identity.mfg_part_number or "").strip().lower()
        desc = (record.identity.raw_description or "").strip().lower()
        sv = (record.identity.schema_version or "1.0.0").strip().lower()
        raw_str = f"{mfg}|{pn}|{desc}|{sv}"
        return hashlib.md5(raw_str.encode("utf-8")).hexdigest()

    def save_enrichment_cache(self, record: ProductRecord) -> None:
        """Cache extracted attributes under the record's enrichment key."""
        if not record.attributes:
            return

        cache_key = self.generate_enrichment_cache_key(record)
        attributes_dict = {
            k: v.model_dump() for k, v in record.attributes.items()
        }
        attributes_json = json.dumps(attributes_dict)

        with self._connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO enrichment_cache (cache_key, sku_id, attributes_json)
                VALUES (?, ?, ?)
                ON CONFLICT(cache_key) DO UPDATE SET
                    attributes_json=excluded.attributes_json
                """,
                (cache_key, record.identity.sku_id, attributes_json),
            )

    def get_enrichment_cache(
        self, record: ProductRecord
    ) -> dict[str, AttributeValue] | None:
        """Retrieve cached attributes for *record* on an enrichment hit."""
        cache_key = self.generate_enrichment_cache_key(record)
        with self._connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT attributes_json FROM enrichment_cache WHERE cache_key = ?",
                (cache_key,),
            )
            row = cursor.fetchone()
            if row:
                raw_dict = json.loads(row["attributes_json"])
                return {
                    k: AttributeValue.model_validate(v)
                    for k, v in raw_dict.items()
                }
        return None

    # --- SCRAPE CACHE OPERATIONS ---

    def save_scrape_cache(self, url: str, cleaned_text: str) -> None:
        """Cache cleaned webpage text by URL hash."""
        url_hash = hashlib.md5(url.strip().encode("utf-8")).hexdigest()
        with self._connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO scrape_cache (url_hash, url, cleaned_text)
                VALUES (?, ?, ?)
                ON CONFLICT(url_hash) DO UPDATE SET
                    cleaned_text=excluded.cleaned_text
                """,
                (url_hash, url, cleaned_text),
            )

    def get_scrape_cache(self, url: str) -> str | None:
        """Retrieve cached webpage text for a given URL."""
        url_hash = hashlib.md5(url.strip().encode("utf-8")).hexdigest()
        with self._connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT cleaned_text FROM scrape_cache WHERE url_hash = ?",
                (url_hash,),
            )
            row = cursor.fetchone()
            if row:
                return row["cleaned_text"]
        return None