"""
app/services/deterministic.py
Stage 3: Deterministic Rule Engine for UniPulse AI.

Runs local C-speed Python regex rules over a product's ``raw_description``
to extract obvious technical specs (voltage, power, grit, dimensions,
frequency, material) without invoking an LLM. Every extracted attribute is
tagged with exact provenance (``source = ExtractionSource.REGEX``,
``confidence = 0.99``), category-level field coverage is computed, and rows
that reach the coverage threshold short-circuit to ``COMPLETED`` without
touching the cache or retrieval stages.
"""

from __future__ import annotations

import re
import time

from app.schemas.product import (
    AttributeValue,
    ExtractionSource,
    ProductRecord,
    RowStatus,
)


class DeterministicEngine:
    """Local C-speed Python regex & Pint engine for extracting deterministic
    attributes directly from raw product descriptions without external API
    calls.
    """

    #: Required fields per product category, used to score field coverage.
    CATEGORY_REQUIRED_FIELDS: dict[str, list[str]] = {
        "General": ["voltage", "dimensions", "material", "power"],
        "Abrasives": ["grit", "dimensions", "material"],
        "Electrical": ["voltage", "power", "frequency"],
        "Fittings": ["dimensions", "material", "connection_type"],
    }

    #: Canonical material names keyed by their lowercase regex match, so
    #: ``"PVC"`` stays ``"PVC"`` (``str.capitalize()`` would produce "Pvc").
    _MATERIAL_NORMALIZED = {
        "stainless steel": "Stainless Steel",
        "sst": "Stainless Steel",
        "polycarbonate": "Polycarbonate",
        "aluminum": "Aluminum",
        "brass": "Brass",
        "pvc": "PVC",
        "copper": "Copper",
        "steel": "Steel",
    }

    # Pre-compiled regex patterns for speed.
    PATTERNS = {
        "voltage": re.compile(r"\b(\d+(?:\.\d+)?)\s*(V|VAC|VDC|Volts?)\b", re.IGNORECASE),
        "power": re.compile(r"\b(\d+(?:\.\d+)?)\s*(W|kW|HP|Watts?)\b", re.IGNORECASE),
        "grit": re.compile(r"\b(P\d{2,4})\b", re.IGNORECASE),
        "frequency": re.compile(r"\b(\d+(?:\.\d+)?)\s*(Hz)\b", re.IGNORECASE),
        # Dimensions allow an optional unit between the first value and the
        # "x" separator — real catalog phrasing is "6 in x 1/8 in", not
        # "6 x 1/8 in". The inter-unit is a non-capturing group so the match
        # groups stay (value, value, unit) exactly as before.
        # KNOWN LIMITATION: the trailing unit is optional and defaults to
        # "in", so non-dimension "N x M" tokens ("24 x 7" hours, "5 x 2"
        # pack quantities) are also captured. Tightening this later would
        # require a unit or dimension-y context; all stage-3 test cases
        # carry a trailing unit.
        "dimensions": re.compile(
            r'\b(\d+(?:\.\d+)?(?:\/\d+)?)\s*(?:(?:in|mm|cm|ft|")\s*)?(?:x|\*)\s*'
            r'(\d+(?:\.\d+)?(?:\/\d+)?)\s*(in|mm|cm|ft|")?\b',
            re.IGNORECASE,
        ),
        "material": re.compile(
            r"\b(Stainless Steel|SST|Polycarbonate|Aluminum|Brass|PVC|Copper|Steel)\b",
            re.IGNORECASE,
        ),
    }

    def extract_attributes(self, raw_description: str) -> dict[str, AttributeValue]:
        """Extract deterministic technical specs using regex patterns."""
        if not raw_description:
            return {}

        extracted: dict[str, AttributeValue] = {}

        # 1. Voltage
        v_match = self.PATTERNS["voltage"].search(raw_description)
        if v_match:
            val, unit = v_match.group(1), v_match.group(2).upper()
            extracted["voltage"] = AttributeValue(
                field_name="voltage",
                raw_value=v_match.group(0),
                normalized_value=val,
                unit=unit if unit in ["V", "VAC", "VDC"] else "V",
                confidence=0.99,
                source=ExtractionSource.REGEX,
                evidence_snippet=v_match.group(0),
            )

        # 2. Power
        p_match = self.PATTERNS["power"].search(raw_description)
        if p_match:
            val, unit = p_match.group(1), p_match.group(2)
            extracted["power"] = AttributeValue(
                field_name="power",
                raw_value=p_match.group(0),
                normalized_value=val,
                unit=unit.upper() if unit.upper() in ["W", "KW", "HP"] else "W",
                confidence=0.99,
                source=ExtractionSource.REGEX,
                evidence_snippet=p_match.group(0),
            )

        # 3. Grit
        g_match = self.PATTERNS["grit"].search(raw_description)
        if g_match:
            extracted["grit"] = AttributeValue(
                field_name="grit",
                raw_value=g_match.group(0),
                normalized_value=g_match.group(1).upper(),
                unit=None,
                confidence=0.99,
                source=ExtractionSource.REGEX,
                evidence_snippet=g_match.group(0),
            )

        # 4. Frequency
        f_match = self.PATTERNS["frequency"].search(raw_description)
        if f_match:
            extracted["frequency"] = AttributeValue(
                field_name="frequency",
                raw_value=f_match.group(0),
                normalized_value=f_match.group(1),
                unit="Hz",
                confidence=0.99,
                source=ExtractionSource.REGEX,
                evidence_snippet=f_match.group(0),
            )

        # 5. Dimensions
        d_match = self.PATTERNS["dimensions"].search(raw_description)
        if d_match:
            dim_str = f"{d_match.group(1)} x {d_match.group(2)}"
            unit = d_match.group(3) if d_match.group(3) else "in"
            unit_norm = "in" if unit in ['"', "in"] else unit.lower()
            extracted["dimensions"] = AttributeValue(
                field_name="dimensions",
                raw_value=d_match.group(0),
                normalized_value=dim_str,
                unit=unit_norm,
                confidence=0.99,
                source=ExtractionSource.REGEX,
                evidence_snippet=d_match.group(0),
            )

        # 6. Material
        m_match = self.PATTERNS["material"].search(raw_description)
        if m_match:
            mat_raw = m_match.group(0)
            mat_norm = self._MATERIAL_NORMALIZED.get(
                mat_raw.lower(), mat_raw.capitalize()
            )
            extracted["material"] = AttributeValue(
                field_name="material",
                raw_value=mat_raw,
                normalized_value=mat_norm,
                unit=None,
                confidence=0.99,
                source=ExtractionSource.REGEX,
                evidence_snippet=mat_raw,
            )

        return extracted

    def calculate_coverage(
        self, attributes: dict[str, AttributeValue], category: str = "General"
    ) -> float:
        """Calculate the field-coverage ratio for *category*.

        The ratio is the number of *category* required fields present in
        *attributes* divided by the total required, rounded to two decimals.
        """
        required = self.CATEGORY_REQUIRED_FIELDS.get(
            category, self.CATEGORY_REQUIRED_FIELDS["General"]
        )
        if not required:
            return 0.0

        found_count = sum(1 for field in required if field in attributes)
        return round(found_count / len(required), 2)

    def process_record(
        self, record: ProductRecord, coverage_threshold: float = 0.8
    ) -> ProductRecord:
        """Run deterministic extraction over *record* and route it onward.

        Populates ``record.attributes`` (``source = REGEX``), stamps
        ``record.quality.coverage_ratio`` and ``deterministic_time_ms``, then
        short-circuits to ``COMPLETED`` when coverage meets the threshold or
        transitions to ``CACHE_CHECK`` otherwise.
        """
        record.status = RowStatus.DETERMINISTIC_CHECK

        # Record the deterministic stage's wall-clock time.
        start = time.perf_counter()
        extracted = self.extract_attributes(record.identity.raw_description)
        record.processing.deterministic_time_ms = (
            time.perf_counter() - start
        ) * 1000.0

        # Merge extracted attributes.
        for field_name, attr in extracted.items():
            record.attributes[field_name] = attr

        # Calculate coverage ratio.
        coverage = self.calculate_coverage(
            record.attributes, record.identity.category
        )
        record.quality.coverage_ratio = coverage

        # Short-circuit or transition status.
        if coverage >= coverage_threshold:
            record.mark_completed()
        else:
            record.status = RowStatus.CACHE_CHECK

        return record
