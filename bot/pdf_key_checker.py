from __future__ import annotations

import hashlib
import json
import logging
import re
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

import pandas as pd
import pdfplumber
from dotenv import load_dotenv
from openai import OpenAI

# Ensure OPENAI_API_KEY (and friends) are loaded from .env even when this module
# is run/imported standalone rather than through main.py.
load_dotenv()

# pdfminer (used by pdfplumber) logs a noisy "Could not get FontBBox" warning for
# PDFs whose font descriptors omit a FontBBox; it doesn't affect text/table output.
logging.getLogger("pdfminer").setLevel(logging.ERROR)


class PdfTableExtractor:
    """Extracts budget tables from the pages of a local PDF that contain HEADER."""

    HEADER = "האוצר - אגף התקציבים"

    # Column holding the budget item code (מספר פרט). Leaf-level program rows
    # have a 6-digit code; shorter codes are section/sub-section totals.
    CODE_COLUMN = "number"

    # Column holding the budget item name (שם הפרט). Identified by its header,
    # which pdfplumber emits character-reversed. Output as NAME_COLUMN.
    NAME_SOURCE_HEADER = "שם הפרט"
    NAME_COLUMN = "name"

    # The one REQUEST_FIELD whose value is a list of government-decision URLs;
    # rendered specially (as left-to-right link rows) by the summary writer.
    DECISION_LINKS_FIELD = "קישורים להחלטות ממשלה"

    # The summary fields of a budget-transfer request letter, as keys for
    # extract_request_fields(). Order matches the request template.
    REQUEST_FIELDS = (
        "תאריך",
        "מס' פנייה",
        "מס' תוכנית",
        "עיקרי הפנייה",
        "עיקרי הפנייה בחלוקה לתוכניות",
        DECISION_LINKS_FIELD,
        "האם יש התייחסות לכספים קואליציונים",
        "שינויים בכוח אדם",
    )

    # The source PDFs draw some column borders as two near-overlapping vertical
    # lines, which makes pdfplumber split one logical column into a duplicate
    # sliver column. Snapping nearby vertical lines together merges them.
    TABLE_SETTINGS = {"snap_x_tolerance": 10}

    # Max characters of letter text sent to the LLM. The request fields sit in the
    # leading narrative; capping here keeps every request under gpt-4o's 30k
    # tokens-per-minute limit (~0.42 tokens/char for this Hebrew text).
    MAX_LLM_CHARS = 20000

    def __init__(self, pdf_source, *, use_first_row_as_header: bool = True):
        self.use_first_row_as_header = use_first_row_as_header
        self.pdf_source = pdf_source

    def _resolve_source(self, pdf_source):
        """Return pdf_source, or the path given at construction if none is passed."""
        return pdf_source if pdf_source is not None else self.pdf_source

    @staticmethod
    def _as_openable(pdf_source):
        """Return something pdfplumber.open can read.

        pdfplumber.open only accepts local paths or file-like objects, not URLs,
        so http(s) sources are downloaded once into an on-disk cache (keyed by URL)
        and reused on later runs — the download is the slow part, not the parsing.
        """
        if isinstance(pdf_source, str) and pdf_source.startswith(("http://", "https://")):
            cache_dir = Path(tempfile.gettempdir()) / "pdf_key_checker_cache"
            cache_dir.mkdir(exist_ok=True)
            cache_file = cache_dir / (hashlib.sha256(pdf_source.encode()).hexdigest() + ".pdf")
            if not cache_file.exists():
                cache_file.write_bytes(PdfTableExtractor._download(pdf_source))
            return str(cache_file)
        return pdf_source

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

    def _is_header_page(self, page) -> bool:
        """True if the page contains HEADER (i.e. a budget-table page).

        pdfplumber doesn't apply bidi reordering, so Hebrew lines come out
        character-reversed; reverse each line back before searching for HEADER.
        Word breaks can shift across line wraps, so whitespace is stripped
        entirely (not just collapsed) before comparing.
        """
        normalized_header = "".join(self.HEADER.split())
        lines = (line[::-1] for line in (page.extract_text() or "").splitlines())
        page_text = "".join("".join(lines).split())
        return normalized_header in page_text

    def extract_text(self, pdf_source=None, *, method: str = "parse",
                     api_key=None, model=None) -> dict[str, str]:
        """Return the request-letter summary fields as a {field: result} dict.

        Each of the REQUEST_FIELDS (תאריך, מס' פנייה, …) is a key and its extracted
        value is the value. This is a convenience wrapper over extract_request_fields:
        method="parse" (default) uses local text rules; method="llm" asks OpenAI.
        Use extract_request_fields directly for the same result with explicit naming.
        """
        return self.extract_request_fields(pdf_source, method=method, api_key=api_key, model=model)

    def extract_page_text(self, pdf_source=None, *, reverse_lines: bool = True) -> str:
        """Return the text of the non-table pages only (the narrative/cover pages).

        Budget-table pages (those containing HEADER) are skipped. pdfplumber doesn't
        apply bidi reordering, so Hebrew comes out character-reversed; reverse_lines=True
        flips each line back so Hebrew reads correctly (default). Set reverse_lines=False
        to get pdfplumber's raw text unchanged.
        """
        pdf_source = self._resolve_source(pdf_source)
        with pdfplumber.open(self._as_openable(pdf_source)) as pdf:
            pages = []
            for page in pdf.pages:
                if self._is_header_page(page):
                    continue
                text = page.extract_text() or ""
                if reverse_lines:
                    text = "\n".join(line[::-1] for line in text.splitlines())
                pages.append(text)
        return "\n".join(pages)

    def _all_pages_text(self, pdf_source) -> str:
        """Readable text of EVERY page (including table pages) — used for field parsing.

        The request letter (with עיקרי הפנייה etc.) shares a page with the budget
        table, so unlike extract_text() this does not skip header pages.
        """
        with pdfplumber.open(self._as_openable(pdf_source)) as pdf:
            pages = (
                "\n".join(line[::-1] for line in (page.extract_text() or "").splitlines())
                for page in pdf.pages
            )
            return "\n".join(pages)

    def extract_request_fields(self, pdf_source=None, *, method: str = "parse",
                               api_key=None, model=None) -> dict[str, str]:
        """Extract the request-letter summary fields (REQUEST_FIELDS) as a dict.

        method="parse" (default) uses local text rules — no API calls.
        method="llm" sends the letter text to OpenAI and returns its structured answer;
        pass api_key/model (e.g. from ConfigManager) or rely on the environment.
        """
        pdf_source = self._resolve_source(pdf_source)
        text = self._all_pages_text(pdf_source)
        if method == "parse":
            fields = self._parse_request_fields(text)
        elif method == "llm":
            fields = self._llm_request_fields(text, api_key=api_key, model=model)
        else:
            raise ValueError(f"Unknown method {method!r}; use 'parse' or 'llm'.")
        # Decision links must come from the raw (un-reversed) text, where URLs read
        # left-to-right; in the bidi-flipped text they come out backwards.
        fields[self.DECISION_LINKS_FIELD] = "\n".join(
            f"{i}. {url}" for i, url in enumerate(self._decision_links(pdf_source), 1)
        )
        return fields

    def _decision_links(self, pdf_source) -> list[str]:
        """Return the government-decision URLs (קישורים להחלטות ממשלה), one per decision.

        URLs are read from the raw page text (not the bidi-reversed text) so they
        read correctly, then de-duplicated by decision number (dec####), keeping the
        first URL seen for each — that yields the links printed in the request letter.
        """
        with pdfplumber.open(self._as_openable(pdf_source)) as pdf:
            raw = "\n".join((page.extract_text() or "") for page in pdf.pages)
        seen: dict[str, str] = {}
        for url in re.findall(r"https?://\S+", raw):
            url = url.rstrip('".,)”–- ')
            match = re.search(r"dec[_-]?(\d+)", url)
            if match and match.group(1) not in seen:
                seen[match.group(1)] = url
        return list(seen.values())

    @staticmethod
    def _section(text: str, start: str, ends: list[str]) -> str:
        """Return the text between `start` and the earliest of `ends` that follows it."""
        i = text.find(start)
        if i == -1:
            return ""
        i += len(start)
        stop = len(text)
        for end in ends:
            j = text.find(end, i)
            if j != -1:
                stop = min(stop, j)
        return text[i:stop].strip()

    @staticmethod
    def _unreverse_numbers(text: str) -> str:
        """Flip each run of digits (with embedded , . /) back to reading order.

        Numbers embedded in the bidi-reversed Hebrew text come out backwards
        (011540, 832,84); reversing each numeric run restores them (045110, 48,238).
        """
        return re.sub(r"\d[\d.,/]*", lambda m: m.group(0)[::-1], text)

    def _parse_request_fields(self, text: str) -> dict[str, str]:
        """Best-effort extraction of REQUEST_FIELDS from the readable letter text."""
        fields = {name: "" for name in self.REQUEST_FIELDS}

        def first(pattern: str) -> str:
            m = re.search(pattern, text)
            return m.group(1).strip() if m else ""

        # The date prints on the budget-table pages as "תאריך הבקשה: 6202/50/01";
        # the line is bidi-reversed, so reverse the captured token back (-> 10/05/2026).
        date_match = re.search(r"תאריך הבקשה\s*:?\s*(\d{1,4}[./]\d{1,2}[./]\d{2,4})", text)
        fields["תאריך"] = date_match.group(1)[::-1] if date_match else ""
        # Each sub-request is headed "בקשה מספר NNN-NN"; the letter bundles several,
        # so collect them all (de-duplicated, in order). The digits are bidi-reversed
        # in the text (202-15 -> 202-51), so un-reverse each numeric run.
        request_numbers = list(dict.fromkeys(
            self._unreverse_numbers(m)
            for m in re.findall(r"בקשה מספר\s*(\d{3}-\d{2})", text)
        ))
        # Also capture the committee request number ("מספר פניה לועדה: 6 עד 9") — both
        # the per-request numbers and the committee number belong in this field.
        committee = re.search(r"מספר פני\S* לועדה\s*:?\s*([^\n]+)", text)
        parts = []
        if request_numbers:
            parts.append(", ".join(request_numbers))
        if committee:
            parts.append(f"מספר פנייה לועדה: {committee.group(1).strip()}")
        fields["מס' פנייה"] = " | ".join(parts)
        fields["עיקרי הפנייה"] = self._section(
            text, "עיקרי הפנייה:", ["מטרת השינוי", "האישור ייכנס", "בכבוד רב"]
        )
        # The breakdown / program list is anchored differently across letter formats:
        # older letters head it "עיקרי הפנייה בחלוקה (לתכניות/לתוכניות)"; newer ones
        # list the programs straight after "מטרת השינוי". Use whichever appears first
        # so both formats are handled. It runs through the program-description block,
        # stopping at the next section ("השפעה על כוח אדם" / links / the sign-off).
        start_anchor = next(
            (a for a in ("עיקרי הפנייה בחלוקה", "מטרת השינוי") if a in text), None
        )
        breakdown_raw = self._section(
            text, start_anchor,
            ["השפעה על", "קישורים להחלטות ממשלה",
             "האישור ייכנס", "בכבוד רב", "נספח"]
        ) if start_anchor else ""
        # Program codes appear either as "תכנית NNNNNN" (old format) or as a
        # line-leading "NNNNNN:" prefix (new format). Digits are bidi-reversed
        # (201103 -> 301102), so un-reverse each. De-duplicate, preserving order.
        codes = re.findall(r"תו?כנית\s*:?\s*(\d{5,6})", breakdown_raw)
        codes += re.findall(r"(?m)^\s*(\d{5,6})\s*:", breakdown_raw)
        programs = list(dict.fromkeys(code[::-1] for code in codes))
        fields["מס' תוכנית"] = ", ".join(programs)
        # Drop the program-number prefix ("תכנית NNNNNN:" or a leading "NNNNNN:") from
        # the breakdown text — it's already its own field (מס' תוכנית).
        breakdown = self._unreverse_numbers(breakdown_raw)
        breakdown = re.sub(r"תו?כנית\s*\d{5,6}\s*:?\s*", "", breakdown)
        breakdown = re.sub(r"(?m)^\s*\d{5,6}\s*:\s*", "", breakdown)
        fields["עיקרי הפנייה בחלוקה לתוכניות"] = breakdown
        # Coalition-funds detection. Every letter contains the boilerplate question
        # "האם יש התייחסות לכספים קואליציונים", so a bare mention of קואליצ proves
        # nothing. A letter genuinely *refers* to coalition funds when it substantively
        # discusses coalition agreements or a budget defined as coalition funding —
        # "הסכמים קואליציוניים", "תקציב/תקצוב ... קואליציוני", "כתקציב קואליציוני", or a
        # statement using "כספים קואליציוניים" (excluding the "התייחסות לכספים..."
        # question). An explicit "אינה/לא כולל כספים קואליציוניים" is a negative and wins.
        if re.search(r"(אינה|לא)\s+כולל\S*\s+כספים קואליציוני", text):
            fields["האם יש התייחסות לכספים קואליציונים"] = "לא"
        elif re.search(
            r"הסכמ\S*\s+\S*קואליצי"               # הסכמים קואליציוניים
            r"|תקצ\S*\s+\S*קואליצי"                # תקציב/תקצוב ... קואליציוני
            r"|כתקציב\s+קואליצי"                   # הוגדר כתקציב קואליציוני
            r"|(?<!התייחסות ל)כספ\S*\s+קואליצי",   # substantive "כספים קואליציוניים"
            text,
        ):
            fields["האם יש התייחסות לכספים קואליציונים"] = "כן"
        else:
            fields["האם יש התייחסות לכספים קואליציונים"] = "לא"
        # Only an explicit "שינוי בכוח אדם" phrase counts — the bare "כוח אדם" / שכ"א
        # column headers in the table would otherwise produce false positives.
        fields["שינויים בכוח אדם"] = "כן" if re.search(r"שינוי\S* בכוח אדם", text) else "לא"
        return fields

    def _llm_request_fields(self, text: str, *, api_key=None, model=None) -> dict[str, str]:
        """Extract REQUEST_FIELDS by asking OpenAI, returning its structured JSON answer."""
        # pdfplumber returns Hebrew right-to-left; flipping lines back fixes the
        # Hebrew but leaves every number reversed (301102 -> 201103, 03/05/2026 ->
        # 6202/50/30). Un-reverse the numeric runs so the LLM sees correct digits.
        text = self._unreverse_numbers(text)
        # The request-letter fields all live in the narrative at the top of the
        # document; the bulk of a multi-page letter is repeated budget tables. A
        # full 16-page letter is ~90k chars (~38k tokens), over gpt-4o's 30k
        # tokens-per-minute limit, so the request would 429. Cap the input to the
        # leading slice that holds the letter, keeping every call comfortably small.
        text = text[: self.MAX_LLM_CHARS]
        client = OpenAI(api_key=api_key) if api_key else OpenAI()
        schema = {
            "type": "object",
            "properties": {
                name: {"type": "string"} for name in self.REQUEST_FIELDS
            },
            "required": list(self.REQUEST_FIELDS),
            "additionalProperties": False,
        }
        prompt = (
            "הטקסט הבא הוא פנייה תקציבית של אגף התקציבים. חלץ את השדות המבוקשים. "
            "אם שדה אינו מופיע בטקסט, החזר עבורו מחרוזת ריקה. הקפד על הכללים הבאים:\n"
            "- תאריך: השתמש ב'תאריך הבקשה' המופיע על דפי הטבלה (בפורמט DD/MM/YYYY, "
            "למשל 10/05/2026).\n"
            "- מס' פנייה: החזר רק את המספרים עצמם (בפורמט NNN-NN) של כל מספרי הבקשה, "
            "מופרדים בפסיק, ללא המילים 'בקשה מספר'. לאחר מכן הוסף 'מספר פנייה לועדה: X'. "
            "למשל: '202-51, 202-20, 002-07, 206-04 | מספר פנייה לועדה: 6 עד 9'.\n"
            "- האם יש התייחסות לכספים קואליציונים: ענה 'כן' או 'לא' בלבד, לפי ההצהרה "
            "המפורשת במכתב (אם נכתב 'אינה כוללת כספים קואליציוניים' ענה 'לא').\n"
            "- מס' תוכנית: רק קודי התוכניות שמופיעים ככותרת 'תכנית NNNNNN:' בתוך מקטע "
            "'עיקרי הפנייה בחלוקה לתוכניות'. אל תיקח קודים מתוך טבלאות התקציב. בדרך כלל "
            "זהו  את כל הקודים  ואם יש יותר מאחד להחזיר ברשימה.\n"
            "- עיקרי הפנייה: הפסקה הקצרה שאחרי הכותרת 'עיקרי הפנייה:' בלבד.\n"
            "- עיקרי הפנייה בחלוקה לתוכניות: כל הטקסט שאחרי הכותרת 'עיקרי הפנייה בחלוקה "
            "לתוכניות', כולל שורות ה'תכנית NNNNNN: ...' ותיאוריהן.\n"
            "- שינויים בכוח אדם: ענה 'כן' או 'לא' בלבד.\n\n" + text
        )
        # OpenAI returns 429 when the account is rate-limited and 5xx on transient
        # server errors; both clear on their own, so retry with exponential backoff
        # (1, 2, 4, 8, 16s) before giving up rather than failing the whole run.
        last_error: Exception | None = None
        for attempt in range(5):
            try:
                response = client.chat.completions.create(
                    model=model or "gpt-4o",
                    messages=[{"role": "user", "content": prompt}],
                    response_format={
                        "type": "json_schema",
                        "json_schema": {
                            "name": "request_fields",
                            "schema": schema,
                            "strict": True,
                        },
                    },
                )
                return json.loads(response.choices[0].message.content)
            except Exception as error:  # noqa: BLE001 - retry only transient classes
                last_error = error
                status = getattr(error, "status_code", None)
                transient = status in (429, 500, 502, 503) or "429" in str(error)
                if transient and attempt < 4:
                    time.sleep(2 ** attempt)
                    continue
                raise
        raise RuntimeError(f"OpenAI request failed after retries: {last_error}")

    def extract_tables_from_header_pages(self, pdf_source=None) -> list[list[list[str | None]]]:
        """Return all tables found on every page that contains HEADER."""
        pdf_source = self._resolve_source(pdf_source)
        with pdfplumber.open(self._as_openable(pdf_source)) as pdf:
            if not pdf.pages:
                raise ValueError("PDF has no pages")

            header_pages = [page for page in pdf.pages if self._is_header_page(page)]
            if not header_pages:
                raise ValueError(f"Header '{self.HEADER}' not found in any page of the PDF")

            tables = []
            for page in header_pages:
                tables.extend(page.extract_tables(self.TABLE_SETTINGS) or [])

        return tables

    def table_to_dataframe(self, table: list[list[str | None]]) -> pd.DataFrame:
        """Convert a raw table (list of rows) into a pandas DataFrame."""
        if not table:
            return pd.DataFrame()

        cleaned = [
            [str(cell).strip() if cell is not None else "" for cell in row]
            for row in table
        ]

        if self.use_first_row_as_header and len(cleaned) > 1:
            columns = cleaned[0]
            data = cleaned[1:]
            return pd.DataFrame(data, columns=columns)

        return pd.DataFrame(cleaned)

    @staticmethod
    def _parse_number(text: str) -> float | None:
        """Parse a numeric string like '1,539,800' or '461.5' into a float, else None."""
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

    def extract_tables(self, pdf_source=None) -> list[pd.DataFrame]:
        """Read a local PDF and return each header-page table as a DataFrame."""
        pdf_source = self._resolve_source(pdf_source)
        tables = self.extract_tables_from_header_pages(pdf_source)
        if not tables:
            raise ValueError("No tables found on the header pages.")
        return [self.split_stacked_columns(self.table_to_dataframe(t)) for t in tables]

    def combine_tables(self, tables: list[pd.DataFrame]) -> pd.DataFrame:
        """Stack the data tables into one clean table: code + expense from/to/delta + name.

        Only leaf rows (6-digit codes) are kept. For rows without a from→to pair
        (e.g. the reserve line), the single change value is used as 'from' (negated)
        and 'to' is set to 0.
        """
        data_tables = [
            df for df in tables if any(str(c).endswith("delta") for c in df.columns)
        ]
        if not data_tables:
            raise ValueError("No data tables (with a 'delta' column) found to combine.")

        combined = pd.concat(data_tables, ignore_index=True)
        code_column = self._find_code_column(combined)
        codes = combined[code_column].astype(str).str.strip()
        # Leaf rows have a 5- or 6-digit code; left-pad 5-digit codes to 6 digits.
        combined = combined[codes.str.fullmatch(r"\d{5,6}")].reset_index(drop=True)
        combined[code_column] = (
            combined[code_column].astype(str).str.strip().str.zfill(6)
        )

        # Find the from/to columns by their "from"/"to" suffix, picking the money
        # group with the most complete from→to pairs (the main expense column).
        from_col, to_col = self._find_from_to_columns(combined)
        frm = pd.to_numeric(combined.get(from_col), errors="coerce")
        to = pd.to_numeric(combined.get(to_col), errors="coerce")

        # Reserve rows have no from→to pair, only a single change value: use it as
        # 'from' (negated, so +37,018 becomes -37,018) and set 'to' to 0.
        exclude = {code_column} | {
            c for c in combined.columns if str(c).endswith((" from", " to", " delta"))
        }
        change_column = self._find_change_column(combined, exclude)
        if change_column is not None:
            change = pd.to_numeric(
                combined[change_column].map(lambda v: self._parse_number(str(v))),
                errors="coerce",
            )
            missing = frm.isna()
            frm = frm.mask(missing, -change)
            to = to.mask(missing, 0)

        name_column = self._find_name_column(combined)
        if name_column is not None:
            names = combined[name_column].map(self._clean_name)
        else:
            names = pd.Series("", index=combined.index)

        result = pd.DataFrame({
            self.CODE_COLUMN: combined[code_column],
            "from": frm,
            "to": to,
        })
        result["delta"] = result["to"] - result["from"]
        result[self.NAME_COLUMN] = names.values
        return result.fillna({"from": 0, "to": 0, "delta": 0}).reset_index(drop=True)

    @classmethod
    def _find_name_column(cls, df: pd.DataFrame) -> str | None:
        """Return the שם הפרט column, matched by its character-reversed header."""
        target = "".join(cls.NAME_SOURCE_HEADER.split())
        for column in df.columns:
            reversed_header = str(column)[::-1]
            if "".join(reversed_header.split()) == target:
                return column
        return None

    @staticmethod
    def _clean_name(value) -> str:
        """Reverse the char-reversed name text and drop decorative separator lines."""
        lines = (line[::-1].strip() for line in str(value).split("\n"))
        # Keep only lines containing an actual letter/digit (drop ===, ---, .-.-).
        meaningful = [line for line in lines if re.search(r"\w", line)]
        return " ".join(meaningful).strip()

    @staticmethod
    def _find_from_to_columns(df: pd.DataFrame) -> tuple[str | None, str | None]:
        """Return the ('<x> from', '<x> to') pair with the most complete numeric pairs.

        split_stacked_columns produces a '<name> from'/'<name> to' pair for every
        stacked money column; the main expense column is the one whose from→to
        pairs are populated on the most rows.
        """
        best_pair: tuple[str | None, str | None] = (None, None)
        best_score = -1
        for column in df.columns:
            if not str(column).endswith(" from"):
                continue
            to_col = str(column)[: -len(" from")] + " to"
            if to_col not in df.columns:
                continue
            frm = pd.to_numeric(df[column], errors="coerce")
            to = pd.to_numeric(df[to_col], errors="coerce")
            score = int((frm.notna() & to.notna()).sum())
            if score > best_score:
                best_score = score
                best_pair = (column, to_col)
        return best_pair

    @staticmethod
    def _find_code_column(df: pd.DataFrame) -> str:
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
    def _find_change_column(cls, df: pd.DataFrame, exclude: set) -> str | None:
        """Return the column of single (non-paired) signed change values, if any."""
        best_column = None
        best_score = -1
        for column in df.columns:
            if column in exclude:
                continue
            values = df[column].astype("string").fillna("").str.strip()
            score = sum(
                1 for v in values if v and "\n" not in v and cls._parse_number(v) is not None
            )
            if score > best_score:
                best_score = score
                best_column = column
        return best_column if best_score > 0 else None

    def extract_combined_table(self, pdf_source=None) -> pd.DataFrame:
        """Read a local PDF and return its data tables combined into one clean table."""
        pdf_source = self._resolve_source(pdf_source)
        return self.combine_tables(self.extract_tables(pdf_source))

    # The appendix table (נספח לדברי ההסבר) listing each program's multi-year
    # net-expenditure history; its page is found by this title in the readable text.
    BUDGET_HISTORY_TITLE = "היסטוריה תקציבית של הפנייה - הוצאה נטו"
    BUDGET_HISTORY_MARKER = "היסטוריה תקציבית"

    def extract_budget_history(self, pdf_source=None) -> pd.DataFrame:
        """Return the appendix 'היסטוריה תקציבית' table as a cleaned DataFrame.

        One row per program code, with the proposed change and the original/approved
        net-expenditure figures across the recent budget years. pdfplumber applies no
        bidi, so each cell comes out character-reversed; codes and numbers are reversed
        back to reading order (a full-cell reverse also fixes the trailing-minus sign).

        Returns an empty DataFrame if the letter has no such appendix.
        """
        pdf_source = self._resolve_source(pdf_source)
        with pdfplumber.open(self._as_openable(pdf_source)) as pdf:
            for page in pdf.pages:
                readable = "\n".join(
                    line[::-1] for line in (page.extract_text() or "").splitlines()
                )
                if self.BUDGET_HISTORY_MARKER not in readable:
                    continue
                tables = page.extract_tables(self.TABLE_SETTINGS)
                if tables:
                    return self._clean_history_table(tables[0])
        return pd.DataFrame()

    def _clean_history_table(self, table: list[list[str | None]]) -> pd.DataFrame:
        """Turn a raw budget-history table (header + data rows) into a clean DataFrame.

        Headers keep their original Hebrew (reversed back to reading order); the first
        column is the program code (kept as a string), the rest are net-expenditure
        amounts (reversed, then formatted with thousands separators).
        """
        header, *data = table
        columns = [self._clean_name(cell) for cell in header]
        rows = []
        for record in data:
            cleaned = []
            for index, cell in enumerate(record):
                value = (cell or "").strip()[::-1]  # one token per cell, char-reversed
                if index == 0:
                    cleaned.append(value)  # program code — keep as-is
                    continue
                number = self._parse_number(value)
                cleaned.append(f"{number:,.0f}" if number is not None else value)
            rows.append(cleaned)
        return pd.DataFrame(rows, columns=columns)

    # Font with Hebrew glyphs, used by the generated PDFs (see reports.Reports).
    HEBREW_FONT_PATH = "/Library/Fonts/Arial Unicode.ttf"

    def extract_letterhead(self, pdf_source=None, lines: int = 3) -> list[str]:
        """Return the letter's first few Hebrew header lines, for use as a letterhead.

        These are lines like מדינת ישראל / האוצר - אגף התקציבים / תקציב פיתוח.
        pdfplumber splits a word's final letter off with a space ("התקציבי ם");
        a lone trailing Hebrew letter is re-joined before returning.
        """
        pdf_source = self._resolve_source(pdf_source)
        header_lines = []
        for line in self._all_pages_text(pdf_source).splitlines():
            cleaned = re.sub(r"(\S) ([֐-ת])(?=\s|$)", r"\1\2", line.strip())
            if re.search(r"[֐-ת]", cleaned):
                header_lines.append(cleaned)
            if len(header_lines) == lines:
                break
        return header_lines


if __name__ == "__main__":
    SOURCE_URL = "https://fs.knesset.gov.il/globaldocs/FINANCE/0e793046-014d-f111-a13e-005056aa7c52/4_0e793046-014d-f111-a13e-005056aa7c52_13_21560.pdf"

    extractor = PdfTableExtractor(SOURCE_URL)
    text_1 = extractor.extract_text()
    text_2 = extractor.extract_text(method="llm")


    combined = extractor.extract_combined_table()
    print(combined.to_string(index=False))
