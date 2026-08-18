"""
app/services/provenance.py
Stage 10: Provenance Merger & Lineage Audit Engine for UniPulse AI.

Consolidates attribute extractions from every upstream stage (Regex, Enrichment
Cache, 8B LLM, 70B Fallback) into auditable lineage records: field-level source
priority conflict resolution, final quality-score consolidation, and a flat
JSON/Excel-ready export with explicit per-field value, confidence, and source
columns.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from app.schemas.product import (
    AttributeValue,
    ExtractionSource,
    ProductRecord,
    RowStatus,
)


class ProvenanceMerger:
    """
    Provenance Merger engine consolidating field extractions from Regex, Cache,
    8B LLM, and 70B Fallback into auditable lineage records.
    """

    # Source priority hierarchy (higher index = higher priority)
    SOURCE_PRIORITY: Dict[ExtractionSource, int] = {
        ExtractionSource.USER_PROVIDED: 5,
        ExtractionSource.REGEX: 4,
        ExtractionSource.ENRICHMENT_CACHE: 3,
        ExtractionSource.LLM_70B_FALLBACK: 2,
        ExtractionSource.LLM_8B: 1,
        ExtractionSource.SCRAPE_CACHE: 0,
    }

    @classmethod
    def resolve_attribute_conflict(
        cls, existing: AttributeValue, incoming: AttributeValue
    ) -> AttributeValue:
        """
        Resolves conflicts between two extractions for the same field name.
        Priority: Higher source priority wins. On equal priority, higher confidence wins.
        """
        existing_priority = cls.SOURCE_PRIORITY.get(existing.source, 0)
        incoming_priority = cls.SOURCE_PRIORITY.get(incoming.source, 0)

        if incoming_priority > existing_priority:
            return incoming
        elif incoming_priority < existing_priority:
            return existing
        else:
            # Same source priority: higher confidence wins
            if incoming.confidence > existing.confidence:
                return incoming
            return existing

    def merge_provenance(self, record: ProductRecord) -> ProductRecord:
        """
        Consolidates attribute lineage, updates final quality scores, and transitions
        record.status to COMPLETED (or preserves MANUAL_REVIEW).
        """
        original_status = record.status
        record.status = RowStatus.PROVENANCE_MERGE

        # Audit and clean attribute lineage
        consolidated: Dict[str, AttributeValue] = {}
        for field_name, attr in record.attributes.items():
            if field_name not in consolidated:
                consolidated[field_name] = attr
            else:
                consolidated[field_name] = self.resolve_attribute_conflict(
                    consolidated[field_name], attr
                )

        record.attributes = consolidated

        # Update final metrics
        record.mark_completed()

        # Preserve MANUAL_REVIEW if flagged by validator/70B stage
        if original_status == RowStatus.MANUAL_REVIEW:
            record.status = RowStatus.MANUAL_REVIEW

        return record

    @classmethod
    def build_provenance_export_dict(cls, record: ProductRecord) -> Dict[str, Any]:
        """
        Exports a flat, audit-ready dictionary suitable for multi-column Excel/JSON export.
        Includes explicit field-level value, confidence, and source columns.
        """
        export_dict: Dict[str, Any] = {
            "row_id": record.identity.row_id,
            "sku_id": record.identity.sku_id,
            "manufacturer": record.identity.manufacturer,
            "mfg_part_number": record.identity.mfg_part_number,
            "category": record.identity.category,
            "status": record.status.value,
            "validity": record.quality.validity,
            "completeness": record.quality.completeness,
            "overall_confidence": record.quality.overall_confidence,
            "total_time_ms": record.processing.total_time_ms,
        }

        # Add explicit field-level lineage columns
        for field_name, attr in record.attributes.items():
            val = (
                getattr(attr, "normalized_value", None)
                or getattr(attr, "raw_value", None)
                or (attr[2] if isinstance(attr, (tuple, list)) and len(attr) > 2 else (attr[1] if isinstance(attr, (tuple, list)) and len(attr) > 1 else str(attr)))
            )
            export_dict[f"attr_{field_name}_value"] = val
            export_dict[f"attr_{field_name}_confidence"] = getattr(attr, "confidence", 0.9)
            source_obj = getattr(attr, "source", None)
            export_dict[f"attr_{field_name}_source"] = getattr(source_obj, "value", str(source_obj or "LLM_8B"))
            if getattr(attr, "evidence_snippet", None):
                export_dict[f"attr_{field_name}_evidence"] = attr.evidence_snippet

        return export_dict
