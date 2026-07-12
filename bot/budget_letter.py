"""BudgetLetter — the document/table provider for a budget-transfer request letter.

It takes a PdfDocument (clean text + tables) and exposes the raw materials the single
extraction agent (agent.py) builds on: the combined budget table, the budget history,
the letterhead, the decision links, and a cheap relevance gate. It never touches
pdfplumber or raw PDFs — that is PdfDocument's job. Request-field extraction is NOT
here; it lives in agent.py.

    letter = BudgetLetter(source)                      # URL or local path
    letter.extract_combined_table()                    # -> DataFrame
    letter.budget_codes()                              # cheap relevance gate, no LLM
"""
from __future__ import annotations

import logging
import re

from pdf_document import PdfDocument
from request_fields import RequestFields

logger = logging.getLogger(__name__)


class BudgetLetter:
    """A budget-transfer request letter, exposed as typed answers."""

    # Field labels come from the typed contract, so the two never drift apart.
    REQUEST_FIELDS = RequestFields.field_labels()

    # Combined-table column names (see extract_combined_table, added in Task 5).
    CODE_COLUMN = "number"
    NAME_COLUMN = "name"
    # The budget item name column (שם הפרט), identified by its char-reversed header.
    NAME_SOURCE_HEADER = "שם הפרט"

    # The one field whose value is a list of government-decision URLs.
    DECISION_LINKS_FIELD = "קישורים להחלטות ממשלה"

    # Font with Hebrew glyphs, used by the generated PDFs (see reports.Reports).
    HEBREW_FONT_PATH = "/Library/Fonts/Arial Unicode.ttf"

    # The appendix table (נספח לדברי ההסבר) listing each program's multi-year
    # net-expenditure history; its page is found by this marker in the readable text.
    BUDGET_HISTORY_TITLE = "היסטוריה תקציבית של הפנייה - הוצאה נטו"
    BUDGET_HISTORY_MARKER = "היסטוריה תקציבית"

    def __init__(self, source: str):
        self.source = source
        self.doc = PdfDocument(source)

    # ---- combined budget table + relevance gate ----

    def extract_combined_table(self):
        """The letter's data tables combined into one clean table.

        Columns: number, from, to, delta, name. Only leaf rows (5-6 digit codes) kept.
        """
        return self._combine_tables(self.doc.tables)

    def budget_codes(self) -> set[str]:
        """The budget-item codes this letter changes (cheap; no LLM call)."""
        return set(self.extract_combined_table()[self.CODE_COLUMN])

    def changes_tracked(self, master_ids: set[str]) -> set[str]:
        """Codes in this letter that are tracked in master; empty means skip the letter."""
        return self.budget_codes() & master_ids

    # Metric columns of a budget line: (display Hebrew header, raw column prefix in
    # doc.tables). The report shows each as a מ-/ל- pair with a שינוי (delta) value.
    METRICS = [
        ("הוצאה", "הוצאה"),
        ("הוצאה מותנית", "הוצאה\nמותנית"),
        ("הרשאה להתחייב", "הרשאה\nלהתחייב"),
        ("שיא כח אדם", 'שכ"א'),
        ('עב"צ', 'עב"צ'),
    ]

    def _combine_tables(self, tables):
        """One row per 6-digit budget line (per occurrence, faithful to the PDF), with
        every metric's from/to value. Columns: number, name, then '<metric> from' /
        '<metric> to' for each METRIC. The report renders each row as מ-/ל- rows + שינוי.
        """
        import pandas as pd

        data_tables = [
            df for df in tables if any(str(c).endswith("delta") for c in df.columns)
        ]
        if not data_tables:
            raise ValueError("No data tables (with a 'delta' column) found to combine.")

        combined = pd.concat(data_tables, ignore_index=True)
        code_column = self._find_code_column(combined)
        codes = combined[code_column].astype(str).str.strip()
        # Keep only program-level rows (5-6 digit codes); drop 2-digit section and
        # 4-digit domain aggregates. Left-pad 5-digit codes to 6 digits.
        combined = combined[codes.str.fullmatch(r"\d{5,6}")].reset_index(drop=True)

        out = pd.DataFrame()
        out[self.CODE_COLUMN] = combined[code_column].astype(str).str.strip().str.zfill(6)
        name_column = self._find_name_column(combined)
        out[self.NAME_COLUMN] = (
            combined[name_column].map(self._clean_name).values
            if name_column is not None else ""
        )

        n = len(combined)
        for display, prefix in self.METRICS:
            raw_from = combined.get(f"{prefix} from")
            raw_to = combined.get(f"{prefix} to")
            out[f"{display} from"] = (
                pd.to_numeric(raw_from, errors="coerce").values
                if raw_from is not None else [pd.NA] * n)
            out[f"{display} to"] = (
                pd.to_numeric(raw_to, errors="coerce").values
                if raw_to is not None else [pd.NA] * n)
        return out.reset_index(drop=True)

    @classmethod
    def _find_name_column(cls, df):
        """Return the שם הפרט column, matched by its (now correct-order) header.

        PdfDocument returns cells in correct reading order, so the header is compared
        directly — whitespace-insensitively, which also absorbs any split final letter.
        """
        target = "".join(cls.NAME_SOURCE_HEADER.split())
        for column in df.columns:
            if "".join(str(column).split()) == target:
                return column
        return None

    @staticmethod
    def _clean_name(value) -> str:
        """Join a cell's lines into one string, dropping decorative separator lines.

        PdfDocument already returns cells in correct reading order (see
        PdfDocument._clean_cell), so no reversing happens here — this only tidies
        whitespace and drops lines with no letters/digits (===, ---, .-.-).
        """
        lines = (line.strip() for line in str(value).split("\n"))
        meaningful = [line for line in lines if re.search(r"\w", line)]
        return " ".join(meaningful).strip()

    @staticmethod
    def _find_code_column(df) -> str:
        """Return the column that holds budget item codes (the one with the most all-digit values).

        The column name comes from the PDF character-reversed, so it's identified by
        content rather than by its (unreliable) header text.
        """
        best_column = None
        best_score = -1
        for column in df.columns:
            values = df[column].astype(str).str.strip()
            score = values.str.fullmatch(r"\d{1,6}").sum()
            if score > best_score:
                best_score = score
                best_column = column
        if best_column is None or best_score == 0:
            raise ValueError("Could not find a budget item code column.")
        return best_column

    @classmethod
    def _find_change_column(cls, df, exclude: set):
        """Return the column of single (non-paired) signed change values, if any."""
        best_column = None
        best_score = -1
        for column in df.columns:
            if column in exclude:
                continue
            values = df[column].astype("string").fillna("").str.strip()
            score = sum(
                1 for v in values
                if v and "\n" not in v and PdfDocument._parse_number(v) is not None
            )
            if score > best_score:
                best_score = score
                best_column = column
        return best_column if best_score > 0 else None

    # ---- decision links, budget history, letterhead ----

    def _decision_links(self) -> list[str]:
        """Return the government-decision URLs (קישורים להחלטות ממשלה), one per decision.

        URLs are read from the raw page text (not the bidi-reversed text) so they read
        correctly, then de-duplicated by decision number (dec####), keeping the first
        URL seen for each — that yields the links printed in the request letter.
        """
        raw = self.doc.raw_text
        seen: dict[str, str] = {}
        for url in re.findall(r"https?://\S+", raw):
            url = url.rstrip('".,)”–- ')
            match = re.search(r"dec[_-]?(\d+)", url)
            if match and match.group(1) not in seen:
                seen[match.group(1)] = url
        return list(seen.values())

    def extract_budget_history(self):
        """Return the appendix 'היסטוריה תקציבית' table as a cleaned DataFrame.

        One row per program code, with the proposed change and the original/approved
        net-expenditure figures across the recent budget years. Returns an empty
        DataFrame if the letter has no such appendix.
        """
        import pandas as pd

        for page in self.doc.pages:
            if self.BUDGET_HISTORY_MARKER not in page.text:
                continue
            if page.tables:
                return self._clean_history_table(page.tables[0])
        return pd.DataFrame()

    def _clean_history_table(self, table):
        """Turn a raw budget-history table (header + data rows) into a clean DataFrame.

        Only the Hebrew header cells come out character-reversed and need flipping
        back; pdfplumber lays the numeric/code cells out left-to-right already
        (045101, -2,550, 786,257), so the data is kept verbatim.
        """
        import pandas as pd

        header, *data = table
        columns = [self._clean_name(cell) for cell in header]
        rows = [[(cell or "").strip() for cell in record] for record in data]
        return pd.DataFrame(rows, columns=columns)

    def extract_letterhead(self, lines: int = 3) -> list[str]:
        """Return the letter's first few Hebrew header lines, for use as a letterhead.

        These are lines like מדינת ישראל / האוצר - אגף התקציבים / תקציב פיתוח.
        pdfplumber splits a word's final letter off with a space ("התקציבי ם");
        a lone trailing Hebrew letter is re-joined before returning.
        """
        header_lines = []
        for line in self.doc.text.splitlines():
            cleaned = re.sub(r"(\S) ([֐-ת])(?=\s|$)", r"\1\2", line.strip())
            if re.search(r"[֐-ת]", cleaned):
                header_lines.append(cleaned)
            if len(header_lines) == lines:
                break
        return header_lines
