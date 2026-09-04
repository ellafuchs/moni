"""The summary page for one budget-transfer letter: HTML from a template, PDF via Chrome.

    Reports().write_summary(path, fields=..., table=..., letterhead=..., name_column=...)
        -> writes <path> (PDF) and the same page as <path>.html next to it

The page is a Jinja2 template (bot/templates/summary.html) filled with already-extracted
data; headless Chrome prints it to PDF, which gives correct Hebrew/bidi text, tables and
page breaks for free. Nothing here talks to the model or parses the letter.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
from datetime import date
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape
from markupsafe import Markup, escape

from summary_text import split_programs

TEMPLATES = Path(__file__).resolve().parent / "templates"

# Where Chrome may live; MONI_CHROME in the environment overrides everything.
CHROME_CANDIDATES = (
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "google-chrome", "google-chrome-stable", "chromium", "chromium-browser",
)


class ChromeNotFound(RuntimeError):
    """Raised when no Chrome/Chromium binary is available for PDF printing."""


def find_chrome() -> str:
    env = os.environ.get("MONI_CHROME")
    if env and (os.path.isfile(env) or shutil.which(env)):
        return env
    for candidate in CHROME_CANDIDATES:
        if os.path.isfile(candidate):
            return candidate
        found = shutil.which(candidate)
        if found:
            return found
    raise ChromeNotFound(
        "Google Chrome / Chromium not found. Install it, or set MONI_CHROME to the browser "
        "binary (e.g. MONI_CHROME=/usr/bin/chromium).")


def html_to_pdf(html_path, pdf_path, *, chrome: str | None = None, timeout: int = 120) -> str:
    """Print an HTML file to PDF with headless Chrome; returns the PDF path."""
    chrome = chrome or find_chrome()
    html_path, pdf_path = Path(html_path).resolve(), Path(pdf_path).resolve()
    cmd = [chrome, "--headless=new", "--disable-gpu", "--no-pdf-header-footer",
           f"--print-to-pdf={pdf_path}", html_path.as_uri()]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if proc.returncode != 0 or not pdf_path.is_file():
        raise RuntimeError(f"Chrome failed to print {html_path.name}: {proc.stderr[-800:]}")
    return str(pdf_path)


_SPLIT_LETTER = re.compile(r"(\S) ([\u0590-\u05ea])(?=\s|$)")


def join_split_letters(text: str) -> str:
    """Re-join a final Hebrew letter the PDF reader split off: 'נט ו' -> 'נטו'.

    A lone 'ו' after a number ('2856 ו 2857') is the conjunction, not a split letter,
    and is left alone; 'ש" ח' -> 'ש"ח' and 'התרבו ת' -> 'התרבות' are joined."""
    def fix(m):
        prev, letter = m.group(1), m.group(2)
        if letter == "ו" and prev.isdigit():
            return m.group(0)
        return prev + letter
    return _SPLIT_LETTER.sub(fix, str(text or ""))


_HISTORY_NUMBER = re.compile(r"^\s*(-?)([\d,]+)(-?)\s*$")


def format_history_cell(value) -> str:
    """'12,900-' or '-12,900' -> '−12,900'; '1,703,020' unchanged; other text as is."""
    text = str(value if value is not None else "").strip()
    m = _HISTORY_NUMBER.match(text)
    if not m or not m.group(2).replace(",", "").isdigit():
        return text
    negative = bool(m.group(1) or m.group(3))
    try:
        number = int(m.group(2).replace(",", ""))
    except ValueError:
        return text
    return f"−{number:,}" if negative and number else f"{number:,}"


def clean_header(text) -> str:
    """History headers copied from the letter: rejoin split letters, fix 'ב2025-' -> 'ב-2025'."""
    text = join_split_letters(text)
    text = re.sub(r"ב(\d{4})-", r"ב-\1", text)
    return re.sub(r"ב-\s+(\d{4})", r"ב-\1", text)


def _as_field_dict(fields) -> dict:
    """Accept a RequestFields (or a plain dict) and return the Hebrew-keyed dict."""
    if hasattr(fields, "model_dump"):
        return fields.model_dump(by_alias=True)
    return fields


_NUMBER = re.compile(r"(?<![\w.])(\d{1,3}(?:,\d{3})+|\d{5,6})(?![\w.,])")


_LABEL = re.compile(r"^(תיאור הת[ו]?כנית|מטרת השינוי(?: התקציבי)?|השפעה על כוח אדם)\s*:")


def bold_labels(text) -> Markup:
    """bold_numbers + a bold section label when the line starts with one."""
    safe = str(bold_numbers(text))
    return Markup(_LABEL.sub(lambda m: f"<b>{m.group(0)}</b>", safe, count=1))


def bold_numbers(text) -> Markup:
    """Escape text, then wrap amounts (1,234) and program codes (6 digits) in <b>."""
    safe = str(escape(text or ""))
    return Markup(_NUMBER.sub(lambda m: f"<b>{m.group(0)}</b>", safe))


class Reports:
    """Renders the per-letter summary page (HTML + PDF)."""

    # Rough USD->ILS rate for the cost line (an estimate, shown for information only).
    USD_TO_ILS = 3.7
    MASTER_COLUMN = "master"
    DECISION_LINKS_FIELD = "קישורים להחלטות ממשלה"

    def __init__(self):
        self.env = Environment(
            loader=FileSystemLoader(str(TEMPLATES)),
            autoescape=select_autoescape(["html"]),
            trim_blocks=True, lstrip_blocks=True,
        )
        self.env.filters["bold_numbers"] = bold_numbers
        self.env.filters["bold_labels"] = bold_labels

    # ---- data shaping ---------------------------------------------------

    @staticmethod
    def _fmt(value) -> str:
        """Thousands-separated integer string, '' for missing."""
        import pandas as pd
        try:
            return f"{float(value):,.0f}" if pd.notna(value) else ""
        except (TypeError, ValueError):
            return ""

    @staticmethod
    def _signed(value: float) -> str:
        s = f"{abs(value):,.0f}"
        return f"−{s}" if value < 0 else (f"+{s}" if value > 0 else "0")

    def _table_rows(self, table, name_column: str):
        """Rows for the budget table + {code: (name, total delta)} for master rows."""
        import pandas as pd

        metrics = [c[:-5] for c in table.columns if str(c).endswith(" from")]
        rows, master_totals, master_names_seen = [], {}, {}
        for _, rec in table.iterrows():
            code = str(rec.get("number", "")).strip()
            name = str(rec.get(name_column, "")).strip()
            master = str(rec.get(self.MASTER_COLUMN, "")).strip()
            delta = None
            for m in metrics:
                f_val, t_val = rec.get(f"{m} from"), rec.get(f"{m} to")
                if pd.notna(f_val) and pd.notna(t_val):
                    delta = float(t_val) - float(f_val)
                    break
            in_master = master == "כן"
            rows.append({
                "code": code, "name": name, "master": master, "in_master": in_master,
                "from_values": [self._fmt(rec.get(f"{m} from")) for m in metrics],
                "to_values": [self._fmt(rec.get(f"{m} to")) for m in metrics],
                "delta": self._signed(delta) if delta is not None else "",
            })
            if in_master:
                master_totals[code] = master_totals.get(code, 0.0) + (delta or 0.0)
                master_names_seen.setdefault(code, name)
        return metrics, rows, master_totals, master_names_seen

    @staticmethod
    def _split_request_number(value: str) -> tuple[str, str]:
        """'12-205, 54-219 | מספר פנייה לועדה: 65 עד 70' -> ('12-205, 54-219', '65 עד 70')."""
        value = value or ""
        numbers, committee = value, ""
        if "|" in value:
            numbers, committee = (part.strip() for part in value.split("|", 1))
        committee = re.sub(r"^מספר פני\S* לועדה\s*:?\s*", "", committee).strip()
        m = re.match(r"^מספר פני\S* לועדה\s*:?\s*(.*)$", numbers)
        if m:  # only the committee part was present
            numbers, committee = "", m.group(1).strip()
        return numbers.strip(), committee

    def build_context(self, *, fields, table, letterhead, name_column, budget_history=None,
                      source_url=None, llm_usage=None, relevant_programs=None,
                      request_id=None, coalition_reason="", master_names=None) -> dict:
        """Everything the template needs, as plain Python values."""
        fields = _as_field_dict(fields)
        master_names = master_names or {}
        metrics, table_rows, master_totals, seen_names = self._table_rows(table, name_column)
        matched = set(master_totals)

        master_rows = []
        for code, total in master_totals.items():
            # The master workbook's names are often truncated ("טיפול חוץ ביתי לאזרחים"),
            # the letter's table has the full ones — take the longest of what we have.
            candidates = [master_names.get(code, ""), (relevant_programs or {}).get(code, ""),
                          seen_names.get(code, "")]
            name = max((c.strip() for c in candidates if c), key=len, default="")
            master_rows.append({"code": code, "name": name, "delta": self._signed(total)})
        master_rows.sort(key=lambda r: -abs(float(r["delta"].replace(",", "").replace("−", "-").replace("+", "") or 0)))

        intro, programs = split_programs(fields.get("עיקרי הפנייה", ""))
        intro_lines = [join_split_letters(line) for line in intro.split("\n") if line.strip()]
        program_blocks = [{
            "code": p.code, "heading": join_split_letters(p.heading),
            "description": join_split_letters(p.description),
            "purpose": join_split_letters(p.purpose),
            "other": [join_split_letters(o) for o in p.other],
            "in_master": p.code in matched,
        } for p in programs]

        links = re.findall(r"https?://\S+", fields.get(self.DECISION_LINKS_FIELD, "") or "")
        numbers, committee = self._split_request_number(fields.get("מס' פנייה", ""))
        codes = [c.strip() for c in (fields.get("מס' תוכנית", "") or "").split(",") if c.strip()]

        history = []
        for title, df in (budget_history or []):
            if df is None or getattr(df, "empty", True):
                continue
            columns = [clean_header(c) for c in df.columns]
            rows = []
            for _, rec in df.iterrows():
                cells = [format_history_cell(rec[c]) for c in df.columns]
                rows.append({"cells": cells, "in_master": cells[0].strip() in matched})
            history.append({"title": clean_header(title), "columns": columns, "rows": rows})

        usage = None
        if llm_usage:
            tokens = llm_usage.get("input_tokens", 0) + llm_usage.get("output_tokens", 0)
            cost = llm_usage.get("cost_usd")
            usage = {"model": llm_usage.get("model", ""), "tokens": f"{tokens:,}",
                     "cost_ils": f"{cost * self.USD_TO_ILS:.2f}" if cost is not None else None}

        return {
            "request_id": request_id or "",
            "generated": date.today().strftime("%d/%m/%Y"),
            "letterhead_line": " · ".join(join_split_letters(h) for h in (letterhead or [])),
            "date": fields.get("תאריך", ""),
            "request_numbers": numbers, "committee": committee, "program_codes": codes,
            "relevant": bool(matched),
            "coalition": fields.get("האם יש התייחסות לכספים קואליציונים", ""),
            "coalition_reason": coalition_reason or "",
            "staffing": fields.get("שינויים בכוח אדם", ""),
            "master_rows": master_rows,
            "intro_lines": intro_lines, "programs": program_blocks,
            "links": links,
            "metrics": metrics, "table_rows": table_rows,
            "history": history,
            "source_url": source_url or "", "usage": usage,
        }

    # ---- rendering --------------------------------------------------------

    def render_summary_html(self, **kwargs) -> str:
        return self.env.get_template("summary.html").render(**self.build_context(**kwargs))

    def write_summary(self, output_path, *, fields, table, letterhead, name_column,
                      budget_history=None, source_url=None, llm_usage=None,
                      relevant_programs=None, request_id=None, coalition_reason="",
                      master_names=None) -> str:
        """Write the summary as <output_path> (PDF) and <output_path>.html; return the PDF path."""
        pdf_path = Path(output_path)
        html_path = pdf_path.with_suffix(".html")
        html = self.render_summary_html(
            fields=fields, table=table, letterhead=letterhead, name_column=name_column,
            budget_history=budget_history, source_url=source_url, llm_usage=llm_usage,
            relevant_programs=relevant_programs, request_id=request_id,
            coalition_reason=coalition_reason, master_names=master_names)
        html_path.parent.mkdir(parents=True, exist_ok=True)
        html_path.write_text(html, encoding="utf-8")
        return html_to_pdf(html_path, pdf_path)
