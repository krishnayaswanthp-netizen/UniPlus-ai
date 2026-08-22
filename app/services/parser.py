"""Document parsing service (PDFs via PyMuPDF).

``PDFParser`` turns uploaded product datasheets and PDF manuals into clean
plain text, preserving technical table layouts (cell boundaries rendered as
pipe-separated rows) so downstream LLM extraction can recover spec tables.
"""

from __future__ import annotations

import re

import fitz  # PyMuPDF

#: Collapses any run of whitespace (incl. newlines inside extracted blocks).
_WHITESPACE_RE = re.compile(r"\s+")

#: Hard cap on a single page's text + tables to guard against pathological PDFs.
_MAX_PAGE_CHARS = 200_000


class PDFParser:
    """Extract clean text and technical table layouts from PDF documents."""

    def extract_text_and_tables(self, file_bytes: bytes) -> str:
        """Return the textual content of the PDF as a single string.

        Raises
        ------
        ValueError
            If *file_bytes* is empty, corrupt/unreadable, or the PDF is
            password-protected.
        """
        if not file_bytes:
            raise ValueError("PDF content is empty")

        try:
            document = fitz.open(stream=file_bytes, filetype="pdf")
        except Exception as exc:  # fitz.FileDataError and friends
            raise ValueError(f"Corrupt or unreadable PDF: {exc}") from exc

        try:
            if document.needs_pass:
                raise ValueError("Password-protected PDF; cannot extract text without credentials")

            blocks: list[str] = []
            for page in document:
                blocks.append(self._page_text(page))
                blocks.append(self._page_tables(page))
                if sum(len(block) for block in blocks) > _MAX_PAGE_CHARS:
                    break
            return self._clean("\n".join(blocks))
        finally:
            document.close()

    # -- per-page extraction ----------------------------------------------

    def _page_text(self, page: fitz.Page) -> str:
        """Extract the plain text layer of *page*."""
        return page.get_text("text")

    def _page_tables(self, page: fitz.Page) -> str:
        """Render detected table layouts as pipe-separated rows of text.

        PyMuPDF's table finder locates tabular regions via ruling lines and
        text alignment; ``table.extract()`` returns rows of cell strings.
        Returns an empty string when the page has no detectable tables (or the
        table finder is unavailable in this PyMuPDF build).
        """
        try:
            finder = page.find_tables()
        except Exception:
            return ""

        rendered: list[str] = []
        for table in finder.tables:
            rows = table.extract()
            if not rows:
                continue
            for row in rows:
                cells = [self._clean(cell) for cell in row]
                rendered.append(" | ".join(cell for cell in cells if cell))
        return "\n".join(rendered)

    # -- helpers -----------------------------------------------------------

    @staticmethod
    def _clean(text: str) -> str:
        """Normalize whitespace and drop empty lines from *text*."""
        lines: list[str] = []
        for line in text.splitlines():
            line = _WHITESPACE_RE.sub(" ", line).strip()
            if line:
                lines.append(line)
        return "\n".join(lines)
