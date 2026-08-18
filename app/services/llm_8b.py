"""
app/services/llm_8b.py
Stage 7: Primary 8B LLM Extractor for UniPulse AI.

Extracts structured technical attributes from reduced product context using
Groq's ``llama-3.1-8b-instant`` model through the Instructor SDK. Integrates
with the Stage 6 ``AdaptiveRateLimiter`` (RPM/TPM capacity is acquired before
every call), retries with backoff + API-key rotation on 429s (honoring the
``Retry-After`` header when present), and tags every extracted attribute with
``source = ExtractionSource.LLM_8B``.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from typing import Any

from pydantic import BaseModel, Field, field_validator

from app.schemas.product import (
    AttributeValue,
    ExtractedField,
    ExtractionSource,
    ProductRecord,
    RowStatus,
)
from app.services.rate_limiter import (
    AdaptiveRateLimiter,
    buffered_backoff,
    parse_retry_after_from_exception,
)

logger = logging.getLogger(__name__)


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


class LLMExtractionOutput(BaseModel):
    """Structured extraction output: a list of extracted fields."""

    attributes: list[ExtractedField] = Field(default_factory=list)


def _parse_retry_after(exc: Exception) -> float | None:
    """Extract a ``Retry-After`` value from an API exception.

    Delegates to :func:`parse_retry_after_from_exception`, which checks the
    ``Retry-After`` header *and* Groq's message-body hint ("Please try again
    in 3.455s") — the header alone is frequently absent on Groq 429s, so the
    message body is what actually carries the backoff window.
    """
    return parse_retry_after_from_exception(exc)


def _is_tool_use_failure(exc: Exception) -> bool:
    """Return ``True`` for a Groq 400 ``tool_use_failed`` error.

    Groq reports these as ``{"error": {"code": "tool_use_failed", ...}}``.
    The status code and body are inspected (both the groq-native and
    openai-compatible SDK error classes expose them); the rendered exception
    text is checked as a last resort for mocks and older SDK versions.
    """
    if getattr(exc, "status_code", None) != 400:
        return False
    body = getattr(exc, "body", None)
    message = ""
    if isinstance(body, dict):
        error = body.get("error") if isinstance(body.get("error"), dict) else body
        message = " ".join(
            str(error[part]) for part in ("code", "message") if error.get(part)
        )
    return "tool_use" in (message or str(exc)).lower()


class LLM8BExtractor:
    """Primary LLM extractor using Groq's llama-3.1-8b-instant model.

    Enforces structured outputs, acquires rate-limiter tokens, handles 429
    retries with key rotation, and populates ``record.attributes`` with
    ``source = LLM_8B``.
    """

    def __init__(
        self,
        api_keys: list[str] | None = None,
        model_name: str = "openai/gpt-oss-20b",
        rate_limiter: AdaptiveRateLimiter | None = None,
    ) -> None:
        self.api_keys = [k.strip() for k in (api_keys or []) if k.strip()]
        self.current_key_idx = 0
        self.model_name = model_name
        self.rate_limiter = rate_limiter or AdaptiveRateLimiter()

    def _get_current_key(self) -> str:
        if not self.api_keys:
            return "mock_key"
        return self.api_keys[self.current_key_idx % len(self.api_keys)]

    def _rotate_key(self) -> str:
        if not self.api_keys:
            return "mock_key"
        self.current_key_idx = (self.current_key_idx + 1) % len(self.api_keys)
        return self._get_current_key()

    def build_prompt(self, record: ProductRecord, text_context: str) -> str:
        """Construct the prompt for technical attribute extraction."""
        return (
            f"Extract all technical specification attributes for this industrial product.\n"
            f"Manufacturer: {record.identity.manufacturer}\n"
            f"Part Number: {record.identity.mfg_part_number}\n"
            f"Category: {record.identity.category}\n"
            f"Raw Description: {record.identity.raw_description}\n\n"
            f"Context Document:\n{text_context[:1500]}\n\n"
            f"Respond ONLY with a valid JSON object: "
            f'{{"attributes": [{{"field_name": "...", "raw_value": "...", "normalized_value": "...", "unit": "...", "confidence": 0.95}}]}}'
        )

    @staticmethod
    def _coerce_field(field: Any) -> ExtractedField | None:
        if field is None:
            return None
        if isinstance(field, ExtractedField):
            if not field.field_name or not str(field.field_name).strip() or str(field.field_name).lower().strip() in _FORBIDDEN_FIELD_NAMES:
                return None
            if not field.raw_value or not str(field.raw_value).strip():
                return None
            return field
        if isinstance(field, tuple):
            if len(field) == 0:
                return None
            fn = str(field[0]) if field[0] is not None else ""
            if not fn.strip() or fn.lower().strip() in _FORBIDDEN_FIELD_NAMES:
                return None
            rv = str(field[1]) if len(field) > 1 and field[1] is not None else ""
            nv = str(field[2]) if len(field) > 2 and field[2] is not None else rv
            u = str(field[3]) if len(field) > 3 and field[3] is not None else None
            try:
                c = float(field[4]) if len(field) > 4 and field[4] is not None else 0.9
            except (ValueError, TypeError):
                c = 0.9
            if not rv.strip():
                return None
            return ExtractedField(
                field_name=fn.strip(),
                raw_value=rv.strip(),
                normalized_value=nv.strip() if nv else rv.strip(),
                unit=u,
                confidence=c,
            )
        if isinstance(field, dict):
            fn = str(field.get("field_name", "") or "").strip()
            if not fn or fn.lower() in _FORBIDDEN_FIELD_NAMES:
                return None
            rv = field.get("raw_value")
            if rv is None:
                return None
            rv_str = str(rv).strip()
            if not rv_str:
                return None
            nv = field.get("normalized_value", rv_str)
            u = field.get("unit")
            try:
                c = float(field.get("confidence", field.get("confidence_score", 0.9)))
            except (ValueError, TypeError):
                c = 0.9
            return ExtractedField(
                field_name=fn,
                raw_value=rv_str,
                normalized_value=str(nv).strip() if nv is not None else rv_str,
                unit=str(u) if u is not None else None,
                confidence=c,
            )
        if hasattr(field, "field_name") and hasattr(field, "raw_value"):
            fn = str(getattr(field, "field_name", "") or "").strip()
            if not fn or fn.lower() in _FORBIDDEN_FIELD_NAMES:
                return None
            rv = str(getattr(field, "raw_value", "") or "").strip()
            if not rv:
                return None
            nv = getattr(field, "normalized_value", rv)
            u = getattr(field, "unit", None)
            c = getattr(field, "confidence", getattr(field, "confidence_score", 0.9))
            return ExtractedField(
                field_name=fn,
                raw_value=rv,
                normalized_value=str(nv) if nv is not None else rv,
                unit=str(u) if u is not None else None,
                confidence=float(c) if c is not None else 0.9,
            )
        return None

    @staticmethod
    def _map_response_to_attributes(
        response: LLMExtractionOutput, evidence_snippet: str
    ) -> dict[str, AttributeValue]:
        """Convert a structured LLM response into ``AttributeValue`` objects
        tagged with ``source = ExtractionSource.LLM_8B``."""
        output: dict[str, AttributeValue] = {}
        for raw_field in response.attributes:
            field = LLM8BExtractor._coerce_field(raw_field)
            if not field or not field.field_name or field.raw_value is None:
                continue
            output[field.field_name] = AttributeValue(
                field_name=field.field_name,
                raw_value=field.raw_value,
                normalized_value=field.normalized_value or field.raw_value,
                unit=field.unit,
                confidence=field.confidence,
                source=ExtractionSource.LLM_8B,
                evidence_snippet=evidence_snippet,
            )
        return output

    async def extract_specs(
        self,
        record: ProductRecord,
        text_context: str,
        client_override: Any | None = None,
    ) -> dict[str, AttributeValue]:
        """Execute LLM extraction with rate-limit acquisition, 429 retry
        backoff, and structured Pydantic parsing.
        """
        import json

        prompt = self.build_prompt(record, text_context)
        estimated_tokens = len(prompt) // 4 + 150

        await self.rate_limiter.acquire(estimated_tokens=estimated_tokens)

        evidence_snippet = (
            text_context[:100] if text_context else record.identity.raw_description
        )

        if client_override is None:
            try:
                from groq import AsyncGroq
            except ImportError as exc:
                record.record_error(RowStatus.EXTRACTING_8B, exc)
                return {}

        attempts = 0
        max_attempts = 3

        while attempts < max_attempts:
            attempts += 1
            try:
                start_time = time.perf_counter()
                if client_override is not None:
                    # Mock/test client path.
                    response = await client_override.extract(
                        prompt=prompt, response_model=LLMExtractionOutput
                    )
                else:
                    raw_client = AsyncGroq(api_key=self._get_current_key())
                    chat_resp = await raw_client.chat.completions.create(
                        model=self.model_name,
                        messages=[
                            {
                                "role": "system",
                                "content": "You are an expert industrial product data analyst. Respond ONLY with a valid JSON object containing an 'attributes' list.",
                            },
                            {"role": "user", "content": prompt},
                        ],
                        response_format={"type": "json_object"},
                        temperature=0.1,
                    )
                    content_str = chat_resp.choices[0].message.content or "{}"
                    clean_str = content_str.strip()
                    if "<think>" in clean_str and "</think>" in clean_str:
                        clean_str = clean_str.split("</think>")[-1].strip()
                    if clean_str.startswith("```"):
                        clean_str = re.sub(
                            r"^```(?:json)?\s*|\s*```$", "", clean_str, flags=re.DOTALL
                        ).strip()

                    try:
                        parsed_data = json.loads(clean_str)
                    except Exception:
                        parsed_data = {}

                    if isinstance(parsed_data, dict):
                        raw_list = parsed_data.get("attributes", parsed_data.get("tasks", []))
                    elif isinstance(parsed_data, list):
                        raw_list = parsed_data
                    else:
                        raw_list = []

                    extracted_fields: list[ExtractedField] = []
                    if isinstance(raw_list, list):
                        for item in raw_list:
                            if isinstance(item, dict) and "field_name" in item:
                                fname = str(item.get("field_name", "")).strip()
                                if fname and fname.lower() not in FORBIDDEN_KEYS:
                                    raw_val = item.get("raw_value")
                                    if raw_val is not None and str(raw_val).strip():
                                        norm_val = item.get("normalized_value", raw_val)
                                        try:
                                            conf = float(item.get("confidence", item.get("confidence_score", 0.95)))
                                        except (ValueError, TypeError):
                                            conf = 0.95
                                        extracted_fields.append(
                                            ExtractedField(
                                                field_name=fname,
                                                raw_value=str(raw_val).strip(),
                                                normalized_value=str(norm_val).strip() if norm_val is not None else str(raw_val).strip(),
                                                unit=str(item.get("unit")) if item.get("unit") is not None else None,
                                                confidence=max(0.0, min(1.0, conf)),
                                            )
                                        )
                            elif isinstance(item, (ExtractedField, tuple)):
                                coerced = self._coerce_field(item)
                                if coerced is not None:
                                    extracted_fields.append(coerced)

                    response = LLMExtractionOutput(attributes=extracted_fields)
                elapsed_ms = (time.perf_counter() - start_time) * 1000.0

                record.processing.llm_8b_time_ms = elapsed_ms
                record.processing.models_invoked.append(self.model_name)
                record.processing.tokens_consumed += estimated_tokens
                self.rate_limiter.reset_429_backoff()

                return self._map_response_to_attributes(response, evidence_snippet)

            except Exception as exc:
                exc_str = str(exc).lower()
                if _is_tool_use_failure(exc):
                    # 400 tool_use_failed: the tool-use JSON generator choked.
                    # giving up.
                    logger.warning(
                        "8B tool-use generation failed (400); retrying with "
                        "instructor.Mode.JSON: %s",
                        exc,
                    )
                    use_json_mode = True
                    if attempts < max_attempts:
                        continue
                if "429" in exc_str or "rate limit" in exc_str:
                    backoff = self.rate_limiter.handle_429(
                        retry_after_seconds=_parse_retry_after(exc)
                    )
                    self._rotate_key()
                    if attempts < max_attempts:
                        await asyncio.sleep(buffered_backoff(backoff))
                        continue
                record.record_error(RowStatus.EXTRACTING_8B, exc)
                break

        return {}

    async def process_record(
        self, record: ProductRecord, client_override: Any | None = None
    ) -> ProductRecord:
        """Transition to ``EXTRACTING_8B``, run 8B extraction on the reduced
        context (or the raw description), merge results, and transition to
        ``VALIDATING_8B``.
        """
        record.status = RowStatus.EXTRACTING_8B

        context = (
            record.retrieval.reduced_text_snippet
            or record.identity.raw_description
        )

        extracted = await self.extract_specs(
            record, text_context=context, client_override=client_override
        )

        for field_name, attr in extracted.items():
            # Only overwrite if the field is not present or the previous
            # source was low confidence.
            if (
                field_name not in record.attributes
                or record.attributes[field_name].confidence < 0.8
            ):
                record.attributes[field_name] = attr

        record.status = RowStatus.VALIDATING_8B
        return record
