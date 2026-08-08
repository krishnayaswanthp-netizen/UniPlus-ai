"""UniPulse AI — FastAPI application entry point.

Registers the product-enrichment API:

- ``POST /api/v1/enrich/single``  — enrich one product from a JSON payload
  (``ProductEnrichmentRequest``) and/or an uploaded PDF datasheet.
- ``POST /api/v1/enrich/batch``   — enrich many products from an uploaded
  CSV / Excel file, processed concurrently with a bounded semaphore.
- ``POST /api/v1/export/excel``   — render enriched results as a
  downloadable ``.xlsx`` workbook (``openpyxl``).

  The export endpoint accepts the enriched-product array as a JSON request
  body (or, for small payloads, a ``data`` query parameter). It is ``POST``
  because browser clients must not rely on ``GET`` request bodies, and large
  batches exceed URL length limits.

The enrichment pipeline itself lives in :func:`_enrich_single_product` and
reuses the existing services: ``WhitelistedSearchScraper`` (web search +
whitelisted scraping), ``PDFParser`` (uploaded datasheets),
``StructuredExtractor`` (LLM extraction; ``UnitNormalizer`` is applied
inside its post-processing step) and ``UnitNormalizer``.
"""

from __future__ import annotations

import asyncio
import csv
import io
import json
import time

from fastapi import FastAPI, File, HTTPException, Query, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from openpyxl import Workbook, load_workbook
from openpyxl.styles import Font
from openpyxl.utils import get_column_letter
from pydantic import ValidationError

from app.core.config import settings
from app.schemas.enrichment import (
    BatchEnrichmentResponse,
    BatchItemResult,
    ProductEnrichmentRequest,
    ProductEnrichmentResponse,
)
from app.services.extractor import StructuredExtractor
from app.services.parser import PDFParser
from app.services.scraper import WhitelistedSearchScraper

app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    description="High-throughput B2B product intelligence engine.",
)

# CORS: allow browser-based clients (e.g. the Streamlit dashboard) to call the API.
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Pipeline wiring
# ---------------------------------------------------------------------------

_pdf_parser = PDFParser()
_scraper = WhitelistedSearchScraper()
#: Built lazily because ``StructuredExtractor`` requires a Groq API key at
#: construction time; importing ``app.main`` must not fail without one.
_extractor: StructuredExtractor | None = None

#: URL stamped onto attributes extracted from an uploaded PDF datasheet.
_UPLOAD_SOURCE_PREFIX = "upload://"
#: Fallback label used when a product has no scrape/PDF/description content.
_FALLBACK_SOURCE_URL = "local://user-provided"

#: Upper bound on rows processed per batch upload (guard against runaway files).
_MAX_BATCH_ROWS = 500
#: Maximum number of products enriched in parallel inside a batch.
_BATCH_CONCURRENCY = 8

#: Global cap on concurrent LLM extraction calls, shared across ALL requests
#: (single + batch, every user). ``_BATCH_CONCURRENCY`` bounds row-level work
#: inside one upload; this bounds total Groq API concurrency server-wide so
#: two concurrent batches can't double the load on the rate limit.
_LLM_CONCURRENCY = asyncio.Semaphore(8)

_HEADER_FONT = Font(bold=True)


def _get_extractor() -> StructuredExtractor:
    """Return the shared extractor, building it on first use.

    Raises
    ------
    HTTPException
        With status 503 when ``GROQ_API_KEY`` is not configured.
    """
    global _extractor
    if _extractor is None:
        try:
            _extractor = StructuredExtractor()
        except ValueError as exc:
            raise HTTPException(
                status_code=503,
                detail=f"{exc} — extraction is unavailable until it is set",
            ) from exc
    return _extractor


def _build_sku(manufacturer: str, part_number: str) -> str:
    """Build a human-readable SKU from manufacturer + part number."""
    manufacturer = (manufacturer or "").strip() or "UNKNOWN"
    part_number = (part_number or "").strip() or "PART"
    return f"{manufacturer}-{part_number}"


def _estimate_cost(text_chars: int, attribute_count: int) -> float:
    """Rough per-request cost estimate (USD) for the LLM extraction call."""
    input_tokens = text_chars / 4.0
    output_tokens = attribute_count * 40.0
    return round((input_tokens + output_tokens) * 0.0000006, 6)


async def _enrich_single_product(
    request: ProductEnrichmentRequest,
    file_bytes: bytes | None = None,
    file_name: str | None = None,
) -> ProductEnrichmentResponse:
    """Run the full enrichment pipeline for a single product.

    Combines the optional uploaded PDF datasheet with the pages scraped for
    ``manufacturer + part_number``, then runs structured LLM extraction
    (which normalizes units via ``UnitNormalizer``) and summarizes the
    result. Never raises for "no content found" — the response simply comes
    back with an empty ``enriched_attributes`` list.
    """
    started = time.perf_counter()

    sources: list[dict[str, str]] = []

    if file_bytes:
        try:
            # PyMuPDF parsing is CPU-bound; run it off the event loop.
            pdf_text = await asyncio.to_thread(
                _pdf_parser.extract_text_and_tables, file_bytes
            )
        except ValueError as exc:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid PDF upload: {exc}",
            ) from exc
        sources.append(
            {
                "source_url": f"{_UPLOAD_SOURCE_PREFIX}{file_name or 'datasheet.pdf'}",
                "raw_content": pdf_text,
            }
        )

    try:
        scraped = await _scraper.search_and_scrape_async(
            request.manufacturer_name, request.part_number
        )
    except Exception:
        # A failed search/scrape must not sink the whole enrichment request.
        scraped = []
    sources.extend(scraped)

    chunks = [request.raw_description or ""]
    chunks.extend(source["raw_content"] for source in sources if source["raw_content"])
    combined = "\n\n".join(chunk.strip() for chunk in chunks if chunk.strip())

    sku_id = _build_sku(request.manufacturer_name, request.part_number)

    if not combined.strip():
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        return ProductEnrichmentResponse(
            sku_id=sku_id,
            category=request.category,
            processing_time_ms=round(elapsed_ms, 2),
        )

    source_url = (
        sources[0]["source_url"]
        if sources
        else _FALLBACK_SOURCE_URL
    )
    extractor = _get_extractor()
    # The LLM call is blocking; run it off the event loop so concurrent batch
    # items actually make progress, gated by the global semaphore so total LLM
    # concurrency stays bounded across all requests.
    async with _LLM_CONCURRENCY:
        attributes = await asyncio.to_thread(
            extractor.extract_product_specs,
            combined,
            source_url,
            request.category,
        )

    confidence = (
        sum(attribute.confidence_score for attribute in attributes) / len(attributes)
        if attributes
        else 0.0
    )
    elapsed_ms = (time.perf_counter() - started) * 1000.0

    return ProductEnrichmentResponse(
        sku_id=sku_id,
        category=request.category,
        enriched_attributes=attributes,
        overall_confidence=round(confidence, 4),
        processing_time_ms=round(elapsed_ms, 2),
        estimated_cost_usd=_estimate_cost(len(combined), len(attributes)),
    )


# ---------------------------------------------------------------------------
# Health probe
# ---------------------------------------------------------------------------


@app.get("/health")
def health() -> dict[str, str]:
    """Simple liveness probe for load balancers / uptime checks."""
    return {
        "status": "ok",
        "app": settings.app_name,
        "version": settings.app_version,
    }


# ---------------------------------------------------------------------------
# POST /api/v1/enrich/single
# ---------------------------------------------------------------------------


@app.post("/api/v1/enrich/single")
async def enrich_single(request: Request) -> ProductEnrichmentResponse:
    """Enrich a single product.

    Accepts either a JSON ``ProductEnrichmentRequest`` body or a
    ``multipart/form-data`` upload carrying the same fields plus an optional
    ``file`` (PDF datasheet).
    """
    content_type = request.headers.get("content-type", "").lower()

    if content_type.startswith("application/json"):
        try:
            payload = await request.json()
        except json.JSONDecodeError as exc:
            raise HTTPException(status_code=400, detail=f"Invalid JSON body: {exc}") from exc
        try:
            product = ProductEnrichmentRequest.model_validate(payload)
        except ValidationError as exc:
            raise HTTPException(status_code=422, detail=exc.errors()) from exc
        return await _enrich_single_product(product)

    if content_type.startswith("multipart/form-data"):
        form = await request.form()
        try:
            product = ProductEnrichmentRequest(
                manufacturer_name=form.get("manufacturer_name"),
                part_number=form.get("part_number"),
                raw_description=form.get("raw_description") or None,
                category=form.get("category"),
            )
        except ValidationError as exc:
            raise HTTPException(status_code=422, detail=exc.errors()) from exc

        upload: UploadFile | None = form.get("file")
        file_bytes = await upload.read() if upload is not None else None
        return await _enrich_single_product(
            product, file_bytes=file_bytes, file_name=upload.filename if upload else None
        )

    raise HTTPException(
        status_code=415,
        detail="Expected 'application/json' or 'multipart/form-data' content type",
    )


# ---------------------------------------------------------------------------
# POST /api/v1/enrich/batch
# ---------------------------------------------------------------------------


def _clean_cell(row: dict[str, str], *aliases: str) -> str:
    """Return the first non-empty cell among *aliases* (header-tolerant)."""
    for alias in aliases:
        value = row.get(alias)
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


def _row_identity(row: dict[str, str]) -> tuple[str, str, str]:
    """Return ``(manufacturer, part_number, category)`` from a batch row."""
    return (
        _clean_cell(row, "Manufacturer", "Manufacturer_Name", "manufacturer"),
        _clean_cell(
            row, "Part_Number", "PartNumber", "Part Number", "part_number"
        ),
        _clean_cell(row, "Category", "category"),
    )


def _row_to_request(row: dict[str, str]) -> ProductEnrichmentRequest:
    """Map a batch row (``Manufacturer``/``Part_Number``/``Category``) to a request."""
    manufacturer, part_number, category = _row_identity(row)
    return ProductEnrichmentRequest(
        manufacturer_name=manufacturer,
        part_number=part_number,
        raw_description=(
            _clean_cell(row, "Description", "Raw_Description", "raw_description")
            or None
        ),
        category=category,
    )


def _read_batch_rows(filename: str, raw: bytes) -> list[dict[str, str]]:
    """Parse an uploaded CSV or Excel file into a list of header-keyed rows.

    Raises
    ------
    HTTPException
        Status 415 for unsupported extensions, 400 for empty/unreadable files.
    """
    lowered = (filename or "").lower()

    if lowered.endswith(".csv"):
        text = raw.decode("utf-8-sig", errors="replace")
        rows = list(csv.DictReader(io.StringIO(text)))
    elif lowered.endswith(".xlsx"):
        try:
            workbook = load_workbook(
                io.BytesIO(raw), read_only=True, data_only=True
            )
        except Exception as exc:
            raise HTTPException(
                status_code=400, detail=f"Could not read spreadsheet: {exc}"
            ) from exc
        try:
            sheet = workbook.active
            try:
                header = [
                    str(cell).strip() if cell is not None else ""
                    for cell in next(sheet.iter_rows(min_row=1, max_row=1, values_only=True))
                ]
            except StopIteration:
                raise HTTPException(
                    status_code=400,
                    detail="Spreadsheet is empty; expected a header row",
                )
            rows = []
            for values in sheet.iter_rows(min_row=2, values_only=True):
                if all(value is None for value in values):
                    continue
                rows.append(
                    dict(zip(header, ["" if value is None else str(value) for value in values]))
                )
        finally:
            workbook.close()
    else:
        raise HTTPException(
            status_code=415,
            detail="Batch upload must be a '.csv' or '.xlsx' file",
        )

    if len(rows) > _MAX_BATCH_ROWS:
        raise HTTPException(
            status_code=422,
            detail=f"Batch upload exceeds the {_MAX_BATCH_ROWS}-row limit",
        )
    return rows


@app.post("/api/v1/enrich/batch")
async def enrich_batch(file: UploadFile = File(...)) -> BatchEnrichmentResponse:
    """Enrich many products from an uploaded CSV / Excel file.

    The file must contain ``Manufacturer``, ``Part_Number`` and ``Category``
    columns. Rows are processed concurrently (bounded by a semaphore) and
    each row is reported independently, so one bad row never sinks the run.
    """
    raw = await file.read()
    if not raw:
        raise HTTPException(status_code=400, detail="Uploaded file is empty")

    # Fail fast (HTTP 503) when extraction is unavailable, matching /single.
    _get_extractor()

    # openpyxl/csv parsing is CPU-bound; run it off the event loop.
    rows = await asyncio.to_thread(_read_batch_rows, file.filename or "", raw)
    semaphore = asyncio.Semaphore(_BATCH_CONCURRENCY)

    async def process_row(row: dict[str, str]) -> BatchItemResult:
        async with semaphore:
            try:
                product = _row_to_request(row)
                response = await _enrich_single_product(product)
                return BatchItemResult(
                    sku_id=response.sku_id,
                    manufacturer_name=product.manufacturer_name,
                    part_number=product.part_number,
                    category=product.category,
                    status="success",
                    enriched_attributes=response.enriched_attributes,
                    overall_confidence=response.overall_confidence,
                    processing_time_ms=response.processing_time_ms,
                )
            except Exception as exc:
                manufacturer, part_number, category = _row_identity(row)
                return BatchItemResult(
                    sku_id=_build_sku(manufacturer, part_number),
                    manufacturer_name=manufacturer,
                    part_number=part_number,
                    category=category,
                    status="error",
                    error=str(exc),
                )

    results = await asyncio.gather(*(process_row(row) for row in rows))
    succeeded = sum(1 for result in results if result.status == "success")
    return BatchEnrichmentResponse(
        total=len(results),
        succeeded=succeeded,
        failed=len(results) - succeeded,
        results=results,
    )


# ---------------------------------------------------------------------------
# POST /api/v1/export/excel
# ---------------------------------------------------------------------------


def _style_sheet(workbook: Workbook, title: str, widths: list[int]) -> None:
    """Apply bold headers, sane column widths and frozen panes to *title*."""
    sheet = workbook[title]
    for column, width in enumerate(widths, start=1):
        sheet.column_dimensions[get_column_letter(column)].width = width
    for cell in sheet[1]:
        cell.font = _HEADER_FONT
    sheet.freeze_panes = "A2"


def _build_workbook(
    products: list[tuple[ProductEnrichmentResponse, str, str]],
) -> Workbook:
    """Render enriched products into a two-sheet Excel workbook."""
    workbook = Workbook()
    products_sheet = workbook.active
    products_sheet.title = "Products"
    products_sheet.append(
        [
            "sku_id",
            "category",
            "manufacturer_name",
            "part_number",
            "overall_confidence",
            "processing_time_ms",
            "estimated_cost_usd",
            "attribute_count",
        ]
    )
    attributes_sheet = workbook.create_sheet("Attributes")
    attributes_sheet.append(
        [
            "sku_id",
            "field_name",
            "raw_value",
            "normalized_value",
            "unit",
            "confidence_score",
            "source_url",
        ]
    )

    for response, manufacturer, part_number in products:
        products_sheet.append(
            [
                response.sku_id,
                response.category,
                manufacturer,
                part_number,
                response.overall_confidence,
                response.processing_time_ms,
                response.estimated_cost_usd,
                len(response.enriched_attributes),
            ]
        )
        for attribute in response.enriched_attributes:
            attributes_sheet.append(
                [
                    response.sku_id,
                    attribute.field_name,
                    attribute.raw_value,
                    attribute.normalized_value,
                    attribute.unit,
                    attribute.confidence_score,
                    attribute.source_url,
                ]
            )

    _style_sheet(workbook, "Products", [34, 14, 22, 20, 18, 18, 18, 16])
    _style_sheet(
        workbook,
        "Attributes",
        [34, 22, 20, 20, 10, 18, 44],
    )
    return workbook


def _render_workbook(
    products: list[tuple[ProductEnrichmentResponse, str, str]],
) -> io.BytesIO:
    """Build and serialize a workbook inside a single worker thread.

    ``openpyxl`` objects are not thread-safe, so building *and* saving must
    happen in the same thread — ``asyncio.to_thread`` guarantees this by
    running the whole function in one executor call.
    """
    buffer = io.BytesIO()
    workbook = _build_workbook(products)
    try:
        workbook.save(buffer)
    finally:
        workbook.close()
    buffer.seek(0)
    return buffer


@app.post("/api/v1/export/excel")
async def export_excel(
    request: Request,
    data: str | None = Query(
        default=None,
        description="JSON-encoded array of enriched product records. "
        "Alternative (preferred): send the array as the JSON request body.",
    ),
) -> StreamingResponse:
    """Render enriched results as a downloadable ``.xlsx`` workbook.

    Expects a JSON array of enriched product records, sent either as the
    request body (recommended — avoids URL-length limits on large batches)
    or as the ``data`` query parameter.
    """
    if data is not None:
        try:
            payload = json.loads(data)
        except json.JSONDecodeError as exc:
            raise HTTPException(
                status_code=400, detail=f"Invalid JSON in 'data' query parameter: {exc}"
            ) from exc
    else:
        raw_body = await request.body()
        if not raw_body:
            raise HTTPException(
                status_code=422,
                detail="Provide enriched results via a JSON request body "
                "(array of enriched product records) or the 'data' query parameter",
            )
        try:
            payload = json.loads(raw_body)
        except json.JSONDecodeError as exc:
            raise HTTPException(status_code=400, detail=f"Invalid JSON body: {exc}") from exc

    if not isinstance(payload, list):
        raise HTTPException(
            status_code=422, detail="Enriched results must be a JSON array"
        )

    products: list[tuple[ProductEnrichmentResponse, str, str]] = []
    for record in payload:
        if not isinstance(record, dict):
            raise HTTPException(
                status_code=422, detail="Every enriched product must be a JSON object"
            )
        try:
            response = ProductEnrichmentResponse.model_validate(record)
        except ValidationError as exc:
            raise HTTPException(
                status_code=422,
                detail=f"Invalid enriched product record: {exc.errors()}",
            ) from exc
        products.append(
            (
                response,
                str(record.get("manufacturer_name", "")),
                str(record.get("part_number", "")),
            )
        )

    # openpyxl rendering is CPU-bound; build + save in one worker thread.
    buffer = await asyncio.to_thread(_render_workbook, products)

    return StreamingResponse(
        buffer,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": 'attachment; filename="enrichment_export.xlsx"'
        },
    )
