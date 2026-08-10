"""Tests for the UniPulse AI FastAPI routes (``app.main``).

Covers the three registered endpoints:

- ``POST /api/v1/enrich/single``  — JSON payload and multipart PDF uploads.
- ``POST /api/v1/enrich/batch``   — CSV and Excel uploads (incl. failure rows).
- ``POST /api/v1/export/excel``   — workbook generation and error handling.

All network/LLM dependencies are faked via monkeypatched module-level
pipeline objects, so the suite runs fully offline and without API keys.
"""

from __future__ import annotations

import csv
import io
import json

import fitz
import openpyxl
import pytest
from fastapi.testclient import TestClient

import app.main as api
from app.schemas.enrichment import IndustrialAttribute

# ---------------------------------------------------------------------------
# Test doubles for the pipeline services
# ---------------------------------------------------------------------------


class FakeScraper:
    """Stand-in for ``WhitelistedSearchScraper`` with canned scrape results."""

    def __init__(self, results: list[dict[str, str]] | None = None) -> None:
        self.results = results or []

    async def search_and_scrape_async(self, manufacturer: str, part_number: str):
        return list(self.results)


class FakeExtractor:
    """Stand-in for ``StructuredExtractor`` that echoes deterministic specs."""

    def __init__(self, attributes: list[IndustrialAttribute] | None = None) -> None:
        self.attributes = attributes or [
            IndustrialAttribute(
                field_name="voltage",
                raw_value="120 VAC",
                normalized_value="120",
                unit="V",
                confidence_score=0.95,
                source_url="https://example.com/spec",
            ),
            IndustrialAttribute(
                field_name="airflow_cfm",
                raw_value="800 CFM",
                normalized_value="800",
                unit="CFM",
                confidence_score=0.9,
                source_url="https://example.com/spec",
            ),
        ]
        self.last_raw_text: str | None = None
        self.last_source_url: str | None = None
        self.last_category: str | None = None

    def extract_product_specs(self, raw_text: str, source_url: str, category: str):
        self.last_raw_text = raw_text
        self.last_source_url = source_url
        self.last_category = category
        return [
            attribute.model_copy(update={"source_url": source_url})
            for attribute in self.attributes
        ]


_CANNED_SOURCE: dict[str, str] = {
    "source_url": "https://example.com/spec",
    "raw_content": "Voltage: 120 VAC\nAirflow: 800 CFM",
}


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """A TestClient wired up with offline scraper + extractor doubles."""
    monkeypatch.setattr(api, "_scraper", FakeScraper([_CANNED_SOURCE]))
    monkeypatch.setattr(api, "_extractor", FakeExtractor())
    with TestClient(api.app) as test_client:
        yield test_client


def _make_pdf(text: str) -> bytes:
    """Render *text* into an in-memory PDF (same approach as test_pipeline)."""
    document = fitz.open()
    page = document.new_page()
    page.insert_text((72, 72), text)
    return document.tobytes()


def _csv_bytes(rows: list[list[str]]) -> bytes:
    """Serialize *rows* under the canonical batch header."""
    return _csv_bytes_with_headers(
        ["Manufacturer", "Part_Number", "Category"], rows
    )


def _csv_bytes_with_headers(headers: list[str], rows: list[list[str]]) -> bytes:
    """Serialize *rows* under an arbitrary *headers* row."""
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(headers)
    writer.writerows(rows)
    return buffer.getvalue().encode("utf-8")


def _xlsx_bytes(rows: list[list[str]]) -> bytes:
    """Serialize *rows* under the canonical batch header as an .xlsx file."""
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.append(["Manufacturer", "Part_Number", "Category"])
    for row in rows:
        sheet.append(row)
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


# ---------------------------------------------------------------------------
# POST /api/v1/enrich/single
# ---------------------------------------------------------------------------


def test_single_enrich_json_payload(client: TestClient) -> None:
    response = client.post(
        "/api/v1/enrich/single",
        json={
            "manufacturer_name": "Honeywell",
            "part_number": "TH6320U2008",
            "raw_description": "24 VAC thermostat, 800 CFM airflow.",
            "category": "HVAC",
        },
    )
    assert response.status_code == 200
    body = response.json()

    assert body["sku_id"] == "Honeywell-TH6320U2008"
    assert body["category"] == "HVAC"
    assert len(body["enriched_attributes"]) == 2

    first = body["enriched_attributes"][0]
    assert first["field_name"] == "voltage"
    assert first["normalized_value"] == "120"
    assert first["unit"] == "V"
    # source_url is stamped from the scraped source, not invented by the LLM.
    assert first["source_url"] == "https://example.com/spec"

    assert 0.0 <= body["overall_confidence"] <= 1.0
    assert body["processing_time_ms"] >= 0.0
    assert body["estimated_cost_usd"] >= 0.0


def test_single_enrich_with_pdf_upload(client: TestClient) -> None:
    extractor = api._extractor  # the fake wired in by the fixture
    pdf_bytes = _make_pdf("Voltage: 24 VAC\nAirflow: 800 CFM")

    response = client.post(
        "/api/v1/enrich/single",
        data={
            "manufacturer_name": "Honeywell",
            "part_number": "TH6320U2008",
            "category": "HVAC",
        },
        files={"file": ("datasheet.pdf", pdf_bytes, "application/pdf")},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["sku_id"] == "Honeywell-TH6320U2008"
    assert len(body["enriched_attributes"]) == 2
    # The uploaded PDF text must have reached the extractor, and its source
    # URL must be the upload marker, not a web source.
    assert "24 VAC" in extractor.last_raw_text
    assert "800 CFM" in extractor.last_raw_text
    assert extractor.last_source_url == "upload://datasheet.pdf"
    assert all(
        attribute["source_url"] == "upload://datasheet.pdf"
        for attribute in body["enriched_attributes"]
    )


def test_single_enrich_invalid_category_rejected(client: TestClient) -> None:
    response = client.post(
        "/api/v1/enrich/single",
        json={
            "manufacturer_name": "Honeywell",
            "part_number": "TH6320U2008",
            "category": "Automotive",
        },
    )
    assert response.status_code == 422


def test_single_enrich_unsupported_content_type_rejected(client: TestClient) -> None:
    response = client.post(
        "/api/v1/enrich/single",
        content=b"manufacturer_name=Honeywell",
        headers={"Content-Type": "text/plain"},
    )
    assert response.status_code == 415


def test_single_enrich_missing_api_key_returns_503(monkeypatch: pytest.MonkeyPatch) -> None:
    """With no Groq key configured, extraction degrades to HTTP 503."""
    monkeypatch.setattr(api, "_scraper", FakeScraper([_CANNED_SOURCE]))

    class BrokenExtractor:
        def __init__(self, *args: object, **kwargs: object) -> None:
            raise ValueError("GROQ_API_KEY is required. Set it in .env or pass api_key=")

    monkeypatch.setattr(api, "_extractor", None)
    monkeypatch.setattr(api, "StructuredExtractor", BrokenExtractor)

    with TestClient(api.app) as test_client:
        response = test_client.post(
            "/api/v1/enrich/single",
            json={
                "manufacturer_name": "Honeywell",
                "part_number": "TH6320U2008",
                "category": "HVAC",
            },
        )
    assert response.status_code == 503


# ---------------------------------------------------------------------------
# POST /api/v1/enrich/batch
# ---------------------------------------------------------------------------


def test_batch_enrich_csv(client: TestClient) -> None:
    content = _csv_bytes(
        [["Honeywell", "TH6320U2008", "HVAC"], ["Trane", "XV18", "HVAC"]]
    )
    response = client.post(
        "/api/v1/enrich/batch",
        files={"file": ("batch.csv", content, "text/csv")},
    )
    assert response.status_code == 200
    body = response.json()

    assert body["total"] == 2
    assert body["succeeded"] == 2
    assert body["failed"] == 0
    skus = {result["sku_id"] for result in body["results"]}
    assert skus == {"Honeywell-TH6320U2008", "Trane-XV18"}
    assert all(result["status"] == "success" for result in body["results"])


def test_batch_enrich_flexible_manufacturer_headers(client: TestClient) -> None:
    """Lowercase/spaced header variants still yield real manufacturer SKUs
    (no "UNKNOWN-" prefix)."""
    content = _csv_bytes_with_headers(
        ["manufacturer", "Part Number", "category"],
        [["Siemens", "3RT2026-1BB40", "Electrical"]],
    )
    response = client.post(
        "/api/v1/enrich/batch",
        files={"file": ("batch.csv", content, "text/csv")},
    )
    assert response.status_code == 200
    assert response.json()["results"][0]["sku_id"] == "Siemens-3RT2026-1BB40"


def test_batch_enrich_mfr_and_brand_headers(client: TestClient) -> None:
    """``Mfr``/``Brand`` columns map to the manufacturer field."""
    for header in ("Mfr", "Brand"):
        content = _csv_bytes_with_headers(
            [header, "Part_Number", "Category"],
            [["Schneider Electric", "LC1D09", "Electrical"]],
        )
        response = client.post(
            "/api/v1/enrich/batch",
            files={"file": ("batch.csv", content, "text/csv")},
        )
        assert response.status_code == 200
        assert (
            response.json()["results"][0]["sku_id"]
            == "Schneider Electric-LC1D09"
        )


def test_batch_enrich_excel(client: TestClient) -> None:
    content = _xlsx_bytes([["Honeywell", "TH6320U2008", "HVAC"]])
    response = client.post(
        "/api/v1/enrich/batch",
        files={
            "file": (
                "batch.xlsx",
                content,
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert body["succeeded"] == 1
    assert body["results"][0]["sku_id"] == "Honeywell-TH6320U2008"


def test_batch_enrich_reports_partial_failure(client: TestClient) -> None:
    content = _csv_bytes(
        [
            ["Honeywell", "TH6320U2008", "HVAC"],
            ["Trane", "XV18", "Automotive"],  # invalid category -> row error
        ]
    )
    response = client.post(
        "/api/v1/enrich/batch",
        files={"file": ("batch.csv", content, "text/csv")},
    )
    assert response.status_code == 200
    body = response.json()

    assert body["total"] == 2
    assert body["succeeded"] == 1
    assert body["failed"] == 1
    error_rows = [result for result in body["results"] if result["status"] == "error"]
    assert len(error_rows) == 1
    assert error_rows[0]["manufacturer_name"] == "Trane"
    assert error_rows[0]["part_number"] == "XV18"
    assert error_rows[0]["error"]


def test_batch_missing_api_key_returns_503(monkeypatch: pytest.MonkeyPatch) -> None:
    """A batch run fails fast with 503 when no Groq key is configured."""
    monkeypatch.setattr(api, "_scraper", FakeScraper([_CANNED_SOURCE]))

    class BrokenExtractor:
        def __init__(self, *args: object, **kwargs: object) -> None:
            raise ValueError("GROQ_API_KEY is required. Set it in .env or pass api_key=")

    monkeypatch.setattr(api, "_extractor", None)
    monkeypatch.setattr(api, "StructuredExtractor", BrokenExtractor)

    content = _csv_bytes([["Honeywell", "TH6320U2008", "HVAC"]])
    with TestClient(api.app) as test_client:
        response = test_client.post(
            "/api/v1/enrich/batch",
            files={"file": ("batch.csv", content, "text/csv")},
        )
    assert response.status_code == 503


def test_batch_rejects_unsupported_extension(client: TestClient) -> None:
    response = client.post(
        "/api/v1/enrich/batch",
        files={"file": ("batch.txt", b"Manufacturer,Part_Number,Category\n", "text/plain")},
    )
    assert response.status_code == 415


def test_batch_rejects_empty_upload(client: TestClient) -> None:
    response = client.post(
        "/api/v1/enrich/batch",
        files={"file": ("batch.csv", b"", "text/csv")},
    )
    assert response.status_code == 400


# ---------------------------------------------------------------------------
# POST /api/v1/export/excel
# ---------------------------------------------------------------------------


def _sample_products() -> list[dict[str, object]]:
    return [
        {
            "sku_id": "Honeywell-TH6320U2008",
            "category": "HVAC",
            "manufacturer_name": "Honeywell",
            "part_number": "TH6320U2008",
            "overall_confidence": 0.925,
            "processing_time_ms": 12.5,
            "estimated_cost_usd": 0.001,
            "enriched_attributes": [
                {
                    "field_name": "voltage",
                    "raw_value": "120 VAC",
                    "normalized_value": "120",
                    "unit": "V",
                    "confidence_score": 0.95,
                    "source_url": "https://example.com/spec",
                }
            ],
        }
    ]


def test_export_excel_generates_workbook(client: TestClient) -> None:
    response = client.post(
        "/api/v1/export/excel",
        params={"data": json.dumps(_sample_products())},
    )
    assert response.status_code == 200
    assert "spreadsheetml" in response.headers["content-type"]
    assert response.headers["content-disposition"].startswith("attachment")

    workbook = openpyxl.load_workbook(io.BytesIO(response.content))
    assert workbook.sheetnames == ["Products", "Attributes"]

    products_sheet = workbook["Products"]
    headers = [cell.value for cell in next(products_sheet.iter_rows(min_row=1, max_row=1))]
    assert headers[:4] == ["sku_id", "category", "manufacturer_name", "part_number"]
    product_rows = list(products_sheet.iter_rows(min_row=2, values_only=True))
    assert len(product_rows) == 1
    assert product_rows[0][0] == "Honeywell-TH6320U2008"
    assert product_rows[0][2] == "Honeywell"  # manufacturer survives the round-trip

    attributes_sheet = workbook["Attributes"]
    attribute_rows = list(attributes_sheet.iter_rows(min_row=2, values_only=True))
    assert len(attribute_rows) == 1
    assert attribute_rows[0][0] == "Honeywell-TH6320U2008"
    assert attribute_rows[0][1] == "voltage"
    assert attribute_rows[0][3] == "120"
    assert attribute_rows[0][4] == "V"


def test_export_excel_accepts_json_body(client: TestClient) -> None:
    response = client.post(
        "/api/v1/export/excel",
        content=json.dumps(_sample_products()),
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 200
    assert len(response.content) > 0


def test_export_excel_rejects_invalid_json(client: TestClient) -> None:
    response = client.post("/api/v1/export/excel", params={"data": "{not json"})
    assert response.status_code == 400


def test_export_excel_rejects_non_list_data(client: TestClient) -> None:
    response = client.post(
        "/api/v1/export/excel",
        params={"data": json.dumps({"sku_id": "not-a-list"})},
    )
    assert response.status_code == 422


def test_export_excel_rejects_missing_data(client: TestClient) -> None:
    response = client.post("/api/v1/export/excel")
    assert response.status_code == 422


def test_export_excel_rejects_get_method(client: TestClient) -> None:
    """Export is a POST endpoint — GET must be rejected (405)."""
    response = client.get(
        "/api/v1/export/excel",
        params={"data": json.dumps(_sample_products())},
    )
    assert response.status_code == 405
