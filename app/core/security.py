"""Security utilities (domain validation, URL safety checks, etc.).

``is_domain_allowed`` implements the web-source whitelist policy used by
the enrichment pipeline: consumer retail marketplaces are always rejected,
official manufacturer/distributor websites and direct PDF datasheet links
are accepted, and an explicit allow-list (``ALLOWED_DOMAINS`` in ``.env``)
is enforced exclusively when configured.
"""

from __future__ import annotations

from urllib.parse import urlparse

from app.core.config import settings

#: Consumer retail marketplaces that must never be scraped as spec sources.
#: Matched with suffix semantics (``www.amazon.co.uk`` -> ``amazon.co.uk``),
#: so subdomains and country mirrors are covered too.
BLOCKED_RETAIL_DOMAINS: frozenset[str] = frozenset(
    {
        "amazon.com", "amazon.in", "amazon.co.uk", "amazon.de", "amazon.ca",
        "ebay.com", "ebay.in", "ebay.co.uk",
        "flipkart.com",
        "walmart.com", "walmart.ca",
        "aliexpress.com", "bestbuy.com", "homedepot.com", "lowes.com",
        "newegg.com", "target.com", "wayfair.com", "overstock.com",
    }
)

#: File suffix that marks a direct (downloadable) PDF datasheet.
_PDF_SUFFIX = ".pdf"

_URL_SCHEMES = ("https://", "http://")


def _extract_host(url: str) -> str | None:
    """Return the lowercased hostname of *url*, or ``None`` when malformed."""
    try:
        hostname = urlparse(url).hostname
    except ValueError:
        return None
    return hostname.lower() if hostname else None


def _normalize_domain(domain: str) -> str:
    """Lowercase *domain*, stripping any scheme and trailing slashes."""
    domain = domain.strip().lower()
    for scheme in _URL_SCHEMES:
        if domain.startswith(scheme):
            domain = domain[len(scheme):]
    return domain.rstrip("/")


def _matches_domain(host: str, domain: str) -> bool:
    """Return ``True`` when *host* is *domain* or any subdomain of it.

    Suffix matching (rather than a naive last-two-labels root extraction)
    correctly handles country-coded domains: ``www.amazon.co.uk`` matches
    ``amazon.co.uk``, whereas a root-domain heuristic would collapse it to
    ``co.uk`` and let it slip through.
    """
    domain = _normalize_domain(domain)
    if not domain:
        return False
    return host == domain or host.endswith("." + domain)


def _matches_any(host: str, domains: frozenset[str] | list[str]) -> bool:
    return any(_matches_domain(host, domain) for domain in domains)


def is_direct_pdf_url(url: str) -> bool:
    """Return ``True`` when *url* points directly at a ``.pdf`` file."""
    try:
        path = urlparse(url).path.lower()
    except ValueError:
        return False
    return path.endswith(_PDF_SUFFIX)


def is_domain_allowed(url: str) -> bool:
    """Enforce the web-source whitelist policy for *url*.

    Order of checks:

    1. Malformed URLs (no hostname) are rejected.
    2. Known consumer retail domains (and any subdomain of them) are always
       rejected.
    3. When ``settings.allowed_domains`` is configured it acts as an
       *exclusive* allow-list — the host must match one of its domains,
       regardless of file type.
    4. Otherwise (no allow-list configured) any non-retail domain passes,
       which lets official manufacturer/distributor websites and direct PDF
       datasheet links through by default.
    """
    host = _extract_host(url)
    if not host:
        return False

    if _matches_any(host, BLOCKED_RETAIL_DOMAINS):
        return False

    allowed_domains = settings.allowed_domains
    if allowed_domains:
        return _matches_any(host, allowed_domains)

    # Default-allow: only non-retail domains reach this point.
    return True