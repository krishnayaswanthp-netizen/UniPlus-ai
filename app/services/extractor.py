"""LLM-backed structured extraction (instructor + Groq).

``StructuredExtractor`` sends raw product-document text to a Groq-hosted LLM
wrapped by ``instructor`` with the ``IndustrialAttribute`` list response model,
forcing the model to return structured attributes. Every returned attribute is
then wired through ``UnitNormalizer.normalize_field`` so values and units are
deterministically canonicalized, and the exact ``source_url`` is stamped onto
each attribute regardless of what the model produced.

The extractor treats all document text alike — scraped pages, uploaded PDFs,
and user descriptions — normalizing it before the LLM call and
short-circuiting on blank input.
"""

from __future__ import annotations

import logging
import random
import time
from typing import Iterable

logger = logging.getLogger(__name__)

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

#: HTTP statuses treated as transient (rate limits, server hiccups); retried
#: with exponential backoff on the current model before switching to the
#: fallback model.
_RETRYABLE_STATUS_CODES = frozenset({429, 500, 502, 503, 504})

#: Retry attempts per model before moving on to the cheaper fallback model.
_MAX_ATTEMPTS_PER_MODEL = 3

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

        *raw_text* may come from scraped pages, uploaded PDFs, or
        user-provided descriptions — all are normalized the same way before
        the LLM call, and blank/whitespace-only input short-circuits to
        ``[]``.

        The returned attributes are guaranteed to carry *source_url* verbatim
        (or ``local://mock-fallback`` when it is blank), a clamped
        ``confidence_score`` in ``[0.0, 1.0]``, and a ``normalized_value``/
        ``unit`` pair computed by ``UnitNormalizer`` from ``raw_value``.

        When the model returns valid output but zero attributes (non-blank
        input), a warning is logged so empty extractions are never silently
        reported as successful.
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
        if not attributes:
            # The model returned valid output but zero attributes — surface
            # this instead of reporting a silent "success" with nothing in
            # it, so operators can spot uninformative documents / model
            # misfires. Blank-input short-circuits above never reach here.
            logger.warning(
                "Empty extraction for source_url=%r (category=%r): "
                "the model returned no attributes",
                stamped_url,
                category,
            )
        return self._postprocess(attributes, stamped_url)

    # -- text preparation --------------------------------------------------

    @staticmethod
    def _prepare_text(raw_text: str | None) -> str:
        """Normalize document text for the LLM.

        Trims each line and drops blank lines so scraped page text arrives at
        the model in a clean, compact form. Returns ``""`` for ``None``/blank
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
        if len(raw_text) > _MAX_INPUT_CHARS:
            # Documents longer than the token guard are truncated before the
            # LLM call; make that loss visible in the logs rather than
            # silently dropping the tail of the document.
            logger.warning(
                "Truncating input text for %r: %d chars exceeds the %d-char "
                "limit; the tail of the document will be dropped.",
                source_url,
                len(raw_text),
                _MAX_INPUT_CHARS,
            )
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
        """Run the structured LLM call, falling back to a cheaper model.

        Transient HTTP failures (429 rate limits, 5xx server errors) are
        retried on the current model with exponential backoff + jitter before
        the fallback model is tried; non-transient errors (schema validation,
        auth, etc.) switch models immediately. Backoff sleeps are safe here
        because the extractor always runs inside a worker thread
        (``asyncio.to_thread`` in ``app.main``), never on the event loop.
        """
        last_error: Exception | None = None
        for model in (self.model, self.fallback_model):
            for attempt in range(_MAX_ATTEMPTS_PER_MODEL):
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
                    if not self._is_retryable(exc):
                        # Not transient — move straight to the next model.
                        break
                    if attempt + 1 < _MAX_ATTEMPTS_PER_MODEL:
                        # Exponential backoff with proportional jitter so
                        # concurrent rows that hit a 429 don't retry in lockstep.
                        time.sleep(2**attempt + random.uniform(0.0, 2**attempt))
        raise RuntimeError(
            f"Structured extraction failed on all configured models: {last_error}"
        ) from last_error

    @staticmethod
    def _is_retryable(exc: Exception) -> bool:
        """Return ``True`` when *exc* is a transient HTTP failure worth retrying."""
        status = getattr(exc, "status_code", None)
        try:
            status = int(status) if status is not None else None
        except (TypeError, ValueError):
            status = None
        return status in _RETRYABLE_STATUS_CODES

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
                attribute.raw_value, field_name=attribute.field_name
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
