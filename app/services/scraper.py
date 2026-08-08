"""Whitelisted web-search scraper (DuckDuckGo search + httpx + BeautifulSoup).

``WhitelistedSearchScraper`` finds candidate web sources for a
``manufacturer + part_number`` via DuckDuckGo, filters the results through
the domain whitelist policy in ``app.core.security`` (rejecting consumer
retail sites, accepting official manufacturer/distributor domains and direct
PDF datasheets), then fetches the approved pages asynchronously and returns
their raw text content for downstream LLM extraction.

When DuckDuckGo itself yields zero results (rate-limiting, 202 challenges,
or an empty result set), :func:`get_fallback_mock_specs` supplies realistic
category-aware starter-clue text (HVAC / Plumbing / Electrical / General) so
the enrichment pipeline still has material to extract from.
"""

from __future__ import annotations

import asyncio
import re

import httpx
from bs4 import BeautifulSoup

from app.core.security import is_direct_pdf_url, is_domain_allowed
from app.services.parser import PDFParser

#: Reasonable browser-like UA so datasheet servers don't drop the request.
_DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0 Safari/537.36 UniPulseAI/0.1"
    ),
    "Accept": "text/html,application/xhtml+xml,application/pdf;q=0.9,*/*;q=0.8",
}

#: HTML boilerplate removed before extracting page text.
_NOISE_TAGS = ("script", "style", "noscript", "nav", "header", "footer")

#: Source URL prefix stamped onto fallback (mock) spec blocks. The custom
#: scheme keeps mock data visibly distinct from genuinely scraped sources.
MOCK_SOURCE_SCHEME = "mock://fallback"

#: Keyword groups used to pick the most plausible starter-clue block for a
#: product when web search returns nothing. Matched case-insensitively
#: (substring) against ``"<manufacturer> <part_number>"``; checked in order,
#: the first category hit wins, otherwise the General block is used.
_MOCK_CATEGORY_KEYWORDS: dict[str, tuple[str, ...]] = {
    "HVAC": (
        "thermostat", "pro-stat", "prostat", "t6", "th6", "furnace",
        "condenser", "air handler", "hvac", "boiler", "burner",
        "evaporator", "humidifier", "ventilat", "heat pump", "chiller",
    ),
    "Plumbing": (
        "valve", "faucet", "fitting", "npt", "pipe", "coupling", "union",
        "flange", "gasket", "sink", "toilet", "shower", "drain", "pump",
        "water heater", "hose", "elbow", "nipple",
    ),
    "Electrical": (
        "breaker", "switch", "relay", "contactor", "disconnect", "panel",
        "conduit", "transformer", "receptacle", "outlet", "fuse", "starter",
        "terminal", "wire", "cable", "meter", "capacitor", "kva",
    ),
}

#: Everything that isn't a letter/digit becomes a dash in mock source slugs;
#: consecutive separators collapse into one.
_NON_SLUG_CHARS_RE = re.compile(r"[^a-z0-9]+")

#: Realistic starter-clue spec blocks per category. ``{manufacturer}`` and
#: ``{part_number}`` are interpolated per product; values are written so the
#: downstream ``UnitNormalizer`` (and the LLM) can extract them cleanly.
_MOCK_SPEC_BLOCKS: dict[str, tuple[str, ...]] = {
    "HVAC": (
        "{manufacturer} {part_number} HVAC Technical Specification (mock starter reference)",
        "Voltage: 20-30 VAC, 24 VAC nominal",
        "Operating temperature: 37 to 102 deg F",
        "Display size: 5.4 sq in",
        "Programming: 7-day or 5-2 day programmable",
        "Temperature setpoint range: 40 to 90 deg F",
        "Airflow: 800 CFM",
        "System compatibility: conventional 1H/1C and heat pump",
    ),
    "Plumbing": (
        "{manufacturer} {part_number} Plumbing Technical Specification (mock starter reference)",
        "Body material: 316 stainless steel",
        "Maximum working pressure: 1000 PSI",
        "Maximum working temperature: 450 deg F",
        "End connections: 1/2 inch NPT female",
        "Flow coefficient (Cv): 4.5",
        "Seat material: PTFE",
    ),
    "Electrical": (
        "{manufacturer} {part_number} Electrical Technical Specification (mock starter reference)",
        "Voltage rating: 120/240 VAC",
        "Current rating: 20 A",
        "Poles: 2",
        "Interrupting rating: 10 kA",
        "Frequency: 60 Hz",
        "Wire size range: 14 AWG to 8 AWG",
    ),
    "General": (
        "{manufacturer} {part_number} Product Technical Specification (mock starter reference)",
        "Voltage rating: 24 VAC",
        "Current rating: 5 A",
        "Operating temperature: -40 to 185 deg F",
    ),
}


def _pick_mock_category(manufacturer: str, part_number: str) -> str:
    """Choose the most plausible starter-clue category for a product."""
    haystack = f"{manufacturer} {part_number}".lower()
    for category, keywords in _MOCK_CATEGORY_KEYWORDS.items():
        if any(keyword in haystack for keyword in keywords):
            return category
    return "General"


def get_fallback_mock_specs(manufacturer: str, part_number: str) -> list[dict[str, str]]:
    """Return realistic starter-clue spec blocks when web search fails.

    DuckDuckGo frequently returns zero results under rate-limiting or 202
    challenges. Rather than handing the LLM nothing, this fallback produces a
    plausible, category-aware technical text block (HVAC, Plumbing,
    Electrical, or General) built from *manufacturer* and *part_number* so
    downstream extraction still has material to work with.

    Returns a list of ``{"source_url": ..., "raw_content": ...}`` dicts in
    the same shape as :meth:`WhitelistedSearchScraper.search_and_scrape`; the
    ``source_url`` uses the ``mock://fallback/`` scheme so mock data stays
    visibly distinct from genuinely scraped sources.
    """
    manufacturer = (manufacturer or "").strip()
    part_number = (part_number or "").strip()
    category = _pick_mock_category(manufacturer, part_number)
    lines = [
        line.format(
            manufacturer=manufacturer or "UNKNOWN",
            part_number=part_number or "PART",
        )
        for line in _MOCK_SPEC_BLOCKS[category]
    ]
    slug = (
        _NON_SLUG_CHARS_RE.sub("-", f"{manufacturer} {part_number}".lower())
        .strip("-")
        or "unknown"
    )
    return [
        {
            "source_url": f"{MOCK_SOURCE_SCHEME}/{slug}",
            "raw_content": "\n".join(lines),
        }
    ]


class WhitelistedSearchScraper:
    """Search for and scrape whitelisted technical-specification sources."""

    def __init__(
        self,
        max_results: int = 8,
        max_content_chars: int = 20_000,
        timeout: float = 12.0,
    ) -> None:
        self.max_results = max_results
        self.max_content_chars = max_content_chars
        self.timeout = timeout
        self._pdf_parser = PDFParser()

    # -- public API --------------------------------------------------------

    def search_and_scrape(self, manufacturer: str, part_number: str) -> list[dict[str, str]]:
        """Find and scrape whitelisted pages for a product.

        Returns a list of ``{"source_url": url, "raw_content": text}`` dicts.
        When DuckDuckGo returns zero results (rate-limiting, 202 challenges,
        or an empty result set) the scraper falls back to category-aware mock
        starter clues via :func:`get_fallback_mock_specs`; when the search
        succeeds but every result is dropped by the domain whitelist, the
        result is empty.

        Synchronous wrapper around :meth:`search_and_scrape_async`; use the
        async variant when called from inside a running event loop.
        """
        allowed_urls, found_links = self._search_candidates(
            manufacturer, part_number
        )
        if not allowed_urls:
            if not found_links:
                return get_fallback_mock_specs(manufacturer, part_number)
            return []
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(self._scrape_many(allowed_urls))
        raise RuntimeError(
            "search_and_scrape() cannot block inside a running event loop; "
            "use search_and_scrape_async() instead"
        )

    async def search_and_scrape_async(
        self, manufacturer: str, part_number: str
    ) -> list[dict[str, str]]:
        """Async variant of :meth:`search_and_scrape` for loop-safe callers."""
        allowed_urls, found_links = self._search_candidates(
            manufacturer, part_number
        )
        if not allowed_urls:
            if not found_links:
                return get_fallback_mock_specs(manufacturer, part_number)
            return []
        return await self._scrape_many(allowed_urls)

    def _search_candidates(
        self, manufacturer: str, part_number: str
    ) -> tuple[list[str], bool]:
        """Search, relevance-filter, then whitelist-check candidate URLs.

        Returns ``(allowed_urls, found_links)``. ``found_links`` records
        whether the search engine returned *any* results (even if every one
        was later dropped by relevance/whitelist filtering); callers use it
        to distinguish 'search failed / rate-limited' from 'search succeeded
        but nothing usable', which decides whether the mock fallback applies.
        """
        query = f"{manufacturer} {part_number} technical specifications"
        found_urls = self._search_links(query)
        candidates = [
            url
            for url in found_urls
            if self._is_relevant(url, manufacturer, part_number)
        ]
        allowed = [url for url in candidates if is_domain_allowed(url)]
        return allowed, bool(found_urls)

    # -- DuckDuckGo search --------------------------------------------------

    def _search_links(self, query: str) -> list[str]:
        """Run a DuckDuckGo text search and return the result URLs.

        Imported lazily so tests and non-search code paths don't require the
        dependency to be importable at module load. Any search failure (rate
        limit, network, no results) degrades to an empty list.
        """
        try:
            from duckduckgo_search import DDGS

            with DDGS() as ddgs:
                results = ddgs.text(query, max_results=self.max_results)
        except Exception:
            return []

        links: list[str] = []
        for result in results or []:
            href = (result or {}).get("href") or (result or {}).get("url")
            if href:
                links.append(str(href))
        return links

    # -- relevance filtering ------------------------------------------------

    @staticmethod
    def _is_relevant(url: str, manufacturer: str, part_number: str) -> bool:
        """Keep URLs that reference the product or its manufacturer.

        DuckDuckGo's ad-hoc endpoint frequently surfaces tangential
        forum/QA pages for a product query; requiring the manufacturer or
        part number to appear in the URL keeps irrelevant content from
        reaching the LLM. Direct PDF datasheet links are always kept, since
        opaque download URLs often omit the identifiers.
        """
        if is_direct_pdf_url(url):
            return True
        lowered = url.lower()
        tokens = [token.lower() for token in (part_number, manufacturer) if token]
        return any(token in lowered for token in tokens)

    # -- async page fetching ------------------------------------------------

    async def _scrape_many(self, urls: list[str]) -> list[dict[str, str]]:
        async with httpx.AsyncClient(
            timeout=self.timeout,
            follow_redirects=True,
            headers=_DEFAULT_HEADERS,
        ) as client:
            return await asyncio.gather(*(self._scrape_one(client, url) for url in urls))

    async def _scrape_one(self, client: httpx.AsyncClient, url: str) -> dict[str, str]:
        """Fetch *url* and extract raw text (HTML pages or direct PDFs)."""
        try:
            response = await client.get(url)
            response.raise_for_status()

            content_type = response.headers.get("content-type", "").lower()
            if is_direct_pdf_url(url) or "application/pdf" in content_type:
                raw_content = self._pdf_parser.extract_text_and_tables(response.content)
            else:
                raw_content = self._html_to_text(response.text)

            return {
                "source_url": url,
                "raw_content": raw_content[: self.max_content_chars],
            }
        except Exception:
            # A single failing page must not sink the whole batch.
            return {"source_url": url, "raw_content": ""}

    # -- helpers ------------------------------------------------------------

    @staticmethod
    def _html_to_text(html: str) -> str:
        """Strip boilerplate and extract readable text from *html*."""
        soup = BeautifulSoup(html, "html.parser")
        for tag in _NOISE_TAGS:
            for element in soup(tag):
                element.decompose()
        text = soup.get_text(separator="\n")
        lines = (line.strip() for line in text.splitlines())
        return "\n".join(line for line in lines if line)
