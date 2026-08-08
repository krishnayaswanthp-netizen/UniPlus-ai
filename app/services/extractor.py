"""LLM-backed structured extraction (instructor + Groq).

``StructuredExtractor`` sends raw product-document text to a Groq-hosted LLM
wrapped by ``instructor`` with the ``IndustrialAttribute`` list response model,
forcing the model to return structured attributes. Every returned attribute is
then wired through ``UnitNormalizer.normalize_field`` so values and units are
deterministically canonicalized, and the exact ``source_url`` is stamped onto
each attribute regardless of what the model produced.

The extractor treats all document text alike — scraped pages, uploaded PDFs,
user descriptions, and the scraper's mock fallback blocks (used when
DuckDuckGo yields zero results) — normalizing it before the LLM call and
short-circuiting on blank input.
"""

from __future__ import annotations

from typing import Iterable

import instructor
from groq import Groq

from app.core.config import settings
from app.schemas.enrichment import IndustrialAttribute
from app.services.normalizer import UnitNormalizer

#: Preferred Groq model for structured extraction.
PRIMARY_MODEL = "llama-3.3-70b-versatile"
#: Cheaper/faster fallback used when the primary model fails.
FALLBACK_MODEL = "llama-3.1-8b-instant"

#: Upper bound on document text sent per request (avoid token blowups).
_MAX_INPUT_CHARS = 25_000

_SYSTEM_PROMPT = """\
You are a technical specification extraction engine for industrial B2B \
products (HVAC, plumbing, electrical, general equipment).

Given a category, a source URL, and a product document, extract every \
technical specification you can find (dimensions, electrical ratings, \
capacities, airflow, pressures, materials, thread specs, etc.) as a list of \
IndustrialAttribute objects.

Rules:
- "field_name" is a short, snake_case label for the attribute.
- "raw_value" is the verbatim value as written in the document (e.g. "10mm", \
"120 VAC", "800 CFM").
- "normalized_value" may be a cleaned copy of the raw value.
- "unit" is the unit symbol when one is present, otherwise null.
- "confidence_score" is a float between 0.0 and 1.0 reflecting how confident \
you are in the extraction.
- "source_url" MUST be exactly the source URL provided by the user — do not \
invent, abbreviate, or omit it.
- Do not extract part numbers, model names, or marketing copy; extract only \
quantifiable technical specifications.
"""


class StructuredExtractor:
    """Extract and normalize product attributes via a structured Groq call."""

    def __init__(
        self,
        api_key: str | None = None,
        model: str = PRIMARY_MODEL,
        fallback_model: str = FALLBACK_MODEL,
    ) -> None:
        self.api_key = api_key or settings.groq_api_key
        if not self.api_key:
            raise ValueError(
                "GROQ_API_KEY is required. Set it in .env or pass api_key="
            )
        self.model = model
        self.fallback_model = fallback_model
        self.normalizer = UnitNormalizer()
        self.client = instructor.from_groq(
            Groq(api_key=self.api_key),
            mode=instructor.Mode.TOOLS,
        )

    # -- public API --------------------------------------------------------

    def extract_product_specs(
        self,
        raw_text: str,
        source_url: str,
        category: str,
    ) -> list[IndustrialAttribute]:
        """Extract structured attributes from *raw_text*.

        *raw_text* may come from scraped pages, uploaded PDFs, user-provided
        descriptions, or mock fallback text produced by the scraper when web
        search returns nothing — all are normalized the same way before the
        LLM call, and blank/whitespace-only input short-circuits to ``[]``.

        The returned attributes are guaranteed to carry *source_url* verbatim
        (or ``local://mock-fallback`` when it is blank), a clamped
        ``confidence_score`` in ``[0.0, 1.0]``, and a ``normalized_value``/
        ``unit`` pair computed by ``UnitNormalizer`` from ``raw_value``.
        """
        text = self._prepare_text(raw_text)
        if not text:
            return []

        # Never pass a blank source marker to the LLM or stamp it on output.
        # (``app.main`` has its own "local://user-provided" marker for the
        # no-content-at-all case; this guard is the extractor-level default.)
        stamped_url = source_url or "local://mock-fallback"
        messages = self._build_messages(text, stamped_url, category)
        attributes = self._request_attributes(messages)
        return self._postprocess(attributes, stamped_url)

    # -- text preparation --------------------------------------------------

    @staticmethod
    def _prepare_text(raw_text: str | None) -> str:
        """Normalize document text (incl. mock fallback blocks) for the LLM.

        Trims each line and drops blank lines so fallback text from
        ``get_fallback_mock_specs`` — and scraped page text — arrives at the
        model in a clean, compact form. Returns ``""`` for ``None``/blank
        input so callers short-circuit before any API call.
        """
        if not raw_text:
            return ""
        return "\n".join(
            line.strip() for line in raw_text.splitlines() if line.strip()
        )

    # -- request construction ----------------------------------------------

    def _build_messages(
        self,
        raw_text: str,
        source_url: str,
        category: str,
    ) -> list[dict[str, str]]:
        user_prompt = (
            f"Category: {category}\n"
            f"Source URL: {source_url}\n\n"
            f"Product document text:\n{raw_text[:_MAX_INPUT_CHARS]}"
        )
        return [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ]

    def _request_attributes(
        self,
        messages: list[dict[str, str]],
    ) -> list[IndustrialAttribute]:
        """Run the structured LLM call, falling back to a cheaper model."""
        last_error: Exception | None = None
        for model in (self.model, self.fallback_model):
            try:
                result = self.client.chat.completions.create(
                    model=model,
                    response_model=Iterable[IndustrialAttribute],
                    messages=messages,
                    max_retries=2,
                )
                return list(result)
            except Exception as exc:  # validation retries exhausted, API errors
                last_error = exc
                continue
        raise RuntimeError(
            "Structured extraction failed on all configured models"
        ) from last_error

    # -- post-processing ----------------------------------------------------

    def _postprocess(
        self,
        attributes: list[IndustrialAttribute],
        source_url: str,
    ) -> list[IndustrialAttribute]:
        """Deterministically normalize values and enforce source/confidence."""
        processed: list[IndustrialAttribute] = []
        for attribute in attributes:
            normalized_value, unit = self.normalizer.normalize_field(
                attribute.raw_value
            )
            processed.append(
                attribute.model_copy(
                    update={
                        "source_url": source_url,
                        "normalized_value": normalized_value,
                        "unit": unit,
                        "confidence_score": max(0.0, min(1.0, attribute.confidence_score)),
                    }
                )
            )
        return processed
