"""Tests for environment-driven settings parsing (``app.core.config``).

Covers the ``groq_api_key_list`` property added for multi-key sharding:
comma-splitting, quote/whitespace stripping, the ``GROQ_API_KEYS`` ->
``GROQ_API_KEY`` fallback, and the empty/unset case. ``monkeypatch`` sets the
model fields directly (same approach as ``tests/conftest.py``), so the suite
never depends on a developer's local ``.env``.
"""

from __future__ import annotations

from app.core.config import settings


def test_groq_api_key_list_splits_and_strips(monkeypatch) -> None:
    """Comma-separated keys are split; quotes and padding are stripped."""
    monkeypatch.setattr(settings, "groq_api_keys", '" gsk_one , gsk_two "')
    monkeypatch.setattr(settings, "groq_api_key", None)
    assert settings.groq_api_key_list == ["gsk_one", "gsk_two"]


def test_groq_api_key_list_strips_quotes_around_entries(monkeypatch) -> None:
    """A quoted env value (each entry wrapped in double quotes) parses cleanly."""
    monkeypatch.setattr(settings, "groq_api_keys", '"gsk_one","gsk_two"')
    monkeypatch.setattr(settings, "groq_api_key", None)
    assert settings.groq_api_key_list == ["gsk_one", "gsk_two"]


def test_groq_api_key_list_prefers_multi_key_variable(monkeypatch) -> None:
    """``GROQ_API_KEYS`` wins over the legacy single ``GROQ_API_KEY``."""
    monkeypatch.setattr(settings, "groq_api_keys", "gsk_a,gsk_b")
    monkeypatch.setattr(settings, "groq_api_key", "gsk_legacy")
    assert settings.groq_api_key_list == ["gsk_a", "gsk_b"]


def test_groq_api_key_list_falls_back_to_single_key(monkeypatch) -> None:
    """An empty ``GROQ_API_KEYS`` gracefully falls back to ``GROQ_API_KEY``."""
    monkeypatch.setattr(settings, "groq_api_keys", None)
    monkeypatch.setattr(settings, "groq_api_key", "gsk_solo")
    assert settings.groq_api_key_list == ["gsk_solo"]


def test_groq_api_key_list_whitespace_only_multi_falls_back(monkeypatch) -> None:
    """A whitespace-only ``GROQ_API_KEYS`` counts as empty and falls back."""
    monkeypatch.setattr(settings, "groq_api_keys", "   ,  ")
    monkeypatch.setattr(settings, "groq_api_key", "gsk_solo")
    assert settings.groq_api_key_list == ["gsk_solo"]


def test_groq_api_key_list_empty_when_unset(monkeypatch) -> None:
    """No keys configured anywhere -> empty list (extractor raises later)."""
    monkeypatch.setattr(settings, "groq_api_keys", None)
    monkeypatch.setattr(settings, "groq_api_key", None)
    assert settings.groq_api_key_list == []
