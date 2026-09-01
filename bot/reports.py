"""PDF rendering for budget-transfer letters — one class, two PDFs.

`Reports` writes the two Hebrew PDFs the project produces:

    Reports().write_summary(path, fields=..., table=..., letterhead=..., name_column=...)
        -> a per-letter summary: request fields on top, combined budget table below.

    Reports().write_comparison(path, name, source, parse, llm)
        -> a parse-vs-llm field comparison table (the "are they the same?" PDF).

Both take already-extracted data (a {field: value} dict, and for the summary a
combined-table DataFrame) — the rendering is kept separate from the extraction
(BudgetLetter). reportlab and python-bidi are imported lazily, so the
rest of the project works without them installed.
"""
from __future__ import annotations

import re
from pathlib import Path

from budget_letter import BudgetLetter


def _as_field_dict(fields) -> dict:
    """Accept a RequestFields (or a plain dict) and return the Hebrew-keyed dict."""
    if hasattr(fields, "model_dump"):
        return fields.model_dump(by_alias=True)
    return fields


class Reports:
    """Renders the summary and comparison PDFs; shares the Hebrew/RTL plumbing."""

    FONT_NAME = "Hebrew"
    FONT_PATH = BudgetLetter.HEBREW_FONT_PATH
    REQUEST_FIELDS = BudgetLetter.REQUEST_FIELDS
    DECISION_LINKS_FIELD = BudgetLetter.DECISION_LINKS_FIELD

    # Rough USD->ILS rate for the LLM-cost footer (both currencies are shown). The
    # cost is only an estimate; update this to your working rate as needed.
    USD_TO_ILS = 3.7

    def __init__(self):
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.ttfonts import TTFont

        pdfmetrics.registerFont(TTFont(self.FONT_NAME, self.FONT_PATH))

    # ---- shared helpers -------------------------------------------------

    @staticmethod
    def _rtl(value) -> str:
        """Reorder a (possibly Hebrew) string for visual right-to-left display.

        base_dir='R' forces a right-to-left base direction, so a Hebrew label stays
        first (rightmost) even when its value is all Latin digits (e.g. 'מס' תוכנית: 231039…')."""
        from bidi.algorithm import get_display

        return get_display(str(value), base_dir="R")

    # Cosmetic fixes for split-word artifacts from the source PDF's text extraction (a
    # space wrongly inserted mid-word). Applied at render time only — the extracted/golden
    # data is left untouched. Currently one known case: 'השי נוי' -> 'השינוי'.
    _WORD_FIXES = ((re.compile(r"הש[יי] נוי"), "השינוי"),)

    @classmethod
    def _normalize_text(cls, text: str) -> str:
        """Repair known split-word artifacts for display (see _WORD_FIXES)."""
        for pattern, repl in cls._WORD_FIXES:
            text = pattern.sub(repl, text)
        return text

    def _cell(self, value, style):
        """A wrapping cell that keeps a value's line breaks, reordered for RTL."""
        from reportlab.platypus import Paragraph
        from xml.sax.saxutils import escape

        lines = str(value).split("\n")
        html = "<br/>".join(escape(self._rtl(line)) for line in lines)
        return Paragraph(html or " ", style)

    # Section labels that open each program's narrative; bolded for scannability. The
    # optional space before the colon covers the 'מטרת השינוי :' variant seen in letters.
    _SECTION_LABEL_RE = re.compile(r"^\s*(תיאור הת[ו]?כנית\s*:|מטרת השינוי\s*:)")

    def _section_label(self, logical_line: str) -> str | None:
        """The section label a logical line opens with (for bolding), else None."""
        m = self._SECTION_LABEL_RE.match(logical_line)
        return m.group(1) if m else None

    def _wrap_rtl_html(self, text: str, max_width: float, *, size: int = 11,
                       bold_labels: bool = False) -> str:
        """Wrap Hebrew text to `max_width` points, then bidi-reorder each display line.

        reportlab has no bidi engine, so Hebrew must be pre-reordered with get_display
        (see _rtl). But reordering a whole long line and then letting reportlab wrap it
        emits the wrapped rows bottom-to-top — the paragraph reads backwards. So instead
        we wrap the LOGICAL text ourselves (greedy, by measured width), which keeps the
        display lines in top-to-bottom reading order, and get_display each resulting line
        so its glyphs are correct too. Returns <br/>-joined HTML for one Paragraph whose
        lines already fit, so reportlab does not re-wrap (and re-reverse) them."""
        from reportlab.pdfbase.pdfmetrics import stringWidth
        from xml.sax.saxutils import escape

        font = self.FONT_NAME
        # Wrap a little short of the true column width: stringWidth and reportlab's own
        # line-fill can disagree by up to a space's width, and any line that ends up even
        # 1pt too wide gets re-wrapped by reportlab — which reverses its last word (the
        # very bug we are avoiding). The margin keeps every emitted line safely on one row.
        usable = max_width - 12 - stringWidth("  ", font, size)
        rows: list[tuple[str, str | None]] = []  # (logical display row, label or None)
        for logical in str(text).split("\n"):
            label = self._section_label(logical) if bold_labels else None
            current, wrapped = "", []
            for word in logical.split(" "):
                trial = f"{current} {word}".strip()
                if not current or stringWidth(trial, font, size) <= usable:
                    current = trial
                else:
                    wrapped.append(current)
                    current = word
            wrapped.append(current)
            # The label opens the logical line, so it only ever lands on the first row.
            rows.extend((row, label if i == 0 else None) for i, row in enumerate(wrapped))

        parts = []
        for row, label in rows:
            html = escape(self._rtl(row))
            if label:  # bold the label by its own get_display'd form (post-bidi order)
                lbl = escape(self._rtl(label))
                html = html.replace(lbl, f"<b>{lbl}</b>", 1)
            parts.append(html)
        return "<br/>".join(parts)

    # ---- summary PDF ----------------------------------------------------

    MASTER_COLUMN = "master"  # the table's כן/לא membership flag

    @classmethod
    def _split_programs_by_master(cls, table, name_column):
        """Split the letter's programs into (in_master, not_in_master) lists.

        Each list is [(code, name), ...], de-duplicated by code with first-seen order
        preserved. Membership comes from the table's `master` column ('כן'/'לא'); a
        table without that column yields everything as not-in-master.
        """
        in_master, not_in_master, seen = [], [], set()
        for _, rec in table.iterrows():
            code = str(rec.get("number", "")).strip()
            if not code or code in seen:
                continue
            seen.add(code)
            name = str(rec.get(name_column, "")).strip()
            bucket = in_master if str(rec.get(cls.MASTER_COLUMN, "")).strip() == "כן" \
                else not_in_master
            bucket.append((code, name))
        return in_master, not_in_master

    def _programs_table(self, title: str, programs, title_style, font) -> list:
        """Flowables for a titled two-column (name | number) program table, RTL."""
        from reportlab.lib import colors
        from reportlab.lib.units import cm
        from reportlab.platypus import Paragraph, Spacer, Table, TableStyle
        from xml.sax.saxutils import escape

        flow = [Paragraph(escape(self._rtl(title)), title_style), Spacer(1, 0.2 * cm)]
        # RTL reading order puts the program number on the right, the name to its left.
        rows = [[self._rtl("שם התוכנית"), self._rtl("מספר תוכנית")]]
        for code, name in programs:
            rows.append([self._rtl(name), str(code)])
        prog_table = Table(rows, colWidths=[13 * cm, 4 * cm], repeatRows=1)
        prog_table.setStyle(TableStyle([
            ("FONTNAME", (0, 0), (-1, -1), font),
            ("FONTSIZE", (0, 0), (-1, -1), 9),
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#d9d9d9")),
            ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
            ("ALIGN", (0, 0), (0, -1), "RIGHT"),    # name (RTL)
            ("ALIGN", (1, 0), (1, -1), "CENTER"),   # program number
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ]))
        flow.append(prog_table)
        return flow

    def write_summary(self, output_path, *, fields: dict, table,
                      letterhead: list[str], name_column: str,
                      budget_history=None,
                      source_url: str | None = None, llm_usage: dict | None = None,
                      relevant_programs: dict | None = None) -> str:
        """Render the request fields and combined budget table into one A4 PDF.

        `fields` is the {field: value} dict, `table` the combined-table DataFrame,
        and `letterhead` the letterhead lines (see BudgetLetter.extract_letterhead).
        `budget_history` is a list of (title, DataFrame) pairs — the letter's
        'היסטוריה תקציבית' appendix tables, one per metric — each rendered under its
        title below the budget table (see BudgetLetter.extract_budget_history).
        `method` only tags the filename (summary.pdf -> summary_parse.pdf / _llm.pdf).
        `source_url`, if given, is printed as the PDF's provenance line.
        `llm_usage` (from BudgetLetter.last_llm_usage) prints the LLM token cost of the
        extraction; None prints a "rules-based, no LLM cost" note instead.
        The letter's programs are also listed by master membership: those in the master
        set at the top, those not in it at the very end (see _split_programs_by_master).
        Returns the written path.
        """
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import ParagraphStyle
        from reportlab.lib.units import cm
        from reportlab.platypus import (
            Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle,
        )
        from xml.sax.saxutils import escape

        fields = _as_field_dict(fields)
        font = self.FONT_NAME
        # Text column width = page minus the 1.5cm side margins set on the doc below.
        content_width = A4[0] - 3 * cm

        field_style = ParagraphStyle(
            "field", fontName=font, fontSize=11, leading=18, alignment=2,  # right
        )
        link_style = ParagraphStyle(
            "link", fontName=font, fontSize=9, leading=12, alignment=0,  # left
        )
        title_style = ParagraphStyle(
            "title", fontName=font, fontSize=13, leading=18, alignment=1,  # center
        )

        # The letter's programs, split by master membership (from the table's master
        # column), de-duplicated by code, order preserved: in-master shown at the TOP,
        # the rest at the END (see below). Header text is pure Hebrew (no Latin word /
        # parentheses): bidi reorders parens/Latin inside an RTL line and mangles them.
        in_master, _ = self._split_programs_by_master(table, name_column)
        # When the caller supplies the master's authoritative code->name mapping for the
        # programs that made this letter relevant, use it for the in-master block at the
        # top (the master's canonical names, not the letter's own wording).
        if relevant_programs:
            in_master = list(relevant_programs.items())

        story = []
        for header in letterhead:
            story.append(Paragraph(escape(self._rtl(header)), title_style))
        story.append(Spacer(1, 0.5 * cm))

        # ---- in-master programs, at the top, right under the letterhead ----
        if in_master:
            title = f"נמצאו {len(in_master)} תוכניות רלוונטיות במאסטר"
            story.extend(self._programs_table(title, in_master, title_style, font))
            story.append(Spacer(1, 0.6 * cm))

        for label in self.REQUEST_FIELDS:
            value = self._normalize_text(str(fields.get(label, "")))
            if not value.strip():
                continue  # skip empty fields (e.g. request_breakdown folded into summary)
            if label == self.DECISION_LINKS_FIELD:
                story.append(Paragraph(escape(self._rtl(f"{label}:")), field_style))
                rows = [[Paragraph(escape(line), link_style)]
                        for line in value.split("\n") if line.strip()]
                if rows:
                    links_table = Table(rows, colWidths=[16 * cm])
                    links_table.setStyle(TableStyle([
                        ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
                        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                        ("LEFTPADDING", (0, 0), (-1, -1), 6),
                    ]))
                    story.append(links_table)
                continue
            # Every field (incl. the date and the program-code list) goes through the one
            # body path below: _wrap_rtl_html anchors the line with its Hebrew label, so
            # comma-separated numbers keep their order while the paragraph stays right/RTL —
            # so all fields render in the same style.
            lines = value.split("\n")
            lines[0] = f"{label}: {lines[0]}"
            # Wrap in logical order then bidi per line (see _wrap_rtl_html): reversing a
            # long line first and letting reportlab wrap it renders the rows bottom-to-top.
            html = self._wrap_rtl_html("\n".join(lines), content_width, bold_labels=True)
            story.append(Paragraph(html, field_style))
        story.append(Spacer(1, 0.6 * cm))

        # ---- faithful budget table: two rows (מ-/ל-) per item; שינוי + master span them ----
        import pandas as pd

        metrics = [c[:-5] for c in table.columns if str(c).endswith(" from")]
        # logical columns in RTL reading order: code, name, מ/ל, <metrics>, שינוי, master
        logical = ["מספר פרט", "שם הפרט", ""] + metrics + ["שינוי", "master"]
        ncol = len(logical)

        def _num(v):
            try:
                return f"{float(v):,.0f}" if pd.notna(v) else ""
            except (TypeError, ValueError):
                return ""

        rows = [[self._rtl(h) for h in logical][::-1]]
        spans = []
        r = 1
        for _, rec in table.iterrows():
            code = str(rec["number"])
            name = self._rtl(str(rec[name_column]))
            master = self._rtl(str(rec.get("master", "")))  # Hebrew כן/לא -> reverse for display
            # שינוי = the change of the first metric that has both from & to (headline).
            delta = ""
            for m in metrics:
                f_val, t_val = rec.get(f"{m} from"), rec.get(f"{m} to")
                if pd.notna(f_val) and pd.notna(t_val):
                    delta = _num(float(t_val) - float(f_val))
                    break
            row_m = ([code, name, self._rtl("מ-")]
                     + [_num(rec.get(f"{m} from")) for m in metrics] + [delta, master])
            row_l = (["", "", self._rtl("ל-")]
                     + [_num(rec.get(f"{m} to")) for m in metrics] + ["", ""])
            rows.append(row_m[::-1])
            rows.append(row_l[::-1])
            # code / name / שינוי / master are per-item — span the two rows.
            for logical_i in (0, 1, ncol - 2, ncol - 1):
                dc = ncol - 1 - logical_i
                spans.append(("SPAN", (dc, r), (dc, r + 1)))
            r += 2

        data_table = Table(rows, repeatRows=1)
        data_table.setStyle(TableStyle([
            ("FONTNAME", (0, 0), (-1, -1), font),
            ("FONTSIZE", (0, 0), (-1, -1), 7),
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#d9d9d9")),
            ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
            ("ALIGN", (0, 0), (-1, -1), "CENTER"),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ] + spans))
        story.append(data_table)

        # Appendix: the letter's budget-history tables — one per metric (הוצאה נטו /
        # הוצאה מותנית בהכנסה / הרשאה להתחייב) — each under its own title, in the order
        # they appear in the letter. See BudgetLetter.extract_budget_history.
        head_style = ParagraphStyle(
            "hist_head", fontName=font, fontSize=7, alignment=1, leading=9,  # center
        )
        for title, history in (budget_history or []):
            if history is None or history.empty:
                continue
            story.append(Spacer(1, 0.6 * cm))
            story.append(Paragraph(escape(self._rtl(title)), title_style))
            story.append(Spacer(1, 0.2 * cm))

            # Columns reversed for Hebrew reading order. Headers are wrapping,
            # bidi-reordered cells; the amounts/codes are left as plain text so
            # bidi doesn't disturb the digits or the leading minus sign.
            hist_headers = list(history.columns)[::-1]
            hist_rows = [[self._cell(h, head_style) for h in hist_headers]]
            for _, record in history.iterrows():
                hist_rows.append([str(record[col]) for col in hist_headers])

            hist_table = Table(hist_rows, repeatRows=1)
            hist_table.setStyle(TableStyle([
                ("FONTNAME", (0, 0), (-1, -1), font),
                ("FONTSIZE", (0, 0), (-1, -1), 7),
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#d9d9d9")),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
                ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ]))
            story.append(hist_table)

        # Footer, at the very bottom of the page: the clickable source URL, then the
        # LLM extraction cost as the last line. URLs read left-to-right (no bidi); the
        # cost line is Hebrew, so right-aligned and bidi-reordered.
        story.append(Spacer(1, 0.5 * cm))
        if source_url:
            href = escape(source_url, {'"': "&quot;"})
            source_style = ParagraphStyle(
                "source", fontName=font, fontSize=7, leading=10, alignment=0,  # left
                textColor=colors.grey,
            )
            story.append(Paragraph(
                f'source: <a href="{href}" color="#1155cc">{escape(source_url)}</a>',
                source_style))
        # Drop the cost table a bit lower so it's visually separated from the source.
        story.append(Spacer(1, 0.35 * cm))
        # LLM cost as a small label/value table — a text line mangles the parentheses
        # under bidi, so each label and number sits in its own cell instead.
        if llm_usage:
            toks = llm_usage.get("input_tokens", 0) + llm_usage.get("output_tokens", 0)
            cost = llm_usage.get("cost_usd")
            # rows are [value, label] — label (Hebrew, bidi) sits on the right.
            rows = [[str(llm_usage.get("model", "")), self._rtl("מודל")]]
            if cost is not None:
                rows.append([f"₪{cost * self.USD_TO_ILS:.4f}", self._rtl("עלות משוערת בשקל")])
                rows.append([f"${cost:.4f}", self._rtl("עלות משוערת בדולר")])
            rows.append([f"{toks:,}", self._rtl("טוקנים")])
            duration = llm_usage.get("duration_s")
            if duration is not None:
                rows.append([f"{duration:.1f}s", self._rtl("זמן חילוץ בשניות")])
            cost_table = Table(rows, colWidths=[4 * cm, 5 * cm], hAlign="RIGHT")
            cost_table.setStyle(TableStyle([
                ("FONTNAME", (0, 0), (-1, -1), font),
                ("FONTSIZE", (0, 0), (-1, -1), 8),
                ("TEXTCOLOR", (0, 0), (-1, -1), colors.HexColor("#555555")),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#d9d9d9")),
                ("ALIGN", (0, 0), (0, -1), "LEFT"),    # values
                ("ALIGN", (1, 0), (1, -1), "RIGHT"),   # Hebrew labels
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ]))
            story.append(cost_table)
        else:
            note_style = ParagraphStyle(
                "cost", fontName=font, fontSize=8, leading=12, alignment=2,  # right
                textColor=colors.grey,
            )
            story.append(Paragraph(
                escape(self._rtl("חילוץ מבוסס-כללים — ללא עלות LLM")), note_style))

        output_path = str(Path(output_path))
        SimpleDocTemplate(
            output_path, pagesize=A4,
            topMargin=1.5 * cm, bottomMargin=1.5 * cm,
            leftMargin=1.5 * cm, rightMargin=1.5 * cm,
        ).build(story)
        return output_path

    # ---- comparison PDF -------------------------------------------------

    @staticmethod
    def _normalize(value: str) -> str:
        """Collapse whitespace and make digit order irrelevant so trivial formatting
        differences (spacing, bidi-reversed numbers) don't count as a mismatch."""
        text = " ".join(str(value).split())
        return re.sub(r"\d[\d.,/]*", lambda m: "".join(sorted(m.group(0))), text)

    @classmethod
    def fields_match(cls, parse_value: str, llm_value: str) -> bool:
        """True if a non-empty parse value equals the llm value after normalization."""
        return bool(parse_value) and cls._normalize(parse_value) == cls._normalize(llm_value)

    def write_comparison(self, output_path, name: str, source: str,
                         parse: dict[str, str], llm: dict[str, str]) -> str:
        """Write a parse-vs-llm comparison PDF for one source; return the written path."""
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import ParagraphStyle
        from reportlab.lib.units import cm
        from reportlab.platypus import (
            Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle,
        )
        from xml.sax.saxutils import escape

        parse = _as_field_dict(parse)
        llm = _as_field_dict(llm)
        font = self.FONT_NAME

        title = ParagraphStyle("title", fontName=font, fontSize=14, alignment=2, leading=20)
        src = ParagraphStyle("src", fontName=font, fontSize=7, alignment=0, leading=10)
        body = ParagraphStyle("body", fontName=font, fontSize=8, alignment=2, leading=11)
        center = ParagraphStyle("center", fontName=font, fontSize=10, alignment=1, leading=12)

        matches = sum(self.fields_match(parse.get(f, ""), llm.get(f, ""))
                      for f in self.REQUEST_FIELDS)
        total = len(self.REQUEST_FIELDS)

        story = [
            Paragraph(escape(self._rtl(f"השוואת parse מול llm — {name}")), title),
            Paragraph(escape(self._rtl(f"שדות תואמים: {matches} מתוך {total}")), title),
            Spacer(1, 0.2 * cm),
            Paragraph(escape(source), src),
            Spacer(1, 0.4 * cm),
        ]

        # Columns are laid out right-to-left: field on the right, then parse, llm,
        # and the match mark on the left.
        header = [self._cell(h, center) for h in ("תואם", "llm", "parse", "שדה")]
        rows = [header]
        for field in self.REQUEST_FIELDS:
            a, b = parse.get(field, ""), llm.get(field, "")
            mark = "✓" if self.fields_match(a, b) else "✗"
            rows.append([Paragraph(mark, center), self._cell(b, body),
                         self._cell(a, body), self._cell(field, body)])

        table = Table(rows, colWidths=[1.3 * cm, 7 * cm, 7 * cm, 3.2 * cm], repeatRows=1)
        table.setStyle(TableStyle([
            ("FONTNAME", (0, 0), (-1, -1), font),
            ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#d9d9d9")),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f3f3f3")]),
        ]))
        story.append(table)

        output_path = str(output_path)
        SimpleDocTemplate(
            output_path, pagesize=A4,
            topMargin=1.2 * cm, bottomMargin=1.2 * cm,
            leftMargin=1 * cm, rightMargin=1 * cm,
        ).build(story)
        return output_path
