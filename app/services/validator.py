"""
app/services/validator.py
Stage 8: Tri-Signal Validation Engine for UniPulse AI.

Evaluates extraction quality across three independent signals:

1. **Business Rule Validity** — physical sanity of extracted values
   (voltage non-negative and numeric, grit on the P-scale, dimensions
   well-formed).
2. **Category Completeness** — required-field coverage for the product's
   category.
3. **Overall Confidence** — average model confidence across attributes.

Records that pass the gate route to Provenance Merge; everything else
escalates to the 70B fallback model.
"""

from __future__ import annotations

import re

from app.schemas.product import AttributeValue, ProductRecord, RowStatus


class ValidationEngine:
    """Tri-Signal Validation Engine evaluating extraction quality across
    three signals:

    1. Business Rule Validity (physical value sanity checks).
    2. Category Completeness (required field coverage).
    3. Extraction Confidence (average model certainty score).
    """

    CATEGORY_REQUIRED_FIELDS: dict[str, list[str]] = {
        "General": ["voltage", "dimensions", "material", "power"],
        "Abrasives": ["grit", "dimensions", "material"],
        "Electrical": ["voltage", "power", "frequency"],
        "Fittings": ["dimensions", "material", "connection_type"],
    }

    @classmethod
    def validate_business_rules(
        cls, attributes: dict[str, AttributeValue]
    ) -> tuple[bool, list[str]]:
        """Validate extracted attribute values against business-rule domain
        logic. Returns ``(is_valid, validation_flags)``.
        """
        flags: list[str] = []

        def _val_of(attr: Any) -> str:
            if hasattr(attr, "normalized_value") and attr.normalized_value:
                return str(attr.normalized_value)
            if hasattr(attr, "raw_value") and attr.raw_value:
                return str(attr.raw_value)
            if isinstance(attr, dict):
                return str(attr.get("normalized_value") or attr.get("raw_value") or "")
            if isinstance(attr, (tuple, list)) and len(attr) > 1:
                return str(attr[2] if len(attr) > 2 and attr[2] else attr[1])
            return str(attr or "")

        # 1. Voltage validation: must be a non-negative number.
        if "voltage" in attributes:
            v_attr = attributes["voltage"]
            v_val = _val_of(v_attr)
            v_match = re.search(r"(-?\d+(?:\.\d+)?)", str(v_val))
            if v_match is None:
                flags.append(f"INVALID_VOLTAGE_FORMAT: {v_val}")
            elif float(v_match.group(1)) < 0:
                flags.append(f"INVALID_VOLTAGE_NEGATIVE: {v_val}")

        # 2. Grit validation: must match the P-scale format (P120, 120).
        if "grit" in attributes:
            g_attr = attributes["grit"]
            g_val = _val_of(g_attr)
            if not re.match(r"^P?\d{2,4}$", str(g_val).strip(), re.IGNORECASE):
                flags.append(f"INVALID_GRIT_FORMAT: {g_val}")

        # 3. Dimensions validation: must contain a valid number pair.
        if "dimensions" in attributes:
            d_attr = attributes["dimensions"]
            d_val = _val_of(d_attr)
            if not re.search(r"\d+", str(d_val)):
                flags.append(f"INVALID_DIMENSIONS_FORMAT: {d_val}")

        is_valid = len(flags) == 0
        return is_valid, flags

    @classmethod
    def calculate_completeness(
        cls, attributes: dict[str, AttributeValue], category: str = "General"
    ) -> float:
        """Calculate the ratio of required category fields present in
        *attributes* (rounded to two decimals)."""
        required = cls.CATEGORY_REQUIRED_FIELDS.get(
            category, cls.CATEGORY_REQUIRED_FIELDS["General"]
        )
        if not required:
            return 1.0

        found_count = sum(1 for field in required if field in attributes)
        return round(found_count / len(required), 2)

    @classmethod
    def calculate_overall_confidence(
        cls, attributes: dict[str, AttributeValue]
    ) -> float:
        """Calculate the average confidence across all extracted attributes."""
        if not attributes:
            return 0.0

        total_conf = sum(attr.confidence for attr in attributes.values())
        return round(total_conf / len(attributes), 2)

    def evaluate_tri_signal(
        self,
        record: ProductRecord,
        confidence_threshold: float = 0.8,
        completeness_threshold: float = 0.75,
    ) -> ProductRecord:
        """Evaluate the Tri-Signal Gate: Validity AND Completeness AND
        Confidence. Routes to ``PROVENANCE_MERGE`` on PASS, or
        ``ESCALATED_70B`` on FAIL/INCOMPLETE.
        """
        # Signal 1: Business Rule Validity
        is_valid, flags = self.validate_business_rules(record.attributes)

        # Signal 2: Category Completeness
        completeness = self.calculate_completeness(
            record.attributes, record.identity.category
        )

        # Signal 3: Overall Confidence
        confidence = self.calculate_overall_confidence(record.attributes)

        # Update quality score in canonical record
        record.quality.validity = is_valid
        record.quality.completeness = completeness
        record.quality.overall_confidence = confidence
        record.quality.validation_flags = flags

        # Tri-Signal Gate Routing Decision
        tri_signal_pass = (
            is_valid
            and (completeness >= completeness_threshold)
            and (confidence >= confidence_threshold)
        )

        if tri_signal_pass:
            record.status = RowStatus.PROVENANCE_MERGE
        else:
            record.status = RowStatus.ESCALATED_70B

        return record
