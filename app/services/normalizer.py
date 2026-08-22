"""Data normalization service (units via Pint, fuzzy matching via RapidFuzz).

``UnitNormalizer`` turns free-form spec values taken from product documents
(e.g. ``"10mm"``, ``"1/2 inch"``, ``"120 VAC"``, ``"800 CFM"``,
``"5.4 sq in"``, ``"1075 RPM"``) into a deterministic ``(normalized_value,
unit)`` pair so downstream consumers (enrichment, comparison, search) can
rely on consistent units.

Parsing is grammar-first: a single structural regex splits ANY leading
numeric expression — integers, decimals, proper fractions (``1/3``, ``3/4``),
mixed numbers (``1 1/2``) and dash / word / slash ranges (``20-30``,
``37 to 102``, ``10/16``) — from its trailing text suffix. Fractions convert
to a standard numeric string (``1/3`` -> ``0.333``) while ranges normalize
to the hyphenated ``lo-hi`` form (``20 to 25`` -> ``20-25``).

The suffix is resolved against a canonical unit registry (Pint-backed
conversions for lengths, exact aliases, and fuzzy typo-tolerant matching).
When it cannot be resolved the split is NOT discarded: a universal fallback
reports the cleaned suffix as the unit (``"1075 RPM"`` -> ``("1075", "RPM")``,
``"1/3 HP"`` -> ``("0.333", "HP")``). Noise qualifiers (``NPT``, ``NPTF``,
``BSP``, ``DIN``, ``mount``) are stripped before resolution so ``"3/4 in NPT"``
normalizes cleanly to ``"0.75 in"``.
"""

from __future__ import annotations

import re
from typing import Any

import pint
from rapidfuzz import fuzz

from app.schemas.product import ProductIdentity, ProductRecord, RawInputData, RowStatus

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
    "degC": ("degc", "deg c", "degree c", "degrees c", "celsius", "\u00b0c"),
    "kva": ("kva", "kilovolt-ampere", "kilovolt-amperes"),
    "percent": ("%", "percent", "pct", "percentage", "percentages"),
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
    "degC": "\u00b0C",
    "degree": "deg",
    "kva": "kVA",
    "percent": "%",
}

#: Lengths are normalized to millimeters (metric) or inches (imperial).
_METRIC_LENGTH_UNITS = frozenset({"millimeter", "centimeter", "meter"})
_IMPERIAL_LENGTH_UNITS = frozenset({"inch", "foot"})

#: One ton of refrigeration == 12,000 BTU/hour (HVAC standard).
_BTUS_PER_TON = 12_000.0

#: Substrings in ``field_name`` that mark a spec as spatial/rotational, where
#: a bare "deg"/"degree(s)" means angular degrees — not degrees Fahrenheit.
_ANGULAR_FIELD_TERMS = ("rotation", "angle", "stroke", "position")

#: Bare degree tokens that resolve to angular degrees on angular fields.
_ANGULAR_DEGREE_TOKENS = frozenset({"deg", "degree", "degrees"})

#: Noise qualifiers stripped from unit suffixes before canonical resolution
#: (thread forms, mounting/standard markers): ``"3/4 in NPT"`` -> ``"in"``.
_NOISE_QUALIFIERS = frozenset({"npt", "nptf", "bsp", "din", "mount"})

#: Gate for the universal fallback: an unrecognized suffix is only reported
#: as the unit when it *looks* like a real unit token (letters/symbols, no
#: digits or conditional punctuation). This keeps trailing conditional
#: phrases ("800 CFM @ 0.5 in. wc") and European decimal commas ("1,5 mm")
#: from being misinterpreted as value+unit splits.
_PLAUSIBLE_UNIT_RE = re.compile(
    r"^[A-Za-z%°'\"\u00b5\u03bc][A-Za-z%°'\"\u00b5\u03bc\u00b3\u00b2/.\-\s]*$"
)

#: UNIVERSAL STRUCTURAL GRAMMAR. Splits ANY leading numeric expression from
#: its trailing text suffix:
#:   * integers / decimals      "1075 RPM", "5.4 sq in"
#:   * proper fractions         "1/3 HP", "1/2 inch"
#:   * mixed numbers            "1 1/2 inch"
#:   * dash ranges              "20-30 A", "-40-185 deg F"
#:   * word ranges              "37 to 102 deg F"
#:   * slash ranges             "10/16 mm", "120/240 V"
#: The value group is intentionally one structural capture: every numeric
#: form is separated from the suffix in a single pass, then re-disambiguated
#: by :meth:`UnitNormalizer._parse_value_expression`.
_STRUCTURAL_RE = re.compile(
    r"^(?P<value>"
    r"[+-]?(?:\d+/\d+|\d+\.\d*|\.\d+|\d+)"      # leading scalar / decimal / fraction
    r"(?:\s+\d+/\d+)?"                           # mixed-number tail ("1 1/2")
    r"(?:\s*[-/]\s*[+-]?\d+(?:\.\d+)?|"          # dash/slash range tail
    r"\s+to\s+[+-]?\d+(?:\.\d+)?)?"              # word range tail
    r")\s*(?P<unit>.*)$",
    re.IGNORECASE,
)

#: Mixed number value token, e.g. "1 1/2" (whole + space + fraction).
_MIXED_NUMBER_RE = re.compile(r"^(?P<whole>\d+)\s+(?P<frac>\d+/\d+)$")

#: Word-form range value token, e.g. "37 to 102", "-40 to 185".
_WORD_RANGE_RE = re.compile(
    r"^(?P<lo>[+-]?\d+(?:\.\d+)?)\s+to\s+(?P<hi>[+-]?\d+(?:\.\d+)?)$",
    re.IGNORECASE,
)

#: Dash-form range value token, e.g. "20-30", "-40-185".
_DASH_RANGE_RE = re.compile(
    r"^(?P<lo>[+-]?\d+(?:\.\d+)?)\s*-\s*(?P<hi>[+-]?\d+(?:\.\d+)?)$"
)

#: Slash-pair value token, e.g. "1/3", "-1/3", "10/16", "120/240". Read as
#: either a proper fraction (inch/foot units or unrecognized units) or a lo-hi
#: size pair range (recognized non-length units — datasheet convention).
_SLASH_PAIR_RE = re.compile(
    r"^(?P<lo>[+-]?\d+(?:\.\d+)?)/(?P<hi>\d+(?:\.\d+)?)$"
)

#: Plain scalar value token, e.g. "1075", "5.4", ".5", "-40".
_SCALAR_RE = re.compile(r"^[+-]?(?:\d+(?:\.\d*)?|\.\d+)$")

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
#: The suffix is followed by a negative lookahead so compound units like
#: "VAC/DC" are NOT truncated to just "V" — they fall through to the generic
#: structural grammar and are preserved verbatim as the unit.
_VOLTAGE_RE = re.compile(
    r"^(?P<v1>\d+(?:\.\d+)?)(?P<sep>\s*/\s*|\s*-\s*)?"
    r"(?P<v2>\d+(?:\.\d+)?)?\s*(?P<suffix>VAC|VDC|V)\b(?![A-Za-z/])",
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
#: structural path and convert to millimeters. The ``square_*`` entries in
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


def _fmt_fraction(magnitude: float) -> str:
    """Format a fraction magnitude to its standard numeric form.

    Terminating fractions keep their exact short form (``1/16`` ->
    ``"0.0625"``, ``7/16`` -> ``"0.4375"``); repeating fractions round to
    three decimal places (``1/3`` -> ``"0.333"``, ``1/6`` -> ``"0.167"``).
    """
    if abs(magnitude - round(magnitude, 4)) < 1e-9:
        return _fmt(magnitude)
    rounded = round(magnitude, 3)
    if rounded == int(rounded):
        return str(int(rounded))
    return f"{rounded:.3f}".rstrip("0").rstrip(".")


class UnitNormalizer:
    """Deterministic normalization of product spec values.

    Parses dimension/value strings such as ``"10mm"``, ``"1/2 inch"``,
    ``"120 VAC"``, ``"800 CFM"`` or ``"1075 RPM"`` and returns a canonical
    ``(normalized_value, unit)`` pair.  Lengths are expressed in millimeters
    (metric input) or inches (imperial input); electrical and airflow values
    keep their natural magnitude with a canonical unit symbol.  Compound
    HVAC/Electrical specs (threads, tonnage, voltage ranges, airflow CFM)
    and area expressions are handled by dedicated regex fallback rules; all
    other inputs go through the universal structural grammar.
    """

    # -- public API --------------------------------------------------------

    def normalize_field(
        self,
        raw_value: str,
        field_name: str | None = None,
        unit: str | None = None,
        normalized_value: str | None = None,
    ) -> tuple[str, str | None]:
        """Normalize *raw_value* into a ``(normalized_value, unit)`` tuple.

        If *unit* is already provided and *normalized_value* is present
        (e.g. from structured LLM extraction), they are preserved with canonical
        symbol resolution. Otherwise, fallback regex grammar parses *raw_value*.

        *field_name* is optional attribute context: when it marks a
        spatial/rotational attribute (``rotation``, ``angle``, ``stroke``,
        ``position``), a bare ``deg``/``degree(s)`` unit is preserved as
        angular degrees instead of being fuzzy-matched to degrees Fahrenheit.
        """
        text = raw_value.strip() if raw_value else ""
        angular = self._is_angular_field(field_name)

        # Direct pass-through: if unit is already provided and normalized_value is given
        if unit is not None and str(unit).strip() and normalized_value is not None and str(normalized_value).strip():
            clean_unit = str(unit).strip()
            clean_norm = str(normalized_value).strip()
            # If normalized_value was cleanly extracted without trailing unit words
            if clean_norm != text or not any(clean_norm.endswith(u) for u in ("mm", "cm", "m", "in", "ft", "VAC", "VDC", "CFM", "psi", "tons", "RPM")):
                canonical = self._resolve_unit(clean_unit, angular=angular)
                final_unit = UNIT_SYMBOLS.get(canonical, clean_unit) if canonical else clean_unit
                return clean_norm, final_unit

        if not text:
            return normalized_value if normalized_value is not None else "", unit

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

        # 2) Universal structural grammar: split the leading numeric
        #    expression (scalar, fraction, mixed number, range) from the
        #    trailing text suffix in a single pass.
        match = _STRUCTURAL_RE.match(text)
        if not match:
            return text, None

        value_token = match.group("value")
        unit_token = match.group("unit").strip()

        # Drop noise qualifiers ("3/4 in NPT" -> "in", "10 mm DIN" -> "mm")
        # before canonical resolution; the cleaned suffix is also what the
        # universal fallback reports as the unit.
        cleaned_unit = self._strip_noise_qualifiers(unit_token)
        canonical = (
            self._resolve_unit(cleaned_unit, angular=angular) if cleaned_unit else None
        )

        parsed = self._parse_value_expression(value_token)
        if parsed is None:
            return text, None

        kind = parsed["kind"]

        # Ranges ("20-30", "37 to 102", "10/16") normalize to "lo-hi".
        if kind == "range":
            if canonical is not None:
                return self._normalize_range_values(
                    parsed["lo"], parsed["hi"], canonical
                )
            # UNIVERSAL FALLBACK — keep the split for unknown units.
            if cleaned_unit and self._is_plausible_unit(cleaned_unit):
                return f"{_fmt(parsed['lo'])}-{_fmt(parsed['hi'])}", cleaned_unit
            return f"{_fmt(parsed['lo'])}-{_fmt(parsed['hi'])}", None

        # Slash pairs are fractions on inch/foot units ("1/2 inch",
        # "3/4 in NPT") and on unrecognized units ("1/3 HP"); on recognized
        # non-length units they are lo-hi size/current pair ranges
        # ("10/16 mm", "20/30 A" — datasheet convention).
        if kind == "slash_pair":
            if canonical in ("inch", "foot") or canonical is None:
                return self._normalize_fraction_value(
                    parsed["lo"] / parsed["hi"],
                    canonical,
                    cleaned_unit,
                    text,
                )
            return self._normalize_range_values(parsed["lo"], parsed["hi"], canonical)

        # Mixed numbers ("1 1/2 inch") always read as fractions.
        if kind == "mixed":
            magnitude = parsed["magnitude"]
            if canonical is not None:
                return self._convert_fraction(canonical, magnitude)
            if cleaned_unit and self._is_plausible_unit(cleaned_unit):
                return _fmt_fraction(magnitude), cleaned_unit
            return _fmt_fraction(magnitude), None

        # Plain scalars ("1075 RPM", "10mm", "5.4 sq in").
        magnitude = parsed["magnitude"]
        if canonical is not None:
            return self._convert(canonical, magnitude)
        # UNIVERSAL FALLBACK — keep the split for unknown units.
        if cleaned_unit and self._is_plausible_unit(cleaned_unit):
            return _fmt(magnitude), cleaned_unit
        return text, None

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
        generic structural path take over (should not happen given the
        anchored ``_AREA_RE``, but kept safe).
        """
        magnitude = _parse_value(match.group("value"))
        if magnitude is None:
            return None
        canonical = self._resolve_unit(match.group("unit").strip())
        if canonical is None:
            return None
        return _fmt(magnitude), UNIT_SYMBOLS.get(canonical, canonical)

    # -- grammar routing helpers ------------------------------------------

    @staticmethod
    def _parse_value_expression(value_token: str) -> dict[str, object] | None:
        """Classify a structural ``value`` token into a routing descriptor.

        Returns a dict with a ``kind`` of ``"range"``, ``"slash_pair"``,
        ``"mixed"`` or ``"scalar"`` plus the parsed magnitudes, or ``None``
        when the token is not a well-formed numeric expression.
        """
        token = value_token.strip()
        if not token:
            return None

        match = _MIXED_NUMBER_RE.match(token)
        if match:
            frac = _parse_value(match.group("frac"))
            if frac is None:
                return None
            return {
                "kind": "mixed",
                "magnitude": float(match.group("whole")) + frac,
            }

        match = _WORD_RANGE_RE.match(token)
        if match:
            return {
                "kind": "range",
                "lo": float(match.group("lo")),
                "hi": float(match.group("hi")),
            }

        match = _DASH_RANGE_RE.match(token)
        if match:
            return {
                "kind": "range",
                "lo": float(match.group("lo")),
                "hi": float(match.group("hi")),
            }

        match = _SLASH_PAIR_RE.match(token)
        if match:
            return {
                "kind": "slash_pair",
                "lo": float(match.group("lo")),
                "hi": float(match.group("hi")),
            }

        if _SCALAR_RE.match(token):
            magnitude = _parse_value(token)
            if magnitude is None:
                return None
            return {"kind": "scalar", "magnitude": magnitude}

        return None

    def _normalize_range_values(
        self, lo: float, hi: float, canonical: str
    ) -> tuple[str, str]:
        """Normalize a lo-hi range to its canonical display form.

        Both endpoints are converted to the canonical display unit, so e.g.
        ``1-2 m`` becomes ``1000-2000 mm`` and ``10/16 mm`` becomes
        ``10-16 mm``. Negative bounds (``-40 to 185 deg F``) are preserved.
        """
        low_value, unit = self._convert(canonical, lo)
        high_value, _ = self._convert(canonical, hi)
        return f"{low_value}-{high_value}", unit

    def _normalize_fraction_value(
        self,
        magnitude: float,
        canonical: str | None,
        cleaned_unit: str,
        text: str,
    ) -> tuple[str, str | None]:
        """Route a proper/mixed fraction through canonical conversion or the
        universal fallback (``1/3 HP`` -> ``("0.333", "HP")``)."""
        if canonical is not None:
            return self._convert_fraction(canonical, magnitude)
        if cleaned_unit:
            if self._is_plausible_unit(cleaned_unit):
                return _fmt_fraction(magnitude), cleaned_unit
            return text, None
        # No unit suffix — the fraction still converts to its numeric form
        # ("3/4" -> ("0.75", None)), mirroring the mixed-number path.
        return _fmt_fraction(magnitude), None

    # -- helpers -----------------------------------------------------------

    @staticmethod
    def _is_angular_field(field_name: str | None) -> bool:
        """Return ``True`` when *field_name* marks a spatial/rotational spec."""
        if not field_name:
            return False
        lowered = field_name.lower()
        return any(term in lowered for term in _ANGULAR_FIELD_TERMS)

    @staticmethod
    def _strip_noise_qualifiers(token: str) -> str:
        """Remove noise qualifiers (NPT, BSP, DIN, mount…) from a unit token.

        Case-preserving so the universal fallback can report the suffix as
        written (``"1075 RPM"`` keeps ``"RPM"``). Word-boundary based, so
        genuine units like ``"in"`` or ``"cfm"`` are never mangled.
        """
        kept = [word for word in token.split() if word.lower() not in _NOISE_QUALIFIERS]
        return " ".join(kept).strip()

    @staticmethod
    def _is_plausible_unit(token: str) -> bool:
        """Return ``True`` when *token* looks like a genuine unit suffix.

        Gates the universal fallback: trailing conditional phrases
        (``"800 CFM @ 0.5 in. wc"``) and European decimal commas
        (``",5 mm"``) must not be reinterpreted as value+unit splits.
        """
        return bool(_PLAUSIBLE_UNIT_RE.match(token))

    def _resolve_unit(
        self, token: str, *, angular: bool = False
    ) -> str | None:
        """Map a raw unit token to a canonical unit name (fuzzy-tolerant).

        Noise qualifiers (``NPT``, ``BSP``, ``DIN``, ``mount``) are stripped
        first so ``"3/4 in NPT"`` resolves against ``"in"``. When *angular*
        is set, a bare ``deg``/``degree(s)`` resolves to the angular
        ``degree`` unit instead of being fuzzy-matched to ``degF``.
        """
        token = token.strip().lower()
        if not token:
            return None

        # Drop noise qualifiers before any alias/fuzzy matching.
        token = " ".join(
            word for word in token.split() if word not in _NOISE_QUALIFIERS
        )
        if not token:
            return None

        # Spatial/rotational fields keep a bare "deg"/"degree(s)" as angular
        # degrees rather than the Fahrenheit unit fuzzy matching would pick.
        if angular and token in _ANGULAR_DEGREE_TOKENS:
            return "degree"

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

    def _convert_fraction(
        self, canonical: str, magnitude: float
    ) -> tuple[str, str]:
        """Convert a fraction magnitude like :meth:`_convert` but formatted
        with the three-decimal fraction formatter (``1/3 m`` -> ``333.333 mm``)."""
        ureg = _UREG

        if canonical in _METRIC_LENGTH_UNITS:
            quantity = magnitude * getattr(ureg, canonical)
            return (
                _fmt_fraction(quantity.to(ureg.millimeter).magnitude),
                UNIT_SYMBOLS["millimeter"],
            )

        if canonical in _IMPERIAL_LENGTH_UNITS:
            quantity = magnitude * getattr(ureg, canonical)
            return (
                _fmt_fraction(quantity.to(ureg.inch).magnitude),
                UNIT_SYMBOLS["inch"],
            )

        return _fmt_fraction(magnitude), UNIT_SYMBOLS.get(canonical, canonical)


# ---------------------------------------------------------------------------
# Stage 2: Input Normalizer & Header Alias Mapping Engine
# ---------------------------------------------------------------------------
# Converts raw catalog rows from arbitrary enterprise distributor files into
# clean, canonical ``ProductRecord`` objects: header aliases are mapped
# (``Mfg_Part_Num`` -> ``mfg_part_number``), parenthetical supplier/ERP codes
# are stripped from manufacturer names (``3M Inc (2435)`` -> ``3M Inc``),
# placeholder strings are filtered (``-- No Unilog Brand --`` -> ``None``),
# and the row is emitted in the Stage 1 canonical shape, ready for the
# deterministic stage of the pipeline (``status = ROW_READY``).


class InputNormalizer:
    """Normalizes raw catalog rows and maps non-standard distributor headers
    to canonical ProductRecord structures."""

    MANUFACTURER_ALIASES = {
        "manufacturer", "part_manuf", "part_manufacturer", "mfg_manuf",
        "manufacturer_name", "mfr", "brand", "mfr_name", "vendor", "e1_brand",
        "unilog_brand", "dib_brand",
    }

    PART_NUMBER_ALIASES = {
        "mfg_part_num", "mfg_part_number", "part_num", "part_number",
        "partnumber", "part_no", "sku", "part", "mpn", "item_num",
    }

    DESCRIPTION_ALIASES = {
        "part_desc", "part_description", "raw_description", "description",
        "product_description", "desc", "item_description", "product_name",
    }

    CATEGORY_ALIASES = {
        "category", "prod_cat", "product_category", "cat", "segment", "dept",
        "class",
    }

    #: Non-informative placeholder strings filtered out of raw cells.
    PLACEHOLDERS = {
        "-- unbranded --", "-- no unilog brand --", "-- no dib brand --",
        "unbranded", "n/a", "none", "unknown", "null", "--",
    }

    @classmethod
    def clean_placeholder(cls, value: str | None) -> str | None:
        """Return ``None`` when *value* is empty or a known placeholder string.

        ``"-- Unbranded --"`` -> ``None``; ``"3M"`` -> ``"3M"``.
        """
        if not value:
            return None
        cleaned = str(value).strip()
        if not cleaned:
            return None
        if cleaned.lower() in cls.PLACEHOLDERS:
            return None
        return cleaned

    @classmethod
    def clean_manufacturer_name(cls, raw_name: str | None) -> str:
        """Strip trailing parenthetical supplier/ERP codes from a company name.

        ``'3M Inc (2435)'`` -> ``'3M Inc'``, ``'Mirka Abrasives Inc (MIRUS)'``
        -> ``'Mirka Abrasives Inc'``. Null/placeholder input falls back to
        ``"Unknown"``.
        """
        cleaned = cls.clean_placeholder(raw_name)
        if not cleaned:
            return "Unknown"
        # Strip trailing parenthetical expressions like (2435) or (MIRUS).
        stripped = re.sub(r"\s*\([^)]*\)$", "", cleaned)
        return stripped.strip() or "Unknown"

    def _find_matching_key(
        self, row_dict: dict[str, Any], alias_set: set[str]
    ) -> str | None:
        """Return the first *row_dict* key matching any alias in *alias_set*.

        Headers are matched flexibly (any casing, spaces or underscores):
        ``"Mfg Part Num"``, ``"Mfg_Part_Num"`` and ``"mfg_part_num"`` all
        resolve to the same alias. Aliases are iterated in sorted order so the
        priority between present aliases is deterministic — set iteration
        order is randomized per process (``PYTHONHASHSEED``), which would
        otherwise make the winner (e.g. ``Part_Manuf`` vs ``Unilog_Brand``)
        flip between runs.
        """
        normalized_keys = {
            re.sub(r"[\s_]+", "", str(k).lower()): k for k in row_dict.keys()
        }
        for alias in sorted(alias_set):
            norm_alias = re.sub(r"[\s_]+", "", alias.lower())
            if norm_alias in normalized_keys:
                return normalized_keys[norm_alias]
        return None

    def map_header_aliases(self, row_dict: dict[str, Any]) -> dict[str, str]:
        """Extract canonical fields from a raw distributor row dictionary.

        Returns ``{"manufacturer", "mfg_part_number", "raw_description",
        "category"}`` with placeholders filtered and parenthetical
        supplier/ERP codes stripped from the manufacturer name.
        """
        mfg_key = self._find_matching_key(row_dict, self.MANUFACTURER_ALIASES)
        part_key = self._find_matching_key(row_dict, self.PART_NUMBER_ALIASES)
        desc_key = self._find_matching_key(row_dict, self.DESCRIPTION_ALIASES)
        cat_key = self._find_matching_key(row_dict, self.CATEGORY_ALIASES)

        raw_mfg = row_dict[mfg_key] if mfg_key else None
        mfg = self.clean_manufacturer_name(
            str(raw_mfg) if raw_mfg is not None else None
        )

        raw_part = row_dict[part_key] if part_key else None
        part_cleaned = self.clean_placeholder(
            str(raw_part) if raw_part is not None else None
        )
        part_num = part_cleaned if part_cleaned else "UNKNOWN"

        raw_desc = row_dict[desc_key] if desc_key else None
        desc_cleaned = self.clean_placeholder(
            str(raw_desc) if raw_desc is not None else None
        )
        desc = desc_cleaned if desc_cleaned else ""

        raw_cat = row_dict[cat_key] if cat_key else None
        cat_cleaned = self.clean_placeholder(
            str(raw_cat) if raw_cat is not None else None
        )
        cat = cat_cleaned if cat_cleaned else "General"

        return {
            "manufacturer": mfg,
            "mfg_part_number": part_num,
            "raw_description": desc,
            "category": cat,
        }

    def normalize_row(
        self,
        row_dict: dict[str, Any],
        row_id: int,
        original_index: int,
        file_source: str | None = None,
    ) -> ProductRecord:
        """Convert a raw CSV/XLSX row dictionary into a clean ``ProductRecord``.

        The returned record carries the canonical identity fields, the
        original header/value pairs for provenance, and starts with
        ``status = ROW_READY`` ready for the deterministic stage.
        """
        canonical_data = self.map_header_aliases(row_dict)

        identity = ProductIdentity(
            row_id=row_id,
            mfg_part_number=canonical_data["mfg_part_number"],
            manufacturer=canonical_data["manufacturer"],
            raw_description=canonical_data["raw_description"],
            category=canonical_data["category"],
        )

        raw_headers = {
            str(k): str(v) for k, v in row_dict.items() if v is not None
        }
        raw_data = RawInputData(
            raw_headers=raw_headers,
            original_row_index=original_index,
            file_source=file_source,
        )

        return ProductRecord(
            identity=identity,
            raw_data=raw_data,
            status=RowStatus.ROW_READY,
        )
