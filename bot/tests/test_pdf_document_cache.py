"""PdfDocument parses the PDF once: repeated access to text/raw_text/pages/tables must
not re-open the file, and the values stay identical between accesses."""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(_ROOT, "bot"))

import pdfplumber  # noqa: E402
import pytest  # noqa: E402

from pdf_document import PdfDocument  # noqa: E402

SMALL_PDF = os.path.join(_ROOT, "bot", "tests", "test_files",
                         "4_10eab6ad-df75-f111-a13e-005056aa7c52_13_21653.pdf")


@pytest.fixture
def counted_open(monkeypatch):
    calls = []
    real_open = pdfplumber.open

    def counting(*args, **kwargs):
        calls.append(1)
        return real_open(*args, **kwargs)

    monkeypatch.setattr(pdfplumber, "open", counting)
    return calls


@pytest.mark.skipif(not os.path.isfile(SMALL_PDF), reason="test PDF not present")
def test_pdf_is_opened_once_for_all_accesses(counted_open):
    doc = PdfDocument(SMALL_PDF)
    text1 = doc.text
    text2 = doc.text
    raw = doc.raw_text
    pages = doc.pages
    tables = doc._header_page_tables()
    tables_again = doc._header_page_tables()
    assert text1 == text2 and text1
    assert raw
    assert pages and pages[0].text
    assert tables == tables_again
    assert len(counted_open) == 1, f"PDF opened {len(counted_open)} times, expected once"


@pytest.mark.skipif(not os.path.isfile(SMALL_PDF), reason="test PDF not present")
def test_pages_text_matches_text():
    doc = PdfDocument(SMALL_PDF)
    assert "\n".join(p.text for p in doc.pages) == doc.text


PDF_21709 = os.path.join(_ROOT, "bot", "tests", "test_files", "21709_original.pdf")


@pytest.mark.skipif(not os.path.isfile(PDF_21709), reason="21709 PDF not present")
def test_narrow_history_table_is_rebuilt_with_all_columns():
    """21709's second history table is detected without its outer columns; the reader
    rebuilds it on the first table's grid so program codes are not lost."""
    doc = PdfDocument(PDF_21709)
    history_pages = [p for p in doc.pages if "היסטוריה תקציבית" in p.text]
    assert history_pages
    tables = [t for p in history_pages for t in p.tables]
    assert len(tables) >= 2
    assert {len(t[0]) for t in tables} == {9}
    assert tables[1][1][0].strip() == "173102"
