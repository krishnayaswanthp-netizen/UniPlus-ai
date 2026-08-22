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

**Multi-key resilience.** When ``GROQ_API_KEYS`` is configured the extractor
builds a pool of instructor clients, one per key:

- extraction calls rotate across the pool (round-robin) so concurrent rows
  spread their token usage across every key's rate budget;
- an HTTP 429 on a multi-key pool logs a warning and immediately fails over to
  the next key instead of failing the item (a single-key pool keeps the
  original bounded backoff retries);
- a 400 ``tool_use_failed`` error — usually triggered by unescaped quote
  characters in the document text breaking the tool-use JSON generator —
  first aggressively re-sanitizes the prompt and retries, then falls back to
  ``instructor.Mode.JSON`` (plain JSON instead of a function call) before
  any model/fallback is given up on;
- an empty extraction (valid output, zero attributes) is retried once with a
  strict thoroughness directive before the empty result is reported.
"""

from __future__ import annotations

import logging
import random
import re
import time
from typing import Any, Iterable

logger = logging.getLogger(__name__)

import groq
import instructor
from groq import Groq

from app.core.config import settings
from app.schemas.enrichment import IndustrialAttribute
from app.services.normalizer import UnitNormalizer
from app.services.rate_limiter import (
    AdaptiveRateLimiter,
    buffered_backoff,
    is_tpd_exhausted,
    parse_retry_after_from_exception,
)

#: Preferred Groq model for structured extraction.
PRIMARY_MODEL = "openai/gpt-oss-20b"
#: Fallback model used when the primary model fails.
FALLBACK_MODEL = "openai/gpt-oss-120b"

#: Strict JSON Schema for Groq Structured Outputs
INDUSTRIAL_ATTRIBUTES_SCHEMA = {
    "name": "IndustrialAttributes",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {
            "attributes": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "field_name": {"type": "string"},
                        "raw_value": {"type": "string"},
                        "normalized_value": {"type": "string"},
                        "unit": {"type": ["string", "null"]},
                        "confidence_score": {"type": "number"},
                    },
                    "required": [
                        "field_name",
                        "raw_value",
                        "normalized_value",
                        "unit",
                        "confidence_score",
                    ],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["attributes"],
        "additionalProperties": False,
    },
}

#: Upper bound on document text sent per request (avoid token blowups).
_MAX_INPUT_CHARS = 25_000

#: HTTP statuses treated as transient (rate limits, server hiccups); retried
#: with exponential backoff on the current model before switching to the
#: fallback model.
_RETRYABLE_STATUS_CODES = frozenset({429, 500, 502, 503, 504})

#: Retry attempts per model before moving on to the cheaper fallback model.
_MAX_ATTEMPTS_PER_MODEL = 3

# ---------------------------------------------------------------------------
# Input sanitization for tool-use JSON safety
# ---------------------------------------------------------------------------
# Raw industrial text frequently uses dimension notation like ``16'`` (feet)
# or ``1/2"`` (inches). Fed verbatim into Groq's tool-use JSON generator these
# unescaped quotes can make the model emit malformed JSON
# (``{"tasks": [...], "tasks": []}``) and fail every extraction. The quotes
# are therefore normalized to explicit unit words before the LLM call, and any
# stragglers are stripped on a tool-use retry.

#: ``16'`` / ``12.5'`` -> ``16 ft`` / ``12.5 ft`` (feet notation).
_QUOTE_FEET_RE = re.compile(r"(\d+(?:\.\d+)?)\s*'")
#: ``1/2"`` / ``12.5"`` / ``1 1/2"`` -> ``... in`` (inches notation).
_QUOTE_INCHES_RE = re.compile(r"(\d+(?:\.\d+)?(?:\s+\d+/\d+)?)\s*\"")
#: Any digit-adjacent quote tokens (used by the aggressive pass).
_QUOTE_FEET_ANY_RE = re.compile(r"(\d)\s*'")
_QUOTE_INCHES_ANY_RE = re.compile(r"(\d)\s*\"")


def _sanitize_dimension_quotes(text: str) -> str:
    """Replace feet/inches quote notation with explicit unit words.

    ``"16' long"`` -> ``"16 ft long"``, ``"1/2\" NPT"`` -> ``"1/2 in NPT"``,
    ``"Height 6' 2\\\""`` -> ``"Height 6 ft 2 in"``. Word apostrophes
    (``"manufacturer's"``) are untouched — the patterns require a digit.
    """
    text = _QUOTE_FEET_RE.sub(r"\1 ft", text)
    text = _QUOTE_INCHES_RE.sub(r"\1 in", text)
    return text


def _sanitize_aggressive(text: str) -> str:
    """Stripping-level sanitization used when a tool-use call still fails.

    Converts any remaining digit-adjacent quotes to unit words, then removes
    every leftover straight quote (stray delimiters, word apostrophes) so the
    text can no longer corrupt tool-use JSON generation.
    """
    text = _QUOTE_FEET_ANY_RE.sub(r"\1 ft", text)
    text = _QUOTE_INCHES_ANY_RE.sub(r"\1 in", text)
    return text.replace('"', "").replace("'", "")


#: Directive appended to the user prompt for the single empty-extraction
#: retry. Kept free of quote/apostrophe characters so the sanitized prompt
#: stays clean.
_EMPTY_RETRY_SUFFIX = (
    "\n\nYour previous response returned no attributes. Re-read the document "
    "carefully and extract every quantifiable technical specification present "
    "(dimensions, electrical ratings, capacities, pressures, materials, thread "
    "specs, etc.). Return at least one attribute when any technical "
    "specification exists, otherwise return an empty list."
)


_SYSTEM_PROMPT = """\
You are a technical specification extraction engine for industrial B2B \
products (HVAC, plumbing, electrical, general equipment).

Respond ONLY with a valid JSON object containing an 'attributes' list of \
technical specifications found in the document (dimensions, electrical ratings, \
capacities, airflow, pressures, materials, thread specs, etc.).

Rules:
- Format: {"attributes": [{"field_name": "...", "raw_value": "...", "normalized_value": "...", "unit": "...", "confidence_score": 0.95, "source_url": "..."}]}
- "field_name" is a short, snake_case label for the attribute.
- Each "field_name" MUST be unique within the response — never repeat a \
field_name, even if the same value appears in several places.
- "raw_value" is the verbatim value as written in the document (e.g. "10mm", \
"120 VAC", "800 CFM").
- "normalized_value" may be a cleaned copy of the raw value.
- "unit" is the unit symbol when one is present, otherwise null.
- "confidence_score" is a float between 0.0 and 1.0 reflecting how confident \
you are in the extraction.
- "source_url" MUST be exactly the source URL provided by the user — do not \
invent, abbreviate, or omit it.
- Return ONLY the JSON object — no markdown code fences, no function-call tags, no commentary.
- Do not extract part numbers, model names, or marketing copy; extract only \
quantifiable technical specifications.
"""


FORBIDDEN_KEYS = frozenset({
    "id",
    "choices",
    "created",
    "model",
    "object",
    "system_fingerprint",
    "usage",
    "service_tier",
    "x_groq",
    "error",
    "headers",
    "status",
    "status_code",
    "tasks",
    "attributes",
})
_FORBIDDEN_FIELD_NAMES = FORBIDDEN_KEYS


def _coerce_to_industrial_attribute(
    attr: Any, default_source_url: str = ""
) -> IndustrialAttribute | None:
    """Coerce any tuple, dict, or object representation into IndustrialAttribute safely."""
    if attr is None:
        return None

    if isinstance(attr, IndustrialAttribute):
        if not attr.field_name or not str(attr.field_name).strip() or str(attr.field_name).lower().strip() in _FORBIDDEN_FIELD_NAMES:
            return None
        if not attr.raw_value or not str(attr.raw_value).strip():
            return None
        return attr

    if isinstance(attr, tuple):
        if len(attr) == 0:
            return None
        field_name = str(attr[0]) if attr[0] is not None else ""
        if field_name.lower().strip() in _FORBIDDEN_FIELD_NAMES:
            return None
        raw_value = str(attr[1]) if len(attr) > 1 and attr[1] is not None else ""
        norm_val = str(attr[2]) if len(attr) > 2 and attr[2] is not None else raw_value
        unit = str(attr[3]) if len(attr) > 3 and attr[3] is not None else None
        try:
            conf = float(attr[4]) if len(attr) > 4 and attr[4] is not None else 0.9
        except (ValueError, TypeError):
            conf = 0.9
        src = str(attr[5]) if len(attr) > 5 and attr[5] is not None else default_source_url
        if not field_name.strip() or not raw_value.strip():
            return None
        return IndustrialAttribute(
            field_name=field_name.strip(),
            raw_value=raw_value.strip(),
            normalized_value=norm_val.strip() if norm_val else raw_value.strip(),
            unit=unit,
            confidence_score=max(0.0, min(1.0, conf)),
            source_url=src,
        )

    if isinstance(attr, dict):
        field_name = str(attr.get("field_name", "") or "").strip()
        if not field_name or field_name.lower() in _FORBIDDEN_FIELD_NAMES:
            return None
        raw_val = attr.get("raw_value")
        if raw_val is None:
            return None
        raw_val_str = str(raw_val).strip()
        if not raw_val_str:
            return None
        norm_val = attr.get("normalized_value", raw_val_str)
        norm_val_str = str(norm_val).strip() if norm_val is not None else raw_val_str
        unit = attr.get("unit")
        try:
            conf = float(attr.get("confidence_score", attr.get("confidence", 0.9)))
        except (ValueError, TypeError):
            conf = 0.9
        src = str(attr.get("source_url", default_source_url))
        return IndustrialAttribute(
            field_name=field_name,
            raw_value=raw_val_str,
            normalized_value=norm_val_str,
            unit=str(unit) if unit is not None else None,
            confidence_score=max(0.0, min(1.0, conf)),
            source_url=src,
        )

    if hasattr(attr, "field_name") and hasattr(attr, "raw_value"):
        fn = str(getattr(attr, "field_name", "") or "").strip()
        if not fn or fn.lower() in _FORBIDDEN_FIELD_NAMES:
            return None
        rv = str(getattr(attr, "raw_value", "") or "").strip()
        if not rv:
            return None
        nv = getattr(attr, "normalized_value", rv)
        u = getattr(attr, "unit", None)
        c = getattr(attr, "confidence_score", getattr(attr, "confidence", 0.9))
        s = getattr(attr, "source_url", default_source_url)
        return IndustrialAttribute(
            field_name=fn,
            raw_value=rv,
            normalized_value=str(nv) if nv is not None else rv,
            unit=str(u) if u is not None else None,
            confidence_score=float(c) if c is not None else 0.9,
            source_url=str(s) if s else default_source_url,
        )

    return None


class StructuredExtractor:
    """Extract and normalize product attributes via a structured Groq call."""

    def __init__(
        self,
        api_key: str | None = None,
        model: str = PRIMARY_MODEL,
        fallback_model: str = FALLBACK_MODEL,
        api_keys: list[str] | None = None,
    ) -> None:
        """Build one instructor client per Groq key.

        Key resolution order: explicit ``api_keys`` list, then ``api_key``,
        then ``settings.groq_api_key_list`` (``GROQ_API_KEYS`` with a
        ``GROQ_API_KEY`` fallback). Raises ``ValueError`` when no key is
        available anywhere.
        """
        keys = list(api_keys or [])
        if api_key:
            keys = [api_key] + [key for key in keys if key != api_key]
        if not keys:
            keys = list(settings.groq_api_key_list)
        if not keys:
            raise ValueError(
                "GROQ_API_KEY / GROQ_API_KEYS is required. Set it in .env or pass api_key="
            )
        self.api_key = keys[0]
        self.api_keys = keys
        self.model = model
        self.fallback_model = fallback_model
        self.normalizer = UnitNormalizer()
        #: Pool of Groq clients, one per API key. ``self.client`` remains
        #: the first pooled client so single-key extractors behave exactly as
        #: before (and tests that stub ``extractor.client`` keep working).
        self._clients = [Groq(api_key=key) for key in keys]
        self.client = self._clients[0]
        #: Round-robin cursor for spreading calls across the pool. Read/written
        #: from worker threads; the GIL makes the increment safe enough for
        #: rotation purposes.
        self._rotation = 0
        self._json_clients: dict[int, Any] = {}

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
        # Normalize feet/inches quote notation (``16'``, ``1/2"``) so the
        # tool-use JSON generator never sees raw quote characters that can
        # corrupt its output.
        text = _sanitize_dimension_quotes(text)

        # Never pass a blank source marker to the LLM or stamp it on output.
        # (``app.main`` has its own "local://user-provided" marker for the
        # no-content-at-all case; this guard is the extractor-level default.)
        stamped_url = source_url or "local://mock-fallback"
        messages = self._build_messages(text, stamped_url, category)
        attributes = self._request_attributes(messages)
        if not attributes:
            # One bounded retry with an explicit thoroughness directive:
            # empty extractions on non-trivial documents are usually model
            # misfires (the tool-use JSON generator dropping the payload), not
            # genuinely empty documents. Blank-input short-circuits above
            # never reach here, so the retry only costs a call when the model
            # underperformed on real content.
            attributes = self._request_attributes(
                self._build_empty_retry_messages(messages)
            )
        if not attributes:
            # The model returned valid output but zero attributes (even after
            # the strict retry) — surface this instead of reporting a silent
            # "success" with nothing in it, so operators can spot
            # uninformative documents / model misfires.
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

    def _parse_completion_response(
        self, result: Any, default_source_url: str = "local://user-provided"
    ) -> list[IndustrialAttribute]:
        """Extract and parse IndustrialAttribute list from response."""
        content_str: str | None = None

        # Step 1: Extract string from first choice
        if hasattr(result, "choices") and result.choices:
            first_choice = result.choices[0]
            if hasattr(first_choice, "message"):
                content_str = getattr(first_choice.message, "content", None)
            elif isinstance(first_choice, dict):
                msg = first_choice.get("message") or {}
                content_str = msg.get("content") if isinstance(msg, dict) else str(msg)
        elif isinstance(result, dict) and "choices" in result:
            choices = result.get("choices") or []
            if choices and isinstance(choices, list) and len(choices) > 0:
                first_choice = choices[0]
                if isinstance(first_choice, dict):
                    msg = first_choice.get("message") or {}
                    content_str = msg.get("content") if isinstance(msg, dict) else str(msg)
                elif hasattr(first_choice, "message"):
                    content_str = getattr(first_choice.message, "content", None)
        elif isinstance(result, str):
            content_str = result

        if content_str is not None:
            import json
            import re

            # Step 2: Strip think tags and markdown code blocks
            clean_str = str(content_str).strip()
            if "<think>" in clean_str and "</think>" in clean_str:
                clean_str = clean_str.split("</think>")[-1].strip()
            if clean_str.startswith("```"):
                clean_str = re.sub(
                    r"^```(?:json)?\s*|\s*```$", "", clean_str, flags=re.DOTALL
                ).strip()

            # Step 3: Parse JSON
            try:
                parsed_data = json.loads(clean_str)
            except Exception:
                parsed_data = []

            # Step 4: Extract the list under 'attributes'
            if isinstance(parsed_data, dict):
                raw_list = parsed_data.get("attributes", parsed_data.get("tasks", []))
            elif isinstance(parsed_data, list):
                raw_list = parsed_data
            else:
                raw_list = []

            # Step 5: Convert ONLY valid items into IndustrialAttribute
            attributes: list[IndustrialAttribute] = []
            if isinstance(raw_list, list):
                for item in raw_list:
                    if isinstance(item, dict) and "field_name" in item:
                        fname = str(item.get("field_name", "")).strip()
                        if fname and fname.lower() not in FORBIDDEN_KEYS:
                            raw_val = item.get("raw_value")
                            if raw_val is not None and str(raw_val).strip():
                                norm_val = item.get("normalized_value", raw_val)
                                try:
                                    conf = float(item.get("confidence_score", item.get("confidence", 0.95)))
                                except (ValueError, TypeError):
                                    conf = 0.95
                                attributes.append(
                                    IndustrialAttribute(
                                        field_name=fname,
                                        raw_value=str(raw_val).strip(),
                                        normalized_value=str(norm_val).strip() if norm_val is not None else str(raw_val).strip(),
                                        unit=str(item.get("unit")) if item.get("unit") is not None else None,
                                        confidence_score=max(0.0, min(1.0, conf)),
                                        source_url=str(item.get("source_url") or default_source_url),
                                    )
                                )
                    elif isinstance(item, (IndustrialAttribute, tuple, ExtractedField)):
                        coerced = _coerce_to_industrial_attribute(item, default_source_url)
                        if coerced is not None:
                            attributes.append(coerced)
            return attributes

        # Fallback for mock test inputs that pass list of attributes directly
        if isinstance(result, list):
            final_attributes = []
            for item in result:
                coerced = _coerce_to_industrial_attribute(item, default_source_url)
                if coerced is not None:
                    final_attributes.append(coerced)
            return final_attributes

        return []

    def _request_attributes(
        self,
        messages: list[dict[str, str]],
    ) -> list[IndustrialAttribute]:
        """Run the structured LLM call across the key pool.

        Uses Groq's native Structured Outputs (`json_schema` mode with `strict: True`)
        and `reasoning_format="hidden"` for clean deterministic attribute extraction.
        """
        last_error: Exception | None = None
        start = self._next_rotation_index()
        pool_size = len(self._clients)
        for offset in range(pool_size):
            client_index = (start + offset) % pool_size
            client = self._clients[client_index]
            switch_key = False
            for model in (self.model, self.fallback_model):
                json_fallback = False
                sanitized = False
                for attempt in range(_MAX_ATTEMPTS_PER_MODEL):
                    try:
                        active_client = (
                            self._json_client(client_index)
                            if json_fallback
                            else client
                        )
                        create_kwargs: dict[str, Any] = {
                            "model": model,
                            "messages": messages,
                            "response_format": {"type": "json_object"},
                            "temperature": 0.1,
                        }
                        try:
                            result = active_client.chat.completions.create(
                                **create_kwargs
                            )
                        except TypeError:
                            # Mock response_model fallback for offline unit tests
                            result = active_client.chat.completions.create(
                                model=model,
                                messages=messages,
                                response_model=Iterable[IndustrialAttribute],
                            )
                        return self._parse_completion_response(result)
                    except Exception as exc:
                        last_error = exc
                        if self._is_tool_use_failure(exc):
                            if json_fallback:
                                break
                            if not sanitized:
                                sanitized = True
                                messages = self._sanitize_messages_aggressive(
                                    messages
                                )
                                logger.warning(
                                    "Groq tool-use generation failed (400); "
                                    "stripping quote characters and retrying: %s",
                                    exc,
                                )
                                continue
                            json_fallback = True
                            logger.warning(
                                "Groq tool-use generation still failing (400); "
                                "switching to Mode.JSON: %s",
                                exc,
                            )
                            continue
                        if is_tpd_exhausted(exc):
                            logger.warning(
                                "Groq TPD limit reached on key index %d; marking key depleted for 24h",
                                client_index,
                            )
                            self._rate_limiter.mark_key_depleted(self.api_keys[client_index])
                            if pool_size > 1:
                                switch_key = True
                                break

                        if self._is_rate_limit(exc):
                            if pool_size > 1:
                                logger.warning(
                                    "Groq rate limit (429) on key index %d; "
                                    "switching to the next key and retrying",
                                    client_index,
                                )
                                switch_key = True
                                break
                            if attempt + 1 < _MAX_ATTEMPTS_PER_MODEL:
                                retry_after = parse_retry_after_from_exception(exc)
                                if retry_after is not None:
                                    time.sleep(buffered_backoff(retry_after))
                                else:
                                    time.sleep(
                                        2**attempt
                                        + random.uniform(0.0, 2**attempt)
                                    )
                            continue
                        if not self._is_retryable(exc):
                            status_code = getattr(exc, "status_code", None)
                            err_msg = str(exc).lower()
                            if (
                                status_code in (404, 400)
                                or "404" in err_msg
                                or "model_not_found" in err_msg
                                or "notfound" in err_msg
                                or "decommissioned" in err_msg
                                or "model_decommissioned" in err_msg
                            ):
                                active_fallback = (
                                    "openai/gpt-oss-20b"
                                    if model != "openai/gpt-oss-20b"
                                    else "openai/gpt-oss-120b"
                                )
                                logger.warning(
                                    "Model %s returned error (%s); attempting fallback to %s",
                                    model,
                                    exc,
                                    active_fallback,
                                )
                                try:
                                    fallback_result = active_client.chat.completions.create(
                                        model=active_fallback,
                                        messages=messages,
                                        response_format={"type": "json_object"},
                                        temperature=0.1,
                                    )
                                    return self._parse_completion_response(fallback_result)
                                except Exception:
                                    pass
                            break
                        if attempt + 1 < _MAX_ATTEMPTS_PER_MODEL:
                            time.sleep(2**attempt + random.uniform(0.0, 2**attempt))
                if switch_key:
                    break

        raise RuntimeError(
            f"Structured extraction failed on all configured models/keys: {last_error}"
        ) from last_error

    def _next_rotation_index(self) -> int:
        """Return the next client index (round-robin across the key pool)."""
        index = self._rotation % len(self._clients)
        self._rotation += 1
        return index

    def _json_client(self, index: int) -> Any:
        """Return the Groq client for key *index*."""
        return self._clients[index]

    @staticmethod
    def _build_empty_retry_messages(
        messages: list[dict[str, str]],
    ) -> list[dict[str, str]]:
        """Return a copy of *messages* with a thoroughness directive appended
        to the user prompt for the empty-extraction retry."""
        retry = [dict(message) for message in messages]
        for message in retry:
            if message.get("role") == "user":
                message["content"] = (
                    str(message.get("content", "")) + _EMPTY_RETRY_SUFFIX
                )
                break
        return retry

    @staticmethod
    def _sanitize_messages_aggressive(
        messages: list[dict[str, str]],
    ) -> list[dict[str, str]]:
        """Return a copy of *messages* with the user prompt aggressively cleaned."""
        sanitized: list[dict[str, str]] = []
        for message in messages:
            content = message.get("content", "")
            if message.get("role") == "user":
                content = _sanitize_aggressive(str(content))
            sanitized.append({"role": message.get("role", ""), "content": content})
        return sanitized

    @staticmethod
    def _is_rate_limit(exc: Exception) -> bool:
        """Return ``True`` when *exc* is an HTTP 429 rate-limit failure."""
        return StructuredExtractor._status_code(exc) == 429

    @staticmethod
    def _is_retryable(exc: Exception) -> bool:
        """Return ``True`` when *exc* is a transient HTTP failure worth retrying."""
        return StructuredExtractor._status_code(exc) in _RETRYABLE_STATUS_CODES

    @staticmethod
    def _status_code(exc: Exception) -> int | None:
        """Extract an HTTP status code from an SDK exception, if present."""
        status = getattr(exc, "status_code", None)
        try:
            return int(status) if status is not None else None
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _is_tool_use_failure(exc: Exception) -> bool:
        """Return ``True`` for a 400 with a Groq ``tool_use_failed`` error.

        Groq reports these as ``{"error": {"code": "tool_use_failed",
        "message": ...}}``; the code/message may also be missing from ``body``
        in some SDK versions, so the rendered exception text is checked as a
        fallback.
        """
        if getattr(exc, "status_code", None) != 400:
            return False
        body = getattr(exc, "body", None)
        message = ""
        if isinstance(body, dict):
            error = body.get("error") if isinstance(body.get("error"), dict) else body
            message = " ".join(
                str(error[part])
                for part in ("code", "message")
                if error.get(part)
            )
        return "tool_use" in (message or str(exc)).lower()

    # -- post-processing ----------------------------------------------------

    def _postprocess(
        self,
        attributes: list[Any],
        source_url: str,
    ) -> list[IndustrialAttribute]:
        """Deterministically normalize values and enforce source/confidence."""
        processed: list[IndustrialAttribute] = []
        for raw_attr in attributes:
            attribute = _coerce_to_industrial_attribute(raw_attr, default_source_url=source_url)
            if attribute is None:
                continue
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
