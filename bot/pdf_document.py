"""PdfDocument — turn a givven url PDF into clean, uniform data.

This module owns everything messy about the PDF itself: 
downloading/caching a URL,
opening it, 
detecting budget-table pages, 
correcting pdfplumber's right-to-left
(bidi) text, and reading tables. 
It knows *nothing* about budgets or request fields.

Its public surface is small and stable, so a consumer (see budget_letter.BudgetLetter)
can rely on it without caring how the PDF was parsed:

    doc = PdfDocument(source)          # source is a URL or a local path
    doc.text        -> str             # every page, bidi-corrected (lines reversed)
    doc.raw_text    -> str             # every page, exactly as pdfplumber emits it
    doc.pages       -> list[PageData]  # per-page text + raw tables
    doc.tables      -> list[DataFrame] # header-page tables, split into from/to/delta

Number direction is normalized here too, so consumers get correct digits without
re-ordering anything themselves.
"""
from __future__ import annotations

import hashlib
import logging
import re
import tempfile
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from functools import cached_property
from pathlib import Path

import pandas as pd
import pdfplumber

# pdfminer (used by pdfplumber) logs a noisy "Could not get FontBBox" warning for
# PDFs whose font descriptors omit a FontBBox; it doesn't affect text/table output.
logging.getLogger("pdfminer").setLevel(logging.ERROR)


@dataclass
class PageData:
    """One page's readable text and its raw pdfplumber tables (rows of cells)."""

    text: str
    tables: list  # list[list[list[str | None]]] — unprocessed, for page-level lookups


class PdfDocument:
    """A budget-letter PDF exposed as clean text and tables."""

    HEADER = "האוצר - אגף התקציבים"

    # The source PDFs draw some column borders as two near-overlapping vertical
    # lines, which makes pdfplumber split one logical column into a duplicate
    # sliver column. Snapping nearby vertical lines together merges them.
    TABLE_SETTINGS = {"snap_x_tolerance": 10}

    def __init__(self, source, *, use_first_row_as_header: bool = True):
        self.source = source
        self.use_first_row_as_header = use_first_row_as_header

    # ---- source handling ----

    @staticmethod
    def _download(url: str, attempts: int = 4) -> bytes:
        """Download url, retrying with backoff so a transient URLError doesn't abort the run."""
        last_error: Exception | None = None
        for attempt in range(attempts):
            try:
                with urllib.request.urlopen(url, timeout=30) as response:
                    return response.read()
            except (urllib.error.URLError, TimeoutError) as error:
                last_error = error
                if attempt < attempts - 1:
                    time.sleep(2 ** attempt)  # 1s, 2s, 4s
        raise RuntimeError(f"Failed to download {url} after {attempts} attempts: {last_error}")

    @staticmethod
    def _as_openable(source):
        """Return something pdfplumber.open can read.

        pdfplumber.open only accepts local paths or file-like objects, not URLs,
        so http(s) sources are downloaded once into an on-disk cache (keyed by URL)
        and reused on later runs — the download is the slow part, not the parsing.
        """
        if isinstance(source, str) and source.startswith(("http://", "https://")):
            cache_dir = Path(tempfile.gettempdir()) / "pdf_key_checker_cache"
            cache_dir.mkdir(exist_ok=True)
            cache_file = cache_dir / (hashlib.sha256(source.encode()).hexdigest() + ".pdf")
            if not cache_file.exists():
                cache_file.write_bytes(PdfDocument._download(source))
            return str(cache_file)
        return source

    def local_path(self) -> str:
        """Path to the PDF on disk (downloading/caching it first for URL sources).

        Lets callers keep a copy of the original letter alongside the generated
        output, without re-downloading — it returns the same cached file used for parsing.
        """
        return self._as_openable(self.source)

    def _open(self):
        return pdfplumber.open(self._as_openable(self.source))

    def _is_header_text(self, raw: str) -> bool:
        """True if a page's raw text contains HEADER (i.e. a budget-table page).

        pdfplumber doesn't apply bidi reordering, so Hebrew lines come out
        character-reversed; reverse each line back before searching for HEADER.
        Word breaks can shift across line wraps, so whitespace is stripped
        entirely (not just collapsed) before comparing.
        """
        normalized_header = "".join(self.HEADER.split())
        lines = (line[::-1] for line in raw.splitlines())
        page_text = "".join("".join(lines).split())
        return normalized_header in page_text

    # ---- bidi / number direction ----

    @staticmethod
    def _reverse_number(token: str) -> str:
        """Put one number token (inside already line-reversed text) back into raw order.

        `line[::-1]` reverses the whole line to fix the Hebrew, which also reverses every
        number; reversing each number token again restores it to the order pdfplumber
        actually read off the page — the correct value for all of them: date 10/05/2026,
        amount 43,688, negative -2,550, request number 51-202, program codes. One uniform
        rule, no per-field special cases.
        """
        return token[::-1]

    @classmethod
    def _fix_numbers(cls, line: str) -> str:
        """Reverse every number run in a line back to correct order (see _reverse_number)."""
        return re.sub(r"\d[\d.,/ -]*\d|\d",
                      lambda m: cls._reverse_number(m.group(0)), line)

    @classmethod
    def _fix_line(cls, line: str) -> str:
        """Correct one raw pdfplumber line: reverse it (fixes Hebrew) then fix the numbers."""
        return cls._fix_numbers(line[::-1])

    # ---- the single parse ----

    # Table extraction is pdfplumber's slow part (minutes over a 378-page letter), so it
    # runs only on pages that carry tables anyone reads: the budget-table pages (HEADER)
    # and the appendix pages (the history marker). Text is extracted for every page.
    TABLE_PAGE_MARKERS = ("היסטוריה תקציבית",)

    @cached_property
    def _parsed(self) -> list[tuple[str, str, list]]:
        """ONE pass over the PDF, kept for the document's lifetime.

        Returns (raw text, bidi-corrected text, raw tables) per page. Every accessor
        below (text, raw_text, pages, header tables) derives from this, so the file is
        opened and read exactly once no matter how many times they are used.
        """
        with self._open() as pdf:
            if not pdf.pages:
                raise ValueError("PDF has no pages")
            parsed = []
            for page in pdf.pages:
                raw = page.extract_text() or ""
                fixed = "\n".join(self._fix_line(line) for line in raw.splitlines())
                wants_tables = self._is_header_text(raw) or any(
                    marker in fixed for marker in self.TABLE_PAGE_MARKERS)
                tables = (page.extract_tables(self.TABLE_SETTINGS) or []) if wants_tables else []
                parsed.append((raw, fixed, tables))
        return parsed

    # ---- text ----

    @property
    def text(self) -> str:
        """Every page's text in correct reading order (Hebrew and numbers both fixed)."""
        return "\n".join(fixed for _, fixed, _ in self._parsed)

    @property
    def raw_text(self) -> str:
        """Every page's text exactly as pdfplumber emits it (no bidi reversal).

        Used for reading left-to-right content such as URLs, which come out correct
        in the raw text but backwards in the bidi-corrected text.
        """
        return "\n".join(raw for raw, _, _ in self._parsed)

    @property
    def pages(self) -> list[PageData]:
        """Per-page bidi-corrected text plus that page's raw tables (cleaned cells)."""
        return [
            PageData(
                text=fixed,
                tables=[[[self._clean_cell(cell) for cell in row] for row in table]
                        for table in tables],
            )
            for _, fixed, tables in self._parsed
        ]

    # ---- tables ----

    def _header_page_tables(self) -> list[list[list[str | None]]]:
        """Return all raw tables found on every page that contains HEADER."""
        header_tables = [tables for raw, _, tables in self._parsed if self._is_header_text(raw)]
        if not header_tables:
            raise ValueError(f"Header '{self.HEADER}' not found in any page of the PDF")
        return [table for tables in header_tables for table in tables]

    @classmethod
    def _clean_cell(cls, cell) -> str:
        """Return a table cell in correct reading order, using the same rule as text.

        Each line is reversed (fixes Hebrew) and its numbers corrected via _fix_numbers,
        exactly like running text — so a year 2026 stays 2026 and codes/amounts (045110,
        337,434) are preserved. Line breaks are kept so stacked from/to cells still split
        correctly downstream.
        """
        if cell is None:
            return ""
        return "\n".join(cls._fix_numbers(line[::-1]).strip() for line in str(cell).split("\n"))

    def table_to_dataframe(self, table: list[list[str | None]]) -> pd.DataFrame:
        """Convert a raw table (list of rows) into a pandas DataFrame with clean cells."""
        if not table:
            return pd.DataFrame()

        cleaned = [[self._clean_cell(cell) for cell in row] for row in table]

        if self.use_first_row_as_header and len(cleaned) > 1:
            columns = cleaned[0]
            data = cleaned[1:]
            return pd.DataFrame(data, columns=columns)

        return pd.DataFrame(cleaned)

    @staticmethod
    def _parse_number(text: str) -> float | None:
        """Parse a numeric string like '1,539,800' or '461.5' into a float, else None."""
        import re

        cleaned = text.strip().replace(",", "")
        if re.fullmatch(r"-?\d+(\.\d+)?", cleaned):
            return float(cleaned)
        return None

    def split_stacked_columns(self, df: pd.DataFrame) -> pd.DataFrame:
        """Split every stacked מ/ל cell ('from\\nto') into <col> from, <col> to and <col> delta (to-from)."""
        columns: dict[str, list] = {}
        for j in range(df.shape[1]):
            series = df.iloc[:, j].astype(str)
            name = str(df.columns[j]).strip() or f"col{j}"

            parsed: list[tuple[float, float] | None] = []
            is_stacked = False
            for value in series:
                parts = value.split("\n")
                if len(parts) == 2:
                    top = self._parse_number(parts[0])
                    bottom = self._parse_number(parts[1])
                    if top is not None and bottom is not None:
                        parsed.append((top, bottom))
                        is_stacked = True
                        continue
                parsed.append(None)

            if is_stacked:
                columns[f"{name} from"] = [p[0] if p else None for p in parsed]
                columns[f"{name} to"] = [p[1] if p else None for p in parsed]
                columns[f"{name} delta"] = [p[1] - p[0] if p else None for p in parsed]
            else:
                columns[name] = series.tolist()

        return pd.DataFrame(columns)

    @property
    def tables(self) -> list[pd.DataFrame]:
        """
        Header-page tables, each split into 
        from/to/delta columns — ready to combine.
        """
        tables = self._header_page_tables()
        if not tables:
            raise ValueError("No tables found on the header pages.")
        return [self.split_stacked_columns(self.table_to_dataframe(t)) for t in tables]
