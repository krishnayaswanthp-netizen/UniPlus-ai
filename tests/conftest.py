"""Shared test fixtures.

The backend reads configuration from a local ``.env`` file at import time
(``app.core.config.settings``). A developer's real ``.env`` typically carries
``ALLOWED_DOMAINS`` and live API keys, which change pipeline behavior:

- ``ALLOWED_DOMAINS`` turns the domain whitelist into an *exclusive* allow-list
  (``test_manufacturer_domain_allowed``, ``test_direct_pdf_link_allowed`` and
  the scraper whitelist tests would fail because manufacturer domains are no
  longer allowed by default);
- a configured ``GROQ_API_KEY`` / ``GROQ_API_KEYS`` means
  ``StructuredExtractor()`` no longer raises
  (``test_structured_extractor_requires_api_key`` would fail) and the batch
  sharder would build real keyed extractors instead of the injected fake.

This autouse fixture pins those environment-sensitive settings back to their
test-time defaults so the suite is hermetic and passes regardless of what a
developer keeps in their local ``.env``.
"""

from __future__ import annotations

import pytest

from app.core.config import settings


@pytest.fixture(autouse=True)
def _neutralize_environment_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """Reset environment-sensitive settings to their clean-environment defaults."""
    monkeypatch.setattr(settings, "allowed_domains", [])
    monkeypatch.setattr(settings, "groq_api_key", None)
    monkeypatch.setattr(settings, "groq_api_keys", None)
    monkeypatch.setattr(settings, "openai_api_key", None)
