"""Tests for the UniPulse AI enrichment pipeline building blocks.

Covers PDF ingestion (``PDFParser``), the web-source domain whitelist
(``app.core.security``), scraper filtering (``WhitelistedSearchScraper``),
the mock fallback specs (``get_fallback_mock_specs``) that the scraper no
longer auto-injects, and the mocked Groq structured extraction +
normalization flow (``StructuredExtractor``).
"""

from __future__ import annotations

import asyncio
import io

import fitz
import pytest

from app.core.security import is_domain_allowed
from app.schemas.enrichment import IndustrialAttribute
from app.services.extractor import StructuredExtractor
from app.services.parser import PDFParser
from app.services.scraper import WhitelistedSearchScraper, get_fallback_mock_specs


# ---------------------------------------------------------------------------
# PDF fixtures
# ---------------------------------------------------------------------------


def _make_pdf(text: str, *, password: str | None = None) -> bytes:
    """Render *text* into an in-memory PDF (optionally password-protected)."""
    document = fitz.open()
    page = document.new_page()
    page.insert_text((72, 72), text)
    if password:
        buffer = io.BytesIO()
        document.save(
            buffer,
            encryption=fitz.PDF_ENCRYPT_AES_256,
            owner_pw=password,
            user_pw=password,
        )
        return buffer.getvalue()
    return document.tobytes()


def _make_pdf_with_table() -> bytes:
    """Render a small ruled 2x2 spec table (grid lines + cell text)."""
    document = fitz.open()
    page = document.new_page()

    left, top, right, bottom = 72, 72, 320, 140
    mid_x, mid_y = (left + right) // 2, (top + bottom) // 2

    for x in (left, mid_x, right):
        page.draw_line(fitz.Point(x, top), fitz.Point(x, bottom))
    for y in (top, mid_y, bottom):
        page.draw_line(fitz.Point(left, y), fitz.Point(right, y))

    page.insert_text((left + 4, top + 14), "Voltage")
    page.insert_text((mid_x + 4, top + 14), "24 VAC")
    page.insert_text((left + 4, mid_y + 14), "Airflow")
    page.insert_text((mid_x + 4, mid_y + 14), "800 CFM")
    return document.tobytes()


@pytest.fixture
def pdf_parser() -> PDFParser:
    return PDFParser()


# ---------------------------------------------------------------------------
# PDF extraction
# ---------------------------------------------------------------------------


def test_pdf_extracts_clean_text(pdf_parser: PDFParser) -> None:
    content = _make_pdf("Model: TH6320U2008\nVoltage: 24 VAC\nAirflow: 800 CFM")
    text = pdf_parser.extract_text_and_tables(content)
    assert "Model: TH6320U2008" in text
    assert "Voltage: 24 VAC" in text
    assert "Airflow: 800 CFM" in text


def test_pdf_table_layout_preserved(pdf_parser: PDFParser) -> None:
    text = pdf_parser.extract_text_and_tables(_make_pdf_with_table())
    assert "Voltage" in text
    assert "24 VAC" in text
    assert "Airflow" in text
    assert "800 CFM" in text


def test_pdf_password_protected_raises(pdf_parser: PDFParser) -> None:
    content = _make_pdf("secret specs", password="hunter2")
    with pytest.raises(ValueError, match="[Pp]assword"):
        pdf_parser.extract_text_and_tables(content)


def test_pdf_corrupt_bytes_raises(pdf_parser: PDFParser) -> None:
    with pytest.raises(ValueError, match="[Cc]orrupt"):
        pdf_parser.extract_text_and_tables(b"this is definitely not a pdf")


def test_pdf_empty_bytes_raises(pdf_parser: PDFParser) -> None:
    with pytest.raises(ValueError, match="empty"):
        pdf_parser.extract_text_and_tables(b"")


# ---------------------------------------------------------------------------
# Domain whitelist
# ---------------------------------------------------------------------------


def test_retail_marketplace_domains_blocked() -> None:
    assert is_domain_allowed("https://www.amazon.com/dp/B0ABC123XYZ") is False
    assert is_domain_allowed("https://shop.ebay.com/itm/12345") is False
    assert is_domain_allowed("https://www.flipkart.com/item/abc") is False
    assert is_domain_allowed("https://www.walmart.com/ip/xyz") is False
    # Two-part-TLD mirrors are covered by suffix matching, not just .com.
    assert is_domain_allowed("https://www.amazon.co.uk/dp/B0ABC123XYZ") is False
    assert is_domain_allowed("https://www.ebay.co.uk/itm/12345") is False


def test_manufacturer_domain_allowed() -> None:
    assert (
        is_domain_allowed(
            "https://www.honeywellhome.com/us/en/product/TH6320U2008"
        )
        is True
    )


def test_direct_pdf_link_allowed() -> None:
    assert (
        is_domain_allowed("https://www.honeywellhome.com/datasheets/TH6320.pdf")
        is True
    )


def test_malformed_url_rejected() -> None:
    assert is_domain_allowed("not-a-valid-url") is False
    assert is_domain_allowed("") is False


# ---------------------------------------------------------------------------
# Scraper whitelist filtering
# ---------------------------------------------------------------------------


def test_scraper_filters_retail_domains(monkeypatch: pytest.MonkeyPatch) -> None:
    scraper = WhitelistedSearchScraper()
    allowed = "https://www.honeywellhome.com/us/en/product/TH6320U2008"
    blocked = "https://www.amazon.com/dp/B0ABC123XYZ"

    monkeypatch.setattr(
        scraper, "_search_links", lambda query: [blocked, allowed]
    )

    async def fake_scrape_one(client: object, url: str) -> dict[str, str]:
        return {"source_url": url, "raw_content": f"content of {url}"}

    monkeypatch.setattr(scraper, "_scrape_one", fake_scrape_one)

    results = scraper.search_and_scrape("Honeywell", "TH6320U2008")
    assert [result["source_url"] for result in results] == [allowed]


def test_scraper_empty_when_no_allowed_sources(monkeypatch: pytest.MonkeyPatch) -> None:
    scraper = WhitelistedSearchScraper()
    monkeypatch.setattr(
        scraper, "_search_links", lambda query: ["https://www.ebay.com/itm/1"]
    )
    assert scraper.search_and_scrape("Honeywell", "TH6320U2008") == []


def test_scraper_drops_irrelevant_but_allowed_sources(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Allowed, non-retail pages that mention neither the manufacturer nor
    the part number are dropped before any scraping happens."""
    scraper = WhitelistedSearchScraper()
    monkeypatch.setattr(
        scraper,
        "_search_links",
        lambda query: ["https://www.example.com/catalog/thermostat-1234"],
    )
    assert scraper.search_and_scrape("Honeywell", "TH6320U2008") == []


# ---------------------------------------------------------------------------
# Mock fallback specs (DuckDuckGo rate-limit / 202-block resilience)
# ---------------------------------------------------------------------------


def test_fallback_mock_specs_hvac_for_thermostat() -> None:
    """A thermostat part number gets the HVAC starter-clue block."""
    results = get_fallback_mock_specs("Honeywell", "T6-PRO-STAT")
    assert len(results) == 1
    block = results[0]
    assert block["source_url"].startswith("mock://fallback/")
    assert "T6-PRO-STAT" in block["raw_content"]
    assert "24 VAC" in block["raw_content"]
    assert "7-day" in block["raw_content"]


def test_fallback_mock_specs_plumbing_and_electrical() -> None:
    """Valve/NPT products map to Plumbing, breaker products to Electrical."""
    plumbing = get_fallback_mock_specs("Watts", "1/2-14 NPT Ball Valve")
    assert "PSI" in plumbing[0]["raw_content"]
    assert "NPT" in plumbing[0]["raw_content"]

    electrical = get_fallback_mock_specs("Square D", "QO120 Breaker")
    assert "120/240 VAC" in electrical[0]["raw_content"]
    assert "Current rating: 20 A" in electrical[0]["raw_content"]


def test_fallback_mock_specs_unknown_product_uses_general_block() -> None:
    block = get_fallback_mock_specs("Acme", "XYZ-123")[0]
    assert "Product Technical Specification" in block["raw_content"]


def test_scraper_returns_empty_when_search_yields_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A rate-limited/202-blocked DuckDuckGo (zero links) yields no content —
    the scraper must not inject fabricated starter clues."""
    scraper = WhitelistedSearchScraper()
    monkeypatch.setattr(scraper, "_search_links", lambda query: [])
    assert scraper.search_and_scrape("Honeywell", "T6-PRO-STAT") == []


def test_scraper_does_not_fallback_when_results_exist_but_filtered(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A live search whose results fail whitelisting must NOT trigger mock."""
    scraper = WhitelistedSearchScraper()
    monkeypatch.setattr(
        scraper, "_search_links", lambda query: ["https://www.ebay.com/itm/1"]
    )
    assert scraper.search_and_scrape("Honeywell", "TH6320U2008") == []


def test_scraper_async_returns_empty_when_search_yields_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The async variant behaves like the sync one — no mock fallback."""
    scraper = WhitelistedSearchScraper()
    monkeypatch.setattr(scraper, "_search_links", lambda query: [])

    async def run() -> list[dict[str, str]]:
        return await scraper.search_and_scrape_async("Honeywell", "T6-PRO-STAT")

    assert asyncio.run(run()) == []


# ---------------------------------------------------------------------------
# Structured extraction (mocked LLM) + normalization wiring
# ---------------------------------------------------------------------------


def test_structured_extractor_formats_normalizes_and_enforces_source(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """LLM output is post-processed: values normalized, source_url stamped,
    confidence clamped to [0.0, 1.0]."""
    # ``model_construct`` bypasses Pydantic validation so we can simulate raw
    # model output that violates the schema (out-of-range confidence), which
    # ``_postprocess`` is responsible for clamping.
    llm_output = [
        IndustrialAttribute(
            field_name="diameter_mm",
            raw_value="10mm",
            normalized_value="10mm",
            confidence_score=0.9,
            source_url="https://llm.got.it/wrong",
        ),
        IndustrialAttribute.model_construct(
            field_name="voltage",
            raw_value="120 VAC",
            normalized_value="120 VAC",
            confidence_score=1.5,  # out-of-range on purpose
            source_url="https://llm.got.it/wrong",
        ),
        IndustrialAttribute(
            field_name="part_number",
            raw_value="PN-1234-A",
            normalized_value="PN-1234-A",
            confidence_score=0.5,
            source_url="https://llm.got.it/wrong",
        ),
    ]

    monkeypatch.setattr(
        StructuredExtractor,
        "_request_attributes",
        lambda self, messages: llm_output,
    )

    extractor = StructuredExtractor(api_key="test-key")
    source_url = "https://www.honeywellhome.com/datasheets/TH6320.pdf"
    result = extractor.extract_product_specs("10mm, 120 VAC", source_url, "HVAC")

    assert len(result) == 3

    # source_url is forcibly the exact one passed in.
    assert all(attribute.source_url == source_url for attribute in result)

    # raw_value is run through UnitNormalizer.
    assert result[0].raw_value == "10mm"
    assert result[0].normalized_value == "10"
    assert result[0].unit == "mm"

    assert result[1].normalized_value == "120"
    assert result[1].unit == "V"
    assert result[1].confidence_score == 1.0  # clamped from 1.5

    # Unparseable values pass through untouched.
    assert result[2].normalized_value == "PN-1234-A"
    assert result[2].unit is None


def test_structured_extractor_empty_text_returns_empty_list() -> None:
    extractor = StructuredExtractor(api_key="test-key")
    assert extractor.extract_product_specs("   ", "https://example.com", "HVAC") == []


def test_structured_extractor_requires_api_key() -> None:
    with pytest.raises(ValueError, match="GROQ_API_KEY"):
        StructuredExtractor(api_key="")


def test_structured_extractor_processes_fallback_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Mock fallback text flows through extraction like any document text."""
    fallback = get_fallback_mock_specs("Honeywell", "T6-PRO-STAT")[0]
    extractor = StructuredExtractor(api_key="test-key")
    monkeypatch.setattr(
        StructuredExtractor,
        "_request_attributes",
        lambda self, messages: [
            IndustrialAttribute(
                field_name="voltage",
                raw_value="24 VAC",
                normalized_value="24 VAC",
                confidence_score=0.6,
                source_url="https://llm.got.it/wrong",
            )
        ],
    )
    result = extractor.extract_product_specs(
        fallback["raw_content"], fallback["source_url"], "HVAC"
    )
    assert len(result) == 1
    # The mock source URL is stamped verbatim, values still normalize.
    assert result[0].source_url == fallback["source_url"]
    assert result[0].normalized_value == "24"
    assert result[0].unit == "V"


# ---------------------------------------------------------------------------
# LLM request retry/backoff behavior
# ---------------------------------------------------------------------------


class _FakeRateLimitError(Exception):
    """Minimal stand-in for a Groq 429 with a ``status_code`` attribute."""

    status_code = 429


class _FakeServerError(Exception):
    """Minimal stand-in for a Groq 5xx with a ``status_code`` attribute."""

    status_code = 503


def _fake_voltage_attribute() -> IndustrialAttribute:
    return IndustrialAttribute(
        field_name="voltage",
        raw_value="120 VAC",
        normalized_value="120",
        unit="V",
        confidence_score=0.9,
        source_url="https://example.com/spec",
    )


def test_request_attributes_retries_transient_errors_then_succeeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """429/5xx are retried with backoff on the same model, then succeed."""
    extractor = StructuredExtractor(api_key="test-key")
    monkeypatch.setattr("app.services.extractor.time.sleep", lambda _seconds: None)
    calls: list[str] = []

    def fake_create(model: str, **kwargs: object) -> list[IndustrialAttribute]:
        calls.append(model)
        if len(calls) < 3:
            raise _FakeRateLimitError("rate limited")
        return [_fake_voltage_attribute()]

    monkeypatch.setattr(extractor.client.chat.completions, "create", fake_create)

    result = extractor._request_attributes([{"role": "user", "content": "x"}])

    assert len(calls) == 3  # two backoff retries, all on the primary model
    assert calls == [extractor.model] * 3
    assert len(result) == 1


def test_request_attributes_moves_to_fallback_on_non_transient_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A validation/auth error skips retries and tries the fallback model."""
    extractor = StructuredExtractor(api_key="test-key")
    calls: list[str] = []

    def fake_create(model: str, **kwargs: object) -> list[IndustrialAttribute]:
        calls.append(model)
        raise ValueError("schema validation failed")

    monkeypatch.setattr(extractor.client.chat.completions, "create", fake_create)

    with pytest.raises(RuntimeError, match="schema validation failed"):
        extractor._request_attributes([{"role": "user", "content": "x"}])

    assert calls == [extractor.model, extractor.fallback_model]


def test_request_attributes_preserves_error_detail_on_total_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The final RuntimeError carries the underlying API error message."""
    extractor = StructuredExtractor(api_key="test-key")
    monkeypatch.setattr("app.services.extractor.time.sleep", lambda _seconds: None)

    def fake_create(model: str, **kwargs: object) -> list[IndustrialAttribute]:
        raise _FakeServerError("upstream gateway timeout")

    monkeypatch.setattr(extractor.client.chat.completions, "create", fake_create)

    with pytest.raises(RuntimeError, match="upstream gateway timeout"):
        extractor._request_attributes([{"role": "user", "content": "x"}])


def test_request_attributes_retries_5xx_before_switching_models(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exhausted transient retries on the primary then try the fallback."""
    extractor = StructuredExtractor(api_key="test-key")
    monkeypatch.setattr("app.services.extractor.time.sleep", lambda _seconds: None)
    calls: list[str] = []

    def fake_create(model: str, **kwargs: object) -> list[IndustrialAttribute]:
        calls.append(model)
        raise _FakeServerError("temporarily unavailable")

    monkeypatch.setattr(extractor.client.chat.completions, "create", fake_create)

    with pytest.raises(RuntimeError, match="temporarily unavailable"):
        extractor._request_attributes([{"role": "user", "content": "x"}])

    # 3 transient retries on primary, then 3 on the fallback, both exhausted.
    assert calls == [extractor.model] * 3 + [extractor.fallback_model] * 3


def test_request_attributes_switches_to_fallback_after_mixed_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A transient error (retried) followed by a non-transient one still tries
    the fallback model rather than aborting."""
    extractor = StructuredExtractor(api_key="test-key")
    monkeypatch.setattr("app.services.extractor.time.sleep", lambda _seconds: None)
    calls: list[str] = []

    def fake_create(model: str, **kwargs: object) -> list[IndustrialAttribute]:
        calls.append(model)
        if len(calls) == 1:
            raise _FakeRateLimitError("burst limit")
        raise ValueError("schema validation failed")

    monkeypatch.setattr(extractor.client.chat.completions, "create", fake_create)

    with pytest.raises(RuntimeError, match="schema validation failed"):
        extractor._request_attributes([{"role": "user", "content": "x"}])

    # 1 transient retry on primary, then a non-transient error -> fallback tried.
    assert calls == [extractor.model, extractor.model, extractor.fallback_model]


def test_is_retryable_accepts_numeric_string_status() -> None:
    """String status codes (some clients report "429") are treated as retryable."""

    class _StringStatusError(Exception):
        status_code = "429"

    assert StructuredExtractor._is_retryable(_StringStatusError("boom")) is True
    assert StructuredExtractor._is_retryable(ValueError("schema")) is False
