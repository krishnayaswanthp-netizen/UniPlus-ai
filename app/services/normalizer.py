"""Data normalization service (units via Pint, fuzzy matching via RapidFuzz).

``UnitNormalizer`` turns free-form spec values taken from product documents
(e.g. ``"10mm"``, ``"1/2 inch"``, ``"120 VAC"``, ``"800 CFM"``,
``"5.4 sq in"``) into a deterministic ``(normalized_value, unit)`` pair so
downstream consumers (enrichment, comparison, search) can rely on
consistent units.
"""

from __future__ import annotations

import re

import pint
from rapidfuzz import fuzz

# ---------------------------------------------------------------------------
# Module-level building blocks
# ---------------------------------------------------------------------------

#: Shared Pint registry — creating one parses the unit definition file and is
#: expensive (~1s), so a single module-level instance is reused by all
#: ``UnitNormalizer`` instances.
_UREG = pint.UnitRegistry()

#: Minimum RapidFuzz ratio for a fuzzy unit-alias match to be accepted.
_FUZZY_THRESHOLD = 85

#: Canonical unit name -> human-written aliases (exact matches win).
UNIT_ALIASES: dict[str, tuple[str, ...]] = {
    "millimeter": ("mm", "millimeter", "millimeters", "millimetre", "millimetres"),
    "centimeter": ("cm", "centimeter", "centimeters", "centimetre", "centimetres"),
    "meter": ("m", "meter", "meters", "metre", "metres"),
    "inch": ("in", "inch", "inches", '"'),
    "foot": ("ft", "foot", "feet", "'"),
    "square_inch": ("sq in", "sqin", "square inch", "square inches"),
    "square_foot": ("sq ft", "sqft", "square foot", "square feet"),
    "square_meter": ("sq m", "sqm", "square meter", "square meters", "square metre", "square metres"),
    "square_centimeter": (
        "sq cm",
        "sqcm",
        "square centimeter",
        "square centimeters",
        "square centimetre",
        "square centimetres",
    ),
    "volt": ("v", "volt", "volts", "vac", "vdc"),
    "watt": ("w", "watt", "watts"),
    "kilowatt": ("kw", "kilowatt", "kilowatts"),
    "ampere": ("a", "amp", "amps", "ampere", "amperes"),
    "hertz": ("hz", "hertz"),
    "cfm": ("cfm", "cubic feet per minute", "cubic foot per minute"),
    "psi": ("psi", "pounds per square inch", "pound per square inch"),
    "bar": ("bar", "bars"),
    "degF": ("degf", "deg f", "degree f", "degrees f", "fahrenheit", "\u00b0f"),
}

#: Canonical unit -> short symbol used in the normalized output.
UNIT_SYMBOLS: dict[str, str] = {
    "millimeter": "mm",
    "centimeter": "cm",
    "meter": "m",
    "inch": "in",
    "foot": "ft",
    "square_inch": "sq in",
    "square_foot": "sq ft",
    "square_meter": "sq m",
    "square_centimeter": "sq cm",
    "volt": "V",
    "watt": "W",
    "kilowatt": "kW",
    "ampere": "A",
    "hertz": "Hz",
    "cfm": "CFM",
    "psi": "psi",
    "bar": "bar",
    "degF": "\u00b0F",
}

#: Lengths are normalized to millimeters (metric) or inches (imperial).
_METRIC_LENGTH_UNITS = frozenset({"millimeter", "centimeter", "meter"})
_IMPERIAL_LENGTH_UNITS = frozenset({"inch", "foot"})

#: One ton of refrigeration == 12,000 BTU/hour (HVAC standard).
_BTUS_PER_TON = 12_000.0

#: Matches a leading value: decimal, fraction, or plain integer.
_VALUE_RE = re.compile(
    r"^(?P<value>[+-]?(?:\d+\.\d*|\.\d+|\d+/\d+|\d+))\s*(?P<unit>.*)$"
)

#: Mixed number, e.g. "1 1/2 inch" (whole + space + fraction).
_MIXED_FRACTION_RE = re.compile(
    r"^(?P<whole>\d+)\s+(?P<frac>\d+/\d+)\s*(?P<unit>.*)$"
)

#: Generic dash range, e.g. "20-30 A", "1-2 m". Dash-only by design so
#: fraction syntax ("1/2 inch") is never misread as a range; slash ranges
#: for voltages are already handled by ``_VOLTAGE_RE``.
_RANGE_RE = re.compile(
    r"^(?P<lo>\d+(?:\.\d+)?)\s*-\s*(?P<hi>\d+(?:\.\d+)?)\s*(?P<unit>[A-Za-z%\"'\u00b0]+)$"
)

#: Removes thousands separators ("1,200" -> "1200", "1,234,567" -> "1234567")
#: while leaving genuine decimal commas alone ("1,5 mm" stays "1,5 mm").
_THOUSANDS_RE = re.compile(r"(?<=\d),(?=\d{3}(?:\D|$))")

#: Thread spec, e.g. "1/2-14 NPT", "3/4-16 UNF", "1/4-20 UNC".
_THREAD_RE = re.compile(
    r"^(?P<diam>\d+(?:\.\d+)?|\d+/\d+)\s*-\s*(?P<tpi>\d+)\s*"
    r"(?P<form>NPT|NPTF|NPSM|UNF|UNC|UNEF|BSP|BSPT)\b",
    re.IGNORECASE,
)

#: HVAC capacity in tons of refrigeration, e.g. "3.5 tons".
_TONNAGE_RE = re.compile(r"^(?P<value>\d+(?:\.\d+)?)\s*tons?\b", re.IGNORECASE)

#: Voltage spec including ranges, e.g. "120 V", "24 VDC", "120/240 V".
_VOLTAGE_RE = re.compile(
    r"^(?P<v1>\d+(?:\.\d+)?)(?P<sep>\s*/\s*|\s*-\s*)?"
    r"(?P<v2>\d+(?:\.\d+)?)?\s*(?P<suffix>VAC|VDC|V)\b",
    re.IGNORECASE,
)

#: Airflow in cubic feet per minute, e.g. "800 CFM", "1200 cubic feet per
#: minute". Anchored to the full string so trailing qualifiers pass through
#: untouched rather than being silently truncated.
_CFM_RE = re.compile(
    r"^(?P<value>\d+(?:\.\d+)?)\s*(?:CFM|cubic feet? per minute)\s*$",
    re.IGNORECASE,
)

#: Area expressions, e.g. "5.4 sq in", "12 sq ft", "10 sq m",
#: "2.5 square feet". Anchored to the full string so plain lengths like
#: "10 m" are never misread as areas — they keep flowing to the generic
#: length path and convert to millimeters. The ``square_*`` entries in
#: ``UNIT_ALIASES`` make this rule a fast explicit path for the same
#: strings the generic parser would resolve — keep both in sync.
_AREA_RE = re.compile(
    r"^(?P<value>\d+(?:\.\d+)?)\s*"
    r"(?P<unit>sq\s*(?:in|ft|m|cm)|square\s*"
    r"(?:inch|inches|foot|feet|meter|meters|metre|metres|"
    r"centimeter|centimeters|centimetre|centimetres))"
    r"\s*$",
    re.IGNORECASE,
)


def _parse_value(token: str) -> float | None:
    """Convert a decimal/fraction value token to a float (``None`` if invalid)."""
    token = token.strip()
    if "/" in token:
        try:
            numerator, denominator = token.split("/", maxsplit=1)
            return float(numerator) / float(denominator)
        except (ValueError, ZeroDivisionError):
            return None
    try:
        return float(token)
    except ValueError:
        return None


def _fmt(magnitude: float) -> str:
    """Format a magnitude without float artifacts (``10.0`` -> ``'10'``)."""
    rounded = round(magnitude, 4)
    if rounded == int(rounded):
        return str(int(rounded))
    return f"{rounded:.4f}".rstrip("0").rstrip(".")


class UnitNormalizer:
    """Deterministic normalization of product spec values.

    Parses dimension/value strings such as ``"10mm"``, ``"1/2 inch"``,
    ``"120 VAC"`` or ``"800 CFM"`` and returns a canonical
    ``(normalized_value, unit)`` pair.  Lengths are expressed in millimeters
    (metric input) or inches (imperial input); electrical and airflow values
    keep their natural magnitude with a canonical unit symbol.  Compound
    HVAC/Electrical specs (threads, tonnage, voltage ranges, airflow CFM)
    and area expressions are handled by dedicated regex fallback rules.
    """

    # -- public API --------------------------------------------------------

    def normalize_field(self, raw_value: str) -> tuple[str, str | None]:
        """Normalize *raw_value* into a ``(normalized_value, unit)`` tuple.

        Returns ``(raw_value, None)`` unchanged when the value cannot be
        parsed, so callers can safely fall back to the original text.
        """
        text = raw_value.strip()
        if not text:
            return text, None

        # Pre-normalize: Unicode minus (datasheets often use U+2212 instead of
        # ASCII '-') and thousands separators ("1,200" -> "1200"; European
        # decimal commas like "1,5" are left untouched).
        text = text.replace("\u2212", "-")
        text = _THOUSANDS_RE.sub("", text)

        # 1) Fallback rules for compound HVAC / Electrical specs.
        match = _THREAD_RE.match(text)
        if match:
            return self._normalize_thread(match)

        match = _TONNAGE_RE.match(text)
        if match:
            return self._normalize_tonnage(match)

        match = _VOLTAGE_RE.match(text)
        if match:
            return self._normalize_voltage(match)

        match = _CFM_RE.match(text)
        if match:
            return self._normalize_cfm(match)

        # Area expressions ("5.4 sq in", "12 sq ft", "10 sq m") — value and
        # unit are split apart; the magnitude is kept as written.
        match = _AREA_RE.match(text)
        if match:
            normalized = self._normalize_area(match)
            if normalized is not None:
                return normalized

        # 2) Mixed numbers ("1 1/2 inch") — checked before generic parsing so
        #    the whole+space+fraction pattern isn't split by ``_VALUE_RE``.
        match = _MIXED_FRACTION_RE.match(text)
        if match:
            return self._normalize_mixed_fraction(match)

        # 3) Generic dash ranges ("20-30 A", "1-2 m"). Dash-only, so fraction
        #    syntax ("1/2 inch") is never misinterpreted as a range.
        match = _RANGE_RE.match(text)
        if match:
            return self._normalize_range(match)

        # 4) Generic "<number> <unit>" parsing (Pint-backed).
        match = _VALUE_RE.match(text)
        if not match:
            return text, None

        magnitude = _parse_value(match.group("value"))
        if magnitude is None:
            return text, None

        unit_token = match.group("unit").strip()
        if not unit_token:
            return _fmt(magnitude), None

        canonical = self._resolve_unit(unit_token)
        if canonical is None:
            return text, None

        return self._convert(canonical, magnitude)

    # -- fallback rule handlers -------------------------------------------

    def _normalize_thread(self, match: re.Match[str]) -> tuple[str, None]:
        """Standardize ``1/2-14 NPT`` -> ``0.5-14 NPT`` (diameter in inches)."""
        diameter = _parse_value(match.group("diam"))
        if diameter is None:
            return match.group(0), None
        tpi = match.group("tpi")
        form = match.group("form").upper()
        return f"{_fmt(diameter)}-{tpi} {form}", None

    def _normalize_tonnage(self, match: re.Match[str]) -> tuple[str, str]:
        """Convert HVAC tonnage to BTU/hour (``3.5 tons`` -> ``42000 BTU/h``)."""
        tons = _parse_value(match.group("value"))
        if tons is None:
            return match.group(0), "BTU/h"
        return _fmt(tons * _BTUS_PER_TON), "BTU/h"

    def _normalize_voltage(self, match: re.Match[str]) -> tuple[str, str]:
        """Standardize voltage specs (``120 VAC`` -> ``120 V``).

        Ranges keep their original separator (``120/240 V`` or ``120-277 V``).
        """
        v1 = _fmt(float(match.group("v1")))
        v2 = match.group("v2")
        if v2 is None:
            return v1, "V"
        separator = (match.group("sep") or "/").strip() or "/"
        return f"{v1}{separator}{_fmt(float(v2))}", "V"

    def _normalize_cfm(self, match: re.Match[str]) -> tuple[str, str]:
        """Normalize airflow to cubic feet per minute (``800 CFM``)."""
        return _fmt(float(match.group("value"))), "CFM"

    def _normalize_area(self, match: re.Match[str]) -> tuple[str, str] | None:
        """Split area specs into value + canonical unit (``5.4 sq in``).

        Returns ``None`` when the unit token cannot be resolved, letting the
        generic parse path take over (should not happen given the anchored
        ``_AREA_RE``, but kept safe).
        """
        magnitude = _parse_value(match.group("value"))
        if magnitude is None:
            return None
        canonical = self._resolve_unit(match.group("unit").strip())
        if canonical is None:
            return None
        return _fmt(magnitude), UNIT_SYMBOLS.get(canonical, canonical)

    def _normalize_mixed_fraction(
        self, match: re.Match[str],
    ) -> tuple[str, str | None]:
        """Parse mixed numbers (``1 1/2 inch`` -> ``1.5 in``)."""
        frac = _parse_value(match.group("frac"))
        if frac is None:
            return match.group(0), None
        magnitude = float(match.group("whole")) + frac
        unit_token = match.group("unit").strip()
        if not unit_token:
            return _fmt(magnitude), None
        canonical = self._resolve_unit(unit_token)
        if canonical is None:
            return match.group(0), None
        return self._convert(canonical, magnitude)

    def _normalize_range(self, match: re.Match[str]) -> tuple[str, str | None]:
        """Normalize dash ranges (``20-30 A`` -> ``20-30 A``).

        Both endpoints are converted to the canonical display unit, so e.g.
        ``1-2 m`` becomes ``1000-2000 mm``.
        """
        unit_token = match.group("unit").strip()
        canonical = self._resolve_unit(unit_token)
        if canonical is None:
            return match.group(0), None
        low_value, unit = self._convert(canonical, float(match.group("lo")))
        high_value, _ = self._convert(canonical, float(match.group("hi")))
        return f"{low_value}-{high_value}", unit

    # -- helpers -----------------------------------------------------------

    def _resolve_unit(self, token: str) -> str | None:
        """Map a raw unit token to a canonical unit name (fuzzy-tolerant)."""
        token = token.strip().lower()
        if not token:
            return None

        # Exact alias match first (e.g. "mm", "VAC", '"').
        for canonical, aliases in UNIT_ALIASES.items():
            if token in aliases:
                return canonical

        # Fuzzy match for typos / alternate spellings (e.g. "millmeters").
        best_score, best_canonical = 0, None
        for canonical, aliases in UNIT_ALIASES.items():
            for alias in aliases:
                score = fuzz.ratio(token, alias)
                if score > best_score:
                    best_score, best_canonical = score, canonical
        return best_canonical if best_score >= _FUZZY_THRESHOLD else None

    def _convert(self, canonical: str, magnitude: float) -> tuple[str, str]:
        """Convert *magnitude* to the canonical display unit for *canonical*."""
        ureg = _UREG

        if canonical in _METRIC_LENGTH_UNITS:
            quantity = magnitude * getattr(ureg, canonical)
            return _fmt(quantity.to(ureg.millimeter).magnitude), UNIT_SYMBOLS["millimeter"]

        if canonical in _IMPERIAL_LENGTH_UNITS:
            quantity = magnitude * getattr(ureg, canonical)
            return _fmt(quantity.to(ureg.inch).magnitude), UNIT_SYMBOLS["inch"]

        return _fmt(magnitude), UNIT_SYMBOLS.get(canonical, canonical)
