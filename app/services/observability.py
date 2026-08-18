"""
app/services/observability.py
Stage 11: Real-time Pipeline Observability & Hackathon Telemetry Tracker for UniPulse AI.

Tracks live execution metrics — state-transition counts, token consumption,
average latency, and LLM-bypass ratio — across all pipeline workers, producing
structured telemetry snapshots for hackathon judges and dashboard displays.
"""

from __future__ import annotations

import time
from typing import Any, Dict, List

from app.schemas.product import ExtractionSource, ProductRecord, RowStatus


class PipelineMetricsTracker:
    """
    Real-time metrics aggregator tracking pipeline state transitions, token velocity,
    cost savings, and execution latency across all processing workers.
    """

    def __init__(self) -> None:
        self.start_time: float = time.time()
        self.total_rows: int = 0
        self.completed_rows: int = 0
        self.deterministic_resolved: int = 0
        self.cache_hits: int = 0
        self.llm_8b_count: int = 0
        self.llm_70b_count: int = 0
        self.manual_review_count: int = 0
        self.failed_count: int = 0

        self.total_tokens_consumed: int = 0
        self.total_processing_time_ms: float = 0.0
        self.observed_records: List[int] = []

    def record_transition(self, record: ProductRecord) -> None:
        """Updates internal counters based on a ProductRecord's current state and attributes."""
        if record.identity.row_id not in self.observed_records:
            self.observed_records.append(record.identity.row_id)
            self.total_rows += 1

        # Track tokens & timing
        self.total_tokens_consumed += record.processing.tokens_consumed
        self.total_processing_time_ms += record.processing.total_time_ms

        # Route by final status
        if record.status == RowStatus.COMPLETED:
            self.completed_rows += 1
            # Classify primary resolution path
            sources = {attr.source for attr in record.attributes.values()}
            if ExtractionSource.LLM_70B_FALLBACK in sources:
                self.llm_70b_count += 1
            elif ExtractionSource.LLM_8B in sources:
                self.llm_8b_count += 1
            elif (
                ExtractionSource.ENRICHMENT_CACHE in sources
                or record.retrieval.cache_hit
            ):
                self.cache_hits += 1
            elif all(s == ExtractionSource.REGEX for s in sources) and len(sources) > 0:
                self.deterministic_resolved += 1
            else:
                self.deterministic_resolved += 1

        elif record.status == RowStatus.MANUAL_REVIEW:
            self.manual_review_count += 1
        elif record.status == RowStatus.FAILED:
            self.failed_count += 1

    def get_summary_snapshot(self) -> Dict[str, Any]:
        """Calculates and returns a real-time telemetry snapshot."""
        elapsed_seconds = max(0.001, time.time() - self.start_time)
        rows_per_second = round(self.completed_rows / elapsed_seconds, 2)
        avg_latency_ms = round(
            self.total_processing_time_ms / max(1, self.completed_rows), 2
        )

        # Estimate LLM bypass ratio
        llm_bypassed = self.deterministic_resolved + self.cache_hits
        bypass_ratio = (
            round(llm_bypassed / max(1, self.completed_rows), 4)
            if self.completed_rows > 0
            else 0.0
        )

        return {
            "total_rows": self.total_rows,
            "completed_rows": self.completed_rows,
            "deterministic_resolved": self.deterministic_resolved,
            "cache_hits": self.cache_hits,
            "llm_8b_count": self.llm_8b_count,
            "llm_70b_count": self.llm_70b_count,
            "manual_review_count": self.manual_review_count,
            "failed_count": self.failed_count,
            "total_tokens_consumed": self.total_tokens_consumed,
            "llm_bypass_ratio": bypass_ratio,
            "avg_latency_ms": avg_latency_ms,
            "rows_per_second": rows_per_second,
            "elapsed_seconds": round(elapsed_seconds, 2),
        }

    def format_judge_demo_status(self) -> str:
        """Formats a clean 1-line status display string for live hackathon demos."""
        snap = self.get_summary_snapshot()
        return (
            f"Completed: {snap['completed_rows']:,} | "
            f"Regex: {snap['deterministic_resolved']:,} | "
            f"Cache Hits: {snap['cache_hits']:,} | "
            f"8B Extracted: {snap['llm_8b_count']:,} | "
            f"70B Fallbacks: {snap['llm_70b_count']:,} | "
            f"Manual Review: {snap['manual_review_count']:,}"
        )
