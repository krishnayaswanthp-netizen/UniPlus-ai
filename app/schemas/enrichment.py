"""Enrichment job/result models.

Defines the request/response contract of the product-enrichment pipeline,
plus the attribute-level data model produced by the extraction and
normalization services.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

#: Product categories supported by the enrichment engine.
Category = Literal["HVAC", "Plumbing", "Electrical", "General"]


class IndustrialAttribute(BaseModel):
    """A single extracted and normalized product attribute."""

    field_name: str
    raw_value: str
    normalized_value: str
    unit: str | None = None
    confidence_score: float = Field(default=0.0, ge=0.0, le=1.0)
    source_url: str


class ProductEnrichmentRequest(BaseModel):
    """Input payload describing a product to enrich."""

    manufacturer_name: str
    part_number: str
    raw_description: str | None = None
    category: Category


class ProductEnrichmentResponse(BaseModel):
    """Output payload produced by the enrichment pipeline."""

    sku_id: str
    category: str
    enriched_attributes: list[IndustrialAttribute] = Field(default_factory=list)
    overall_confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    processing_time_ms: float = Field(default=0.0, ge=0.0)
    estimated_cost_usd: float = Field(default=0.0, ge=0.0)


class BatchItemResult(BaseModel):
    """Outcome of enriching a single row inside a batch upload."""

    sku_id: str
    manufacturer_name: str
    part_number: str
    category: str
    status: Literal["success", "error"]
    error: str | None = None
    enriched_attributes: list[IndustrialAttribute] = Field(default_factory=list)
    overall_confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    processing_time_ms: float = Field(default=0.0, ge=0.0)


class BatchEnrichmentResponse(BaseModel):
    """Summary of an asynchronous batch enrichment run."""

    total: int = Field(ge=0)
    succeeded: int = Field(ge=0)
    failed: int = Field(ge=0)
    results: list[BatchItemResult] = Field(default_factory=list)
