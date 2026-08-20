"""
app/services/llm_70b.py
Stage 9: 70B Fallback LLM Extractor for UniPulse AI.

Fallback extractor using Groq's ``llama-3.3-70b-versatile`` model, triggered
only when Stage 8 validation fails. Uses a targeted repair prompt that names
the exact validation flags (e.g. ``INVALID_VOLTAGE_NEGATIVE``), acquires rate
limiter capacity, tags every extracted attribute with
``source = ExtractionSource.LLM_70B_FALLBACK``, and re-runs the repaired
extraction through the Stage 8 ``ValidationEngine`` before routing the record
to ``PROVENANCE_MERGE`` (on success) or ``MANUAL_REVIEW`` (on persistent
failure).
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from typing import Any, Dict, List, Optional

from app.schemas.product import (
    AttributeValue,
    ExtractionSource,
    ProductRecord,
    RowStatus,
)
from app.services.llm_8b import (
    FORBIDDEN_KEYS,
    _FORBIDDEN_FIELD_NAMES,
    _is_tool_use_failure,
    _parse_retry_after,
    ExtractedField,
    LLMExtractionOutput,
)
from app.services.rate_limiter import (
    AdaptiveRateLimiter,
    buffered_backoff,
    is_tpd_exhausted,
)
from app.services.validator import ValidationEngine

logger = logging.getLogger(__name__)


def repair_json_string(json_str: str) -> str:
    """Repair common LLM JSON syntax errors.

    Strips hallucinated `</?function[^>]*>` tags, markdown code blocks, converts
    key assignments like `"key" = value` or `key = value` into `"key": value`,
    removes trailing commas before `}` or `]`, and cleans up surrounding whitespace.
    """
    if not json_str:
        return ""

    s = json_str.strip()

    # Strip markdown fences if present
    if s.startswith("```"):
        s = re.sub(r"^```(?:json)?\s*", "", s, flags=re.IGNORECASE)
        s = re.sub(r"\s*```$", "", s)

    # Strip leading <function...>, <function=...>, <tool_call...>, etc.
    s = re.sub(r"^<function\b[^>{\[]*=?\s*", "", s, flags=re.IGNORECASE)
    s = re.sub(r"^<tool_call\b[^>{\[]*=?\s*", "", s, flags=re.IGNORECASE)

    # Strip tags like </function>, </tool_call>, or any non-JSON tags without braces
    s = re.sub(r"</?(?:function|tool_call|tool_calls)[^>{}]*>", "", s, flags=re.IGNORECASE)

    # If s does not start with { or [, locate the first { or [
    if not (s.startswith("{") or s.startswith("[")):
        start_brace = s.find("{")
        start_bracket = s.find("[")
        starts = [i for i in (start_brace, start_bracket) if i != -1]
        if starts:
            s = s[min(starts):]

    # If s does not end with } or ], locate the last } or ]
    if not (s.endswith("}") or s.endswith("]")):
        end_brace = s.rfind("}")
        end_bracket = s.rfind("]")
        ends = [i for i in (end_brace, end_bracket) if i != -1]
        if ends:
            s = s[: max(ends) + 1]

    # Convert key assignment '=' to ':' (e.g. "field_name" = "voltage" -> "field_name": "voltage")
    s = re.sub(r'("(?:[^"\\]|\\.)*")\s*=\s*', r'\1: ', s)
    s = re.sub(r'(\b[a-zA-Z_][a-zA-Z0-9_]*\b)\s*=\s*', r'"\1": ', s)

    # Fix trailing commas before closing braces/brackets: e.g. ", }" -> "}" or ", ]" -> "]"
    s = re.sub(r',\s*([\}\]])', r'\1', s)

    return s.strip()


class LLM70BFallbackExtractor:
    """
    Fallback LLM Extractor using Groq's llama-3.3-70b-versatile model.
    Triggered only when Stage 8 validation fails. Uses targeted repair prompts,
    acquires rate limiter capacity, tags attributes with source = LLM_70B_FALLBACK,
    and re-validates results before routing to PROVENANCE_MERGE or MANUAL_REVIEW.
    """

    def __init__(
        self,
        api_keys: Optional[List[str]] = None,
        model_name: str = "llama-3.3-70b-versatile",
        rate_limiter: Optional[AdaptiveRateLimiter] = None,
        validator: Optional[ValidationEngine] = None,
    ) -> None:
        self.api_keys = [k.strip() for k in (api_keys or []) if k.strip()]
        self.current_key_idx = 0
        self.model_name = model_name
        self.rate_limiter = rate_limiter or AdaptiveRateLimiter()
        self.validator = validator or ValidationEngine()

    def _get_current_key(self) -> str:
        if not self.api_keys:
            return "mock_key"
        return self.api_keys[self.current_key_idx % len(self.api_keys)]

    def _rotate_key(self) -> str:
        if not self.api_keys:
            return "mock_key"
        self.current_key_idx = (self.current_key_idx + 1) % len(self.api_keys)
        return self._get_current_key()

    def build_targeted_prompt(self, record: ProductRecord, text_context: str) -> str:
        """Constructs targeted repair prompt specifying validation failure reasons."""
        flags_str = (
            ", ".join(record.quality.validation_flags)
            if record.quality.validation_flags
            else "None"
        )
        existing_attrs = {k: v.raw_value for k, v in record.attributes.items()}

        return (
            f"You are an expert industrial data auditor repairing a failed extraction.\n"
            f"Manufacturer: {record.identity.manufacturer}\n"
            f"Part Number: {record.identity.mfg_part_number}\n"
            f"Category: {record.identity.category}\n"
            f"Raw Description: {record.identity.raw_description}\n\n"
            f"Previous Extraction Attempt: {existing_attrs}\n"
            f"Validation Failure Flags: {flags_str}\n"
            f"Current Completeness: {record.quality.completeness}\n\n"
            f"Context Document:\n{text_context[:1500]}\n\n"
            f"Carefully re-extract and correct the technical fields. Fix any invalid values or missing attributes. "
            f"Each attribute must have a unique field_name — never repeat a field_name. "
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

    def _map_response_to_attributes(
        self, response: LLMExtractionOutput, text_context: str, record: ProductRecord
    ) -> Dict[str, AttributeValue]:
        evidence = (
            text_context[:100] if text_context else record.identity.raw_description
        )
        output: Dict[str, AttributeValue] = {}
        for raw_field in response.attributes:
            field = self._coerce_field(raw_field)
            if not field or not field.field_name or field.raw_value is None:
                continue
            output[field.field_name] = AttributeValue(
                field_name=field.field_name,
                raw_value=field.raw_value,
                normalized_value=field.normalized_value or field.raw_value,
                unit=field.unit,
                confidence=field.confidence,
                source=ExtractionSource.LLM_70B_FALLBACK,
                evidence_snippet=evidence,
            )
        return output

    async def extract_fallback(
        self,
        record: ProductRecord,
        text_context: str,
        client_override: Optional[Any] = None,
    ) -> Dict[str, AttributeValue]:
        """Executes fallback extraction with targeted prompt and rate-limit checks."""
        prompt = self.build_targeted_prompt(record, text_context)
        estimated_tokens = len(prompt) // 4 + 200

        await self.rate_limiter.acquire(estimated_tokens=estimated_tokens)

        if client_override is None:
            try:
                from groq import AsyncGroq
            except ImportError as exc:
                record.record_error(RowStatus.EXTRACTING_70B, exc)
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
                    try:
                        chat_resp = await raw_client.chat.completions.create(
                            model=self.model_name,
                            messages=[
                                {
                                    "role": "system",
                                    "content": "You are a top-tier industrial technical specialist repairing product attributes. Respond ONLY with a valid JSON object containing an 'attributes' list.",
                                },
                                {"role": "user", "content": prompt},
                            ],
                            response_format={"type": "json_object"},
                            temperature=0.1,
                        )
                    except Exception as model_err:
                        err_str = str(model_err).lower()
                        if "404" in err_str or "model_not_found" in err_str or "decommissioned" in err_str:
                            fallback_m = "llama-3.1-8b-instant" if "8b" not in self.model_name else "llama-3.1-70b-versatile"
                            logger.warning(
                                "70B model %s returned 404/not_found; failing over to %s",
                                self.model_name,
                                fallback_m,
                            )
                            chat_resp = await raw_client.chat.completions.create(
                                model=fallback_m,
                                messages=[
                                    {
                                        "role": "system",
                                        "content": "You are a top-tier industrial technical specialist repairing product attributes. Respond ONLY with a valid JSON object containing an 'attributes' list.",
                                    },
                                    {"role": "user", "content": prompt},
                                ],
                                response_format={"type": "json_object"},
                                temperature=0.1,
                            )
                        else:
                            raise
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

                record.processing.llm_70b_time_ms = elapsed_ms
                record.processing.models_invoked.append(self.model_name)
                record.processing.tokens_consumed += estimated_tokens
                self.rate_limiter.reset_429_backoff()

                return self._map_response_to_attributes(response, text_context, record)

            except Exception as exc:
                exc_str = str(exc).lower()
                is_tool_use = _is_tool_use_failure(exc)
                is_val_failed = (
                    "validation" in exc_str
                    or getattr(exc, "failed_generation", None) is not None
                )

                if is_tool_use or is_val_failed:
                    failed_gen = getattr(exc, "failed_generation", None)
                    if failed_gen and isinstance(failed_gen, str):
                        try:
                            repaired_str = repair_json_string(failed_gen)
                            raw_data = json.loads(repaired_str)
                            if isinstance(raw_data, dict):
                                response = LLMExtractionOutput.model_validate(raw_data)
                            elif isinstance(raw_data, list):
                                response = LLMExtractionOutput(
                                    attributes=[
                                        ExtractedField.model_validate(item)
                                        for item in raw_data
                                    ]
                                )
                            else:
                                response = None

                            if response:
                                logger.info(
                                    "Successfully repaired malformed JSON from exc.failed_generation"
                                )
                                record.processing.models_invoked.append(self.model_name)
                                return self._map_response_to_attributes(
                                    response, text_context, record
                                )
                        except Exception as repair_exc:
                            logger.warning(
                                "Manual JSON repair failed on generation: %s",
                                repair_exc,
                            )

                    logger.warning(
                        "70B extraction failed (%s); retrying with instructor.Mode.JSON",
                        "tool_use_failed" if is_tool_use else "validation_failed",
                    )
                    use_json_mode = True
                    if attempts < max_attempts:
                        continue

                if is_tpd_exhausted(exc) or "429" in exc_str or "rate limit" in exc_str:
                    if is_tpd_exhausted(exc):
                        logger.warning(
                            "70B TPD (Tokens Per Day) quota depleted; switching fallback model to llama-3.1-8b-instant"
                        )
                        self.model_name = "llama-3.1-8b-instant"

                    backoff = self.rate_limiter.handle_429(
                        retry_after_seconds=_parse_retry_after(exc)
                    )
                    self._rotate_key()
                    if attempts < max_attempts:
                        await asyncio.sleep(buffered_backoff(backoff))
                        continue
                record.record_error(RowStatus.EXTRACTING_70B, exc)
                break

        return {}

    async def process_record(
        self, record: ProductRecord, client_override: Optional[Any] = None
    ) -> ProductRecord:
        """
        Executes 70B fallback processing, merges corrected attributes, re-evaluates
        quality via ValidationEngine, and routes to PROVENANCE_MERGE or MANUAL_REVIEW.
        """
        record.status = RowStatus.EXTRACTING_70B

        context = (
            record.retrieval.reduced_text_snippet
            or record.identity.raw_description
        )
        extracted = await self.extract_fallback(
            record, text_context=context, client_override=client_override
        )

        # Merge corrected attributes
        for field_name, attr in extracted.items():
            record.attributes[field_name] = attr

        # Re-validate record via ValidationEngine
        record.status = RowStatus.VALIDATING_70B
        validated_record = self.validator.evaluate_tri_signal(record)

        # Route based on re-validation outcome
        if validated_record.status == RowStatus.PROVENANCE_MERGE:
            return validated_record
        else:
            validated_record.status = RowStatus.MANUAL_REVIEW
            return validated_record
