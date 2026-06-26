"""PDF rendering for budget-transfer letters — one class, two PDFs.

`Reports` writes the two Hebrew PDFs the project produces:

    Reports().write_summary(path, fields=..., table=..., letterhead=..., name_column=...)
        -> a per-letter summary: request fields on top, combined budget table below.

    Reports().write_comparison(path, name, source, parse, llm)
        -> a parse-vs-llm field comparison table (the "are they the same?" PDF).

Both take already-extracted data (a {field: value} dict, and for the summary a
combined-table DataFrame) — the rendering is kept separate from PdfTableExtractor,
which does the extraction. reportlab and python-bidi are imported lazily, so the
rest of the project works without them installed.
"""
from __future__ import annotations

import re
from pathlib import Path

from pdf_key_checker import PdfTableExtractor


class Reports:
    """Renders the summary and comparison PDFs; shares the Hebrew/RTL plumbing."""

    FONT_NAME = "Hebrew"
    FONT_PATH = PdfTableExtractor.HEBREW_FONT_PATH
    REQUEST_FIELDS = PdfTableExtractor.REQUEST_FIELDS
    DECISION_LINKS_FIELD = PdfTableExtractor.DECISION_LINKS_FIELD

    def __init__(self):
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.ttfonts import TTFont

        pdfmetrics.registerFont(TTFont(self.FONT_NAME, self.FONT_PATH))

    # ---- shared helpers -------------------------------------------------

    @staticmethod
    def _rtl(value) -> str:
        """Reorder a (possibly Hebrew) string for visual right-to-left display."""
        from bidi.algorithm import get_display

        return get_display(str(value))

    def _cell(self, value, style):
        """A wrapping cell that keeps a value's line breaks, reordered for RTL."""
        from reportlab.platypus import Paragraph
        from xml.sax.saxutils import escape

        lines = str(value).split("\n")
        html = "<br/>".join(escape(self._rtl(line)) for line in lines)
        return Paragraph(html or " ", style)

    # ---- summary PDF ----------------------------------------------------

    def write_summary(self, output_path, *, fields: dict, table,
                      letterhead: list[str], name_column: str,
                      method: str = "parse", budget_history=None) -> str:
        """Render the request fields and combined budget table into one A4 PDF.

        `fields` is the {field: value} dict, `table` the combined-table DataFrame,
        and `letterhead` the letterhead lines (see PdfTableExtractor.extract_letterhead).
        `budget_history`, if a non-empty DataFrame, is rendered as a second table
        (the 'היסטוריה תקציבית' appendix) below the budget table.
        `method` only tags the filename (summary.pdf -> summary_parse.pdf / _llm.pdf).
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

        font = self.FONT_NAME

        field_style = ParagraphStyle(
            "field", fontName=font, fontSize=11, leading=18, alignment=2,  # right
        )
        link_style = ParagraphStyle(
            "link", fontName=font, fontSize=9, leading=12, alignment=0,  # left
        )
        title_style = ParagraphStyle(
            "title", fontName=font, fontSize=13, leading=18, alignment=1,  # center
        )

        story = []
        for header in letterhead:
            story.append(Paragraph(escape(self._rtl(header)), title_style))
        story.append(Spacer(1, 0.5 * cm))
        for label in self.REQUEST_FIELDS:
            value = str(fields.get(label, ""))
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
            lines = value.split("\n")
            lines[0] = f"{label}: {lines[0]}"
            html = "<br/>".join(escape(self._rtl(line)) for line in lines)
            story.append(Paragraph(html, field_style))
        story.append(Spacer(1, 0.6 * cm))

        # Build the table with columns reversed so the first column sits on the
        # right (Hebrew reading order). Headers and Hebrew name cells get bidi.
        headers = list(table.columns)[::-1]
        rows = [[self._rtl(h) for h in headers]]
        for _, record in table.iterrows():
            cells = []
            for column in headers:
                value = record[column]
                if column in ("from", "to", "delta"):
                    cells.append(f"{value:,.0f}")
                elif column == name_column:
                    cells.append(self._rtl(value))
                else:
                    cells.append(str(value))
            rows.append(cells)

        data_table = Table(rows, repeatRows=1)
        data_table.setStyle(TableStyle([
            ("FONTNAME", (0, 0), (-1, -1), font),
            ("FONTSIZE", (0, 0), (-1, -1), 8),
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#d9d9d9")),
            ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
            ("ALIGN", (0, 0), (-1, -1), "CENTER"),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ]))
        story.append(data_table)

        # Appendix: the multi-year net-expenditure history, if the letter has one.
        if budget_history is not None and not budget_history.empty:
            head_style = ParagraphStyle(
                "hist_head", fontName=font, fontSize=7, alignment=1, leading=9,  # center
            )
            story.append(Spacer(1, 0.6 * cm))
            story.append(Paragraph(
                escape(self._rtl("היסטוריה תקציבית של הפנייה - הוצאה נטו")), title_style))
            story.append(Spacer(1, 0.2 * cm))

            # Columns reversed for Hebrew reading order. Headers are wrapping,
            # bidi-reordered cells; the amounts/codes are left as plain text so
            # bidi doesn't disturb the digits or the leading minus sign.
            hist_headers = list(budget_history.columns)[::-1]
            hist_rows = [[self._cell(h, head_style) for h in hist_headers]]
            for _, record in budget_history.iterrows():
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

        # Tag the output filename with the extraction method.
        output_path = Path(output_path)
        output_path = str(output_path.with_stem(f"{output_path.stem}_{method}"))
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
