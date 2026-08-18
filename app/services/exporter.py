"""
app/services/exporter.py
Stage 12: Multi-Tab Excel & JSON Export Engine for UniPulse AI.

Exports enriched ``ProductRecord`` objects to structured JSON files and to
multi-tab ``openpyxl`` workbooks covering the Enriched Catalog, Provenance
Lineage Audit, Judge Telemetry, and Manual Review Queue.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill

from app.schemas.product import ProductRecord, RowStatus
from app.services.provenance import ProvenanceMerger


class CatalogExporter:
    """
    Multi-tab Excel and JSON export engine producing enterprise delivery formats,
    provenance lineage audit trails, judge observability sheets, and manual review queues.
    """

    @classmethod
    def export_to_json(cls, records: List[ProductRecord], file_path: str) -> str:
        """Exports a list of ProductRecords to a formatted JSON file."""
        data = []
        for rec in records:
            dumped = json.loads(rec.model_dump_json())
            # ``sku_id`` is a model *property* (not a serialized field), so it
            # never appears in ``model_dump_json`` output — surface it
            # explicitly so the exported identity carries the SKU lineage.
            dumped["identity"]["sku_id"] = rec.identity.sku_id
            data.append(dumped)
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        return file_path

    @classmethod
    def export_to_excel(
        cls,
        records: List[ProductRecord],
        metrics_snapshot: Dict[str, Any],
        output_path: str,
    ) -> str:
        """
        Exports ProductRecords and telemetry snapshot into a 4-tab openpyxl Excel workbook:
        1. Enriched Catalog
        2. Lineage Audit
        3. Judge Telemetry
        4. Manual Review Queue
        """
        wb = Workbook()
        # Remove default sheet
        default_sheet = wb.active
        if default_sheet:
            wb.remove(default_sheet)

        # Styling
        header_font = Font(name="Calibri", size=11, bold=True, color="FFFFFF")
        header_fill = PatternFill(
            start_color="1F4E78", end_color="1F4E78", fill_type="solid"
        )
        review_fill = PatternFill(
            start_color="C00000", end_color="C00000", fill_type="solid"
        )

        # Collect all dynamic attribute names across records
        all_attr_names: List[str] = sorted(
            {field for rec in records for field in rec.attributes.keys()}
        )

        # -------------------------------------------------------------------
        # TAB 1: Enriched Catalog
        # -------------------------------------------------------------------
        ws_catalog = wb.create_sheet(title="Enriched Catalog")
        cat_headers = [
            "Row ID",
            "SKU ID",
            "Manufacturer",
            "Part Number",
            "Category",
            "Status",
            "Validity",
            "Completeness",
            "Confidence",
        ] + [f"Attr: {attr.capitalize()}" for attr in all_attr_names]

        ws_catalog.append(cat_headers)
        for cell in ws_catalog[1]:
            cell.font = header_font
            cell.fill = header_fill
            cell.alignment = Alignment(horizontal="center")

        for rec in records:
            row_data = [
                rec.identity.row_id,
                rec.identity.sku_id,
                rec.identity.manufacturer,
                rec.identity.mfg_part_number,
                rec.identity.category,
                rec.status.value,
                rec.quality.validity,
                rec.quality.completeness,
                rec.quality.overall_confidence,
            ]
            for attr_name in all_attr_names:
                attr = rec.attributes.get(attr_name)
                row_data.append(
                    attr.normalized_value or attr.raw_value if attr else ""
                )
            ws_catalog.append(row_data)

        # -------------------------------------------------------------------
        # TAB 2: Lineage Audit
        # -------------------------------------------------------------------
        ws_audit = wb.create_sheet(title="Lineage Audit")
        export_dicts = [
            ProvenanceMerger.build_provenance_export_dict(rec) for rec in records
        ]

        audit_headers: List[str] = []
        if export_dicts:
            # Gather all unique keys across export dicts
            audit_headers = list(export_dicts[0].keys())
            for d in export_dicts[1:]:
                for k in d.keys():
                    if k not in audit_headers:
                        audit_headers.append(k)

        ws_audit.append(audit_headers)
        for cell in ws_audit[1]:
            cell.font = header_font
            cell.fill = header_fill

        for d in export_dicts:
            ws_audit.append([d.get(k, "") for k in audit_headers])

        # -------------------------------------------------------------------
        # TAB 3: Judge Telemetry
        # -------------------------------------------------------------------
        ws_telemetry = wb.create_sheet(title="Judge Telemetry")
        ws_telemetry.append(["Metric / Telemetry Indicator", "Observed Value"])
        for cell in ws_telemetry[1]:
            cell.font = header_font
            cell.fill = header_fill

        for k, v in metrics_snapshot.items():
            ws_telemetry.append([k.replace("_", " ").title(), str(v)])

        # -------------------------------------------------------------------
        # TAB 4: Manual Review Queue
        # -------------------------------------------------------------------
        ws_review = wb.create_sheet(title="Manual Review Queue")
        review_headers = [
            "Row ID",
            "SKU ID",
            "Manufacturer",
            "Part Number",
            "Category",
            "Status",
            "Validation Flags",
            "Description",
        ]
        ws_review.append(review_headers)
        for cell in ws_review[1]:
            cell.font = header_font
            cell.fill = review_fill

        for rec in records:
            if rec.status == RowStatus.MANUAL_REVIEW or not rec.quality.validity:
                ws_review.append(
                    [
                        rec.identity.row_id,
                        rec.identity.sku_id,
                        rec.identity.manufacturer,
                        rec.identity.mfg_part_number,
                        rec.identity.category,
                        rec.status.value,
                        ", ".join(rec.quality.validation_flags),
                        rec.identity.raw_description,
                    ]
                )

        wb.save(output_path)
        return output_path
