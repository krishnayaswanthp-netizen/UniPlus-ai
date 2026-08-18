"""
app/schemas/product.py
Stage 1: Single Canonical Record Schema for UniPulse AI Pipeline.

Defines the canonical ``ProductRecord`` domain model and its supporting data
contracts. This is the single record shape that travels through every stage of
the 13-stage pipeline — deterministic checks, cache, retrieval, 8B/70B
extraction, validation, provenance merge and completion — so attributes,
provenance, quality and processing telemetry always move together with the row.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, field_validator


def _utcnow() -> datetime:
    """Return the current UTC time as a naive ``datetime``.

    ``datetime.utcnow()`` is deprecated since Python 3.12; this builds the
    equivalent naive-UTC value via ``timezone.utc`` so model defaults and
    helper methods stay warning-free on the project's 3.12 runtime.
    """
    return datetime.now(timezone.utc).replace(tzinfo=None)


class RowStatus(str, Enum):
    """Lifecycle status of a product row as it flows through the pipeline."""

    ROW_READY = "ROW_READY"
    DETERMINISTIC_CHECK = "DETERMINISTIC_CHECK"
    CACHE_CHECK = "CACHE_CHECK"
    RETRIEVAL = "RETRIEVAL"
    LLM_PENDING = "LLM_PENDING"
    EXTRACTING_8B = "8B_EXTRACTION"
    VALIDATING_8B = "VALIDATING_8B"
    ESCALATED_70B = "70B_PENDING"
    EXTRACTING_70B = "70B_EXTRACTION"
    VALIDATING_70B = "VALIDATING_70B"
    PROVENANCE_MERGE = "PROVENANCE_MERGE"
    COMPLETED = "COMPLETED"
    MANUAL_REVIEW = "MANUAL_REVIEW"
    FAILED = "FAILED"


class ExtractionSource(str, Enum):
    """Where an attribute's value came from."""

    REGEX = "regex"
    ENRICHMENT_CACHE = "enrichment_cache"
    SCRAPE_CACHE = "scrape_cache"
    LLM_8B = "llm_8b"
    LLM_70B_FALLBACK = "llm_70b_fallback"
    USER_PROVIDED = "user_provided"


class ProductIdentity(BaseModel):
    """Stable identity of the product row (independent of extraction results)."""

    row_id: int
    mfg_part_number: str
    manufacturer: str
    raw_description: str = ""
    category: str = "General"
    schema_version: str = "1.0.0"

    @property
    def sku_id(self) -> str:
        clean_mfg = self.manufacturer.strip().replace(" ", "_")
        clean_part = self.mfg_part_number.strip().replace(" ", "_")
        return f"{clean_mfg}-{clean_part}"


class RawInputData(BaseModel):
    """Original row context as it arrived in the upload."""

    raw_headers: dict[str, str] = Field(default_factory=dict)
    original_row_index: int
    file_source: str | None = None


class ExtractedField(BaseModel):
    """A single attribute returned by LLM extraction."""

    field_name: str | None = None
    raw_value: str | None = None
    normalized_value: str | None = None
    unit: str | None = None
    confidence: float = Field(default=0.8, ge=0.0, le=1.0)

    @field_validator(
        "field_name", "raw_value", "normalized_value", "unit", mode="before"
    )
    @classmethod
    def coerce_nulls(cls, v: Any) -> str | None:
        """Coerce 'null', 'none', 'n/a' or empty strings into None."""
        if v is None:
            return None
        if not isinstance(v, str):
            v = str(v)
        s = v.strip()
        if s.lower() in ("", "null", "none", "n/a", "undefined"):
            return None
        return s


class AttributeValue(BaseModel):
    """A single extracted attribute with its provenance and confidence."""

    field_name: str | None = None
    raw_value: str | None = None
    normalized_value: str | None = None
    unit: str | None = None
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    source: ExtractionSource
    evidence_snippet: str | None = None
    source_url: str | None = None

    @field_validator(
        "field_name", "raw_value", "normalized_value", "unit", mode="before"
    )
    @classmethod
    def coerce_nulls(cls, v: Any) -> str | None:
        """Coerce 'null', 'none', 'n/a' or empty strings into None."""
        if v is None:
            return None
        if not isinstance(v, str):
            v = str(v)
        s = v.strip()
        if s.lower() in ("", "null", "none", "n/a", "undefined"):
            return None
        return s


class QualityScore(BaseModel):
    """Quality/validity assessment of the extracted record."""

    validity: bool = False
    completeness: float = Field(default=0.0, ge=0.0, le=1.0)
    overall_confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    coverage_ratio: float = Field(default=0.0, ge=0.0, le=1.0)
    validation_flags: list[str] = Field(default_factory=list)


class RetrievalContext(BaseModel):
    """Telemetry about the web-retrieval step for this row."""

    search_queries_used: list[str] = Field(default_factory=list)
    selected_source_url: str | None = None
    raw_scraped_char_count: int = 0
    reduced_context_char_count: int = 0
    reduced_text_snippet: str | None = None
    cache_hit: bool = False


class ProcessingMetrics(BaseModel):
    """Timing / token telemetry for the row's pipeline run."""

    start_time: datetime = Field(default_factory=_utcnow)
    end_time: datetime | None = None
    total_time_ms: float = 0.0
    deterministic_time_ms: float = 0.0
    retrieval_time_ms: float = 0.0
    llm_8b_time_ms: float = 0.0
    llm_70b_time_ms: float = 0.0
    tokens_consumed: int = 0
    models_invoked: list[str] = Field(default_factory=list)


class PipelineError(BaseModel):
    """An error recorded at a specific pipeline stage."""

    stage: RowStatus
    error_type: str
    error_message: str
    timestamp: datetime = Field(default_factory=_utcnow)


class ProductRecord(BaseModel):
    """The single canonical record passed across all 13 pipeline stages."""

    identity: ProductIdentity
    raw_data: RawInputData
    attributes: dict[str, AttributeValue] = Field(default_factory=dict)
    quality: QualityScore = Field(default_factory=QualityScore)
    retrieval: RetrievalContext = Field(default_factory=RetrievalContext)
    processing: ProcessingMetrics = Field(default_factory=ProcessingMetrics)
    status: RowStatus = RowStatus.ROW_READY
    enrichment_source: str = "llm_8b"
    errors: list[PipelineError] = Field(default_factory=list)

    def mark_completed(self) -> None:
        self.status = RowStatus.COMPLETED
        self.processing.end_time = _utcnow()
        if self.processing.start_time and self.processing.end_time:
            delta = self.processing.end_time - self.processing.start_time
            self.processing.total_time_ms = delta.total_seconds() * 1000.0

    def record_error(self, stage: RowStatus, exc: Exception) -> None:
        self.errors.append(
            PipelineError(
                stage=stage,
                error_type=exc.__class__.__name__,
                error_message=str(exc),
            )
        )
