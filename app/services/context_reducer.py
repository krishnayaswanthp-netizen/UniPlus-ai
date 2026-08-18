"""
app/services/context_reducer.py
Stage 5: Relevance-Based Scraped Context Reducer for UniPulse AI.

Cleans raw HTML/webpage text, scores sentences by technical-specification
density (numbers, units of measure, key-value pairs, spec keywords, product
identity), and keeps only the top-K spec-dense sentences (~1,500 chars) for
the LLM stage. Integrates with the Stage 4 scrape cache so the same URL is
never re-scraped (and never re-reduced) twice.
"""

from __future__ import annotations

import re

from app.db.checkpoint_store import CheckpointStore
from app.schemas.product import ProductRecord, RowStatus


class ContextReducer:
    """Cleans raw HTML/webpage text and scores sentences based on
    specification density (numbers, units of measure, key-value pairs,
    manufacturer terms). Reduces 25,000+ character raw scraped text to
    ~1,500 spec-dense characters.
    """

    SPEC_KEYWORDS = {
        "voltage", "volts", "power", "watts", "hp", "grit", "dimension",
        "dimensions", "size", "diameter", "width", "height", "depth",
        "material", "finish", "rating", "mounting", "capacity", "pressure",
        "psi", "rpm", "frequency", "hz", "current", "amps", "amperage",
        "weight", "temperature", "temp", "type", "series", "color", "uom",
    }

    UNITS_PATTERN = re.compile(
        r"\b(\d+(?:\.\d+)?(?:\/\d+)?)\s*"
        r"(v|vac|vdc|w|kw|hp|hz|in|mm|cm|ft|\"|psi|rpm|dba|a|amp|amps|"
        r"lb|lbs|kg|oz|°c|°f)\b",
        re.IGNORECASE,
    )

    BOILERPLATE_PATTERNS = [
        re.compile(r"copyright\s+\d+", re.IGNORECASE),
        re.compile(r"all\s+rights\s+reserved", re.IGNORECASE),
        re.compile(r"cookie\s+policy|privacy\s+policy|terms\s+of\s+use", re.IGNORECASE),
        re.compile(r"add\s+to\s+cart|shopping\s+cart|sign\s+in|checkout", re.IGNORECASE),
        re.compile(r"free\s+shipping|customer\s+service|contact\s+us", re.IGNORECASE),
    ]

    @classmethod
    def clean_raw_text(cls, raw_html_or_text: str) -> str:
        """Strip HTML tags, scripts, styles, entities, and blank lines."""
        if not raw_html_or_text:
            return ""

        # 1. Remove script and style elements
        text = re.sub(
            r"<(script|style)[^>]*>.*?</\1>",
            " ",
            raw_html_or_text,
            flags=re.DOTALL | re.IGNORECASE,
        )
        # 2. Strip HTML tags
        text = re.sub(r"<[^>]+>", " ", text)
        # 3. Unescape HTML entities
        text = (
            text.replace("&nbsp;", " ")
            .replace("&amp;", "&")
            .replace("&lt;", "<")
            .replace("&gt;", ">")
        )
        # 4. Normalize multiple newlines and spaces
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        return "\n".join(lines)

    @classmethod
    def score_sentence(cls, sentence: str, mfg: str = "", part_num: str = "") -> float:
        """Score a sentence by specification density.

        +3.0 per unit-of-measure match (e.g., 24V, 5 in)
        +2.0 for key-value specification pairs (e.g., "Material: Steel")
        +1.0 per spec keyword found
        +2.0 for matching manufacturer or part number
        -5.0 for navigation/footer boilerplate
        """
        if not sentence or len(sentence.strip()) < 10:
            return 0.0

        sent_lower = sentence.lower()

        # Boilerplate penalty short-circuits everything else.
        for b_pat in cls.BOILERPLATE_PATTERNS:
            if b_pat.search(sent_lower):
                return -5.0

        score = 0.0

        # Unit matches
        unit_matches = len(cls.UNITS_PATTERN.findall(sent_lower))
        score += unit_matches * 3.0

        # Key-value spec pairs (e.g., "Operating Voltage: 120V")
        if ":" in sentence:
            score += 2.0

        # Spec keywords
        words = set(re.findall(r"\b\w+\b", sent_lower))
        keyword_hits = words.intersection(cls.SPEC_KEYWORDS)
        score += len(keyword_hits) * 1.0

        # Identity matches
        if mfg and mfg.lower() in sent_lower:
            score += 2.0
        if part_num and part_num.lower() in sent_lower:
            score += 2.0

        return score

    def reduce_context(
        self,
        raw_text: str,
        max_chars: int = 1500,
        mfg: str = "",
        part_num: str = "",
    ) -> str:
        """Split cleaned text into sentences, score each, and return the
        top-scoring sentences up to *max_chars*, re-ordered by their
        original position to keep reading flow.
        """
        cleaned = self.clean_raw_text(raw_text)
        if not cleaned:
            return ""

        if len(cleaned) <= max_chars:
            return cleaned

        # Split into sentences. Catalog text is line-based, so bare newline
        # runs must also break sentences — the prose-only lookbehind
        # `(?<=[.!?\n])\s+` would merge a line ending in ")" (e.g. the
        # boilerplate "Shopping Cart (0 items)") with the identity line that
        # follows, letting the boilerplate penalty kill real content.
        raw_sentences = re.split(r"(?<=[.!?])\s+|\n+", cleaned)

        scored_sentences: list[tuple[float, int, str]] = []
        for idx, sentence in enumerate(raw_sentences):
            sentence_clean = sentence.strip()
            if not sentence_clean:
                continue
            score = self.score_sentence(
                sentence_clean, mfg=mfg, part_num=part_num
            )
            if score > 0.0:
                scored_sentences.append((score, idx, sentence_clean))

        # Sort by score descending.
        scored_sentences.sort(key=lambda x: x[0], reverse=True)

        # Select top sentences up to max_chars limit.
        selected_sentences: list[tuple[int, str]] = []
        current_chars = 0

        for _score, original_idx, sentence in scored_sentences:
            if current_chars + len(sentence) + 1 > max_chars and selected_sentences:
                break
            selected_sentences.append((original_idx, sentence))
            current_chars += len(sentence) + 1

        # Re-order by original position to maintain reading flow.
        selected_sentences.sort(key=lambda x: x[0])
        return " ".join([s[1] for s in selected_sentences])

    def process_retrieval(
        self,
        record: ProductRecord,
        scraped_text: str,
        source_url: str | None = None,
        checkpoint_store: CheckpointStore | None = None,
    ) -> ProductRecord:
        """Run retrieval processing for *record*.

        Checks the scrape cache first (cache hit short-circuits to
        ``LLM_PENDING`` without re-reducing), otherwise reduces the scraped
        text to its spec-dense snippet, persists it to the scrape cache, and
        transitions the record to ``RowStatus.LLM_PENDING``.

        Note: the scrape cache stores the *reduced* snippet keyed by URL, so
        a later row sharing the same URL receives context reduced with the
        first row's ``mfg``/``part_num`` scoring terms.
        """
        record.status = RowStatus.RETRIEVAL
        record.retrieval.selected_source_url = source_url
        record.retrieval.raw_scraped_char_count = len(scraped_text or "")

        # Check scrape cache if store and URL available.
        if checkpoint_store and source_url:
            cached_text = checkpoint_store.get_scrape_cache(source_url)
            if cached_text:
                record.retrieval.cache_hit = True
                record.retrieval.reduced_text_snippet = cached_text
                record.retrieval.reduced_context_char_count = len(cached_text)
                record.status = RowStatus.LLM_PENDING
                return record

        # Reduce context.
        reduced = self.reduce_context(
            scraped_text,
            max_chars=1500,
            mfg=record.identity.manufacturer,
            part_num=record.identity.mfg_part_number,
        )

        record.retrieval.reduced_text_snippet = reduced
        record.retrieval.reduced_context_char_count = len(reduced)

        # Save to scrape cache if store and URL present.
        if checkpoint_store and source_url and reduced:
            checkpoint_store.save_scrape_cache(source_url, reduced)

        record.status = RowStatus.LLM_PENDING
        return record
