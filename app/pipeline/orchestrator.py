"""
app/pipeline/orchestrator.py
Stage 13: End-to-End Async Pipeline Orchestrator for UniPulse AI.

Wires all 12 upstream services into a unified, resumable, concurrent async
state machine that transforms raw catalog rows into enriched, audited
Excel/JSON exports.
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional

from app.db.checkpoint_store import CheckpointStore
from app.schemas.product import ProductRecord, RowStatus
from app.services.context_reducer import ContextReducer
from app.services.deterministic import DeterministicEngine
from app.services.exporter import CatalogExporter
from app.services.llm_70b import LLM70BFallbackExtractor
from app.services.llm_8b import LLM8BExtractor
from app.services.normalizer import InputNormalizer
from app.services.observability import PipelineMetricsTracker
from app.services.provenance import ProvenanceMerger
from app.services.rate_limiter import AdaptiveRateLimiter
from app.services.validator import ValidationEngine


class UniPulsePipeline:
    """
    End-to-End Orchestrator uniting all 12 pipeline stages into a resumable,
    fault-tolerant, rate-limited, multi-tiered catalog enrichment engine.
    """

    def __init__(
        self,
        db_path: str = "unipulse_checkpoint.db",
        api_keys: Optional[List[str]] = None,
    ) -> None:
        self.normalizer = InputNormalizer()
        self.deterministic = DeterministicEngine()
        self.checkpoint_store = CheckpointStore(db_path=db_path)
        self.context_reducer = ContextReducer()
        self.rate_limiter = AdaptiveRateLimiter()
        self.llm_8b = LLM8BExtractor(
            api_keys=api_keys, rate_limiter=self.rate_limiter
        )
        self.validator = ValidationEngine()
        self.llm_70b = LLM70BFallbackExtractor(
            api_keys=api_keys,
            rate_limiter=self.rate_limiter,
            validator=self.validator,
        )
        self.provenance_merger = ProvenanceMerger()
        self.metrics_tracker = PipelineMetricsTracker()
        self.exporter = CatalogExporter()

    async def process_single_record(
        self,
        record: ProductRecord,
        scraped_text: str = "",
        source_url: Optional[str] = None,
        client_override: Optional[Any] = None,
    ) -> ProductRecord:
        """
        Executes the 13-stage state machine for a single ProductRecord.
        """
        # --- Stage 3: Deterministic Engine ---
        record = self.deterministic.process_record(record)
        if record.status == RowStatus.COMPLETED:
            self.checkpoint_store.save_checkpoint(record)
            self.metrics_tracker.record_transition(record)
            return record

        # --- Stage 4: Enrichment Cache Check ---
        cached_attrs = self.checkpoint_store.get_enrichment_cache(record)
        if cached_attrs:
            record.attributes.update(cached_attrs)
            record.retrieval.cache_hit = True
            record.mark_completed()
            self.checkpoint_store.save_checkpoint(record)
            self.metrics_tracker.record_transition(record)
            return record

        # --- Stage 5: Context Reducer & Scrape Cache ---
        if scraped_text or source_url:
            record = self.context_reducer.process_retrieval(
                record,
                scraped_text,
                source_url=source_url,
                checkpoint_store=self.checkpoint_store,
            )

        # --- Stage 7: Primary 8B LLM Extractor ---
        record = await self.llm_8b.process_record(
            record, client_override=client_override
        )

        # --- Stage 8: Tri-Signal Validation Engine ---
        record = self.validator.evaluate_tri_signal(record)

        # --- Stage 9: 70B Fallback Extractor (Only if Escalated) ---
        if record.status == RowStatus.ESCALATED_70B:
            record = await self.llm_70b.process_record(
                record, client_override=client_override
            )

        # --- Stage 10: Provenance Merger ---
        record = self.provenance_merger.merge_provenance(record)

        # --- Persistence & Caching ---
        self.checkpoint_store.save_checkpoint(record)
        if record.attributes:
            self.checkpoint_store.save_enrichment_cache(record)

        # --- Stage 11: Observability Metrics ---
        self.metrics_tracker.record_transition(record)

        return record

    async def process_batch(
        self,
        raw_rows: List[Dict[str, Any]],
        scraped_texts: Optional[Dict[int, str]] = None,
        client_override: Optional[Any] = None,
    ) -> List[ProductRecord]:
        """
        Normalizes, dedupes against checkpoints, and processes a batch of raw catalog rows.
        """
        scraped_texts = scraped_texts or {}
        completed_ids = self.checkpoint_store.get_completed_row_ids()

        records_to_process: List[ProductRecord] = []
        restored_records: List[ProductRecord] = []

        for idx, row in enumerate(raw_rows):
            row_id = idx + 1
            if row_id in completed_ids:
                restored = self.checkpoint_store.get_checkpoint(row_id)
                if restored:
                    restored_records.append(restored)
                    self.metrics_tracker.record_transition(restored)
                    continue

            record = self.normalizer.normalize_row(
                row, row_id=row_id, original_index=idx
            )
            records_to_process.append(record)

        # Process new records concurrently
        tasks = [
            self.process_single_record(
                rec,
                scraped_text=scraped_texts.get(rec.identity.row_id, ""),
                client_override=client_override,
            )
            for rec in records_to_process
        ]

        newly_processed = await asyncio.gather(*tasks) if tasks else []

        # Return combined list ordered by row_id
        all_records = restored_records + list(newly_processed)
        all_records.sort(key=lambda r: r.identity.row_id)
        return all_records

    def export_results(
        self,
        records: List[ProductRecord],
        excel_path: str,
        json_path: Optional[str] = None,
    ) -> Dict[str, str]:
        """
        Triggers Stage 12 multi-tab Excel and optional JSON exports.
        """
        out_paths: Dict[str, str] = {}
        snapshot = self.metrics_tracker.get_summary_snapshot()

        out_paths["excel"] = self.exporter.export_to_excel(
            records, snapshot, excel_path
        )
        if json_path:
            out_paths["json"] = self.exporter.export_to_json(records, json_path)

        return out_paths
