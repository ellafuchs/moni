"""The summary page: text shaping, template rendering, and (if Chrome is present) PDF."""
import json
import os
import shutil
import sys

import pandas as pd
import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "bot"))

from reports import Reports, find_chrome, html_to_pdf, ChromeNotFound  # noqa: E402
from request_fields import RequestFields  # noqa: E402
from summary_text import split_programs, structure_summary  # noqa: E402

GOLDEN_21658 = os.path.join(_ROOT, "bot", "tests", "fixtures", "golden", "21658.json")

ONE_LINE = ('הפנייה נועדה לתקצוב סך של 190,709 אלפי ש"ח. עיקרי הפנייה: 231039: שירותים קהילתיים – '
            '139,917 אלפי ש"ח בהוצאה . תיאור התוכנית: תכנית זו משמשת. מטרת השינוי : הקצאת תקציב. '
            '230120: שירותי משרד - 104,678 אלפי ש"ח . תיאור התכנית: תפעול. מטרת השי נוי: מיזם.')


def test_structure_summary_adds_line_breaks_and_fixes_split_word():
    lines = structure_summary(ONE_LINE).split("\n")
    assert lines[0].startswith("הפנייה נועדה")
    assert any(l.startswith("231039:") for l in lines)
    assert any(l.startswith("230120:") for l in lines)
    assert any(l.startswith("תיאור התוכנית:") for l in lines)
    assert any(l.startswith("מטרת השינוי:") for l in lines)  # 'השי נוי' repaired
    assert structure_summary(structure_summary(ONE_LINE)) == structure_summary(ONE_LINE)


def test_split_programs_one_paragraph():
    intro, programs = split_programs(ONE_LINE)
    assert intro.startswith("הפנייה נועדה")
    assert [p.code for p in programs] == ["231039", "230120"]
    assert programs[0].heading.startswith("שירותים קהילתיים")
    assert programs[0].description == "תכנית זו משמשת."
    assert programs[0].purpose == "הקצאת תקציב."
    assert programs[1].purpose == "מיזם."


@pytest.mark.skipif(not os.path.isfile(GOLDEN_21658), reason="golden fixture missing")
def test_split_programs_golden_21658_has_eleven_blocks():
    golden = json.load(open(GOLDEN_21658, encoding="utf-8"))
    intro, programs = split_programs(golden["text"]["request_summary"])
    assert len(programs) == 11
    assert all(p.description and p.purpose for p in programs)
    assert intro.startswith("הפנייה התקציבית נועדה")


def _sample():
    fields = RequestFields(
        date="01/07/2026",
        request_number="12-205, 54-219 | מספר פנייה לועדה: 65 עד 70",
        program_number="231039, 230120",
        request_summary=ONE_LINE,
        decision_links="1. https://www.gov.il/he/pages/dec550_2021",
        coalition_funds="כן", staffing_changes="לא",
    )
    table = pd.DataFrame([
        {"number": "231039", "name": "שירותים קהילתיים - שירותים אישיים וחברתיים",
         "הוצאה from": 2048152.0, "הוצאה to": 2188069.0, "master": "כן"},
        {"number": "121101", "name": "גמלאות מקופת המדינה",
         "הוצאה from": 17780480.0, "הוצאה to": 17690480.0, "master": "לא"},
    ])
    history = [("היסטוריה תקציבית של הפנייה - הוצאה נטו",
                pd.DataFrame([{"קוד תוכנית": "231039", "מקורי 2026": "1,703,020"}]))]
    return dict(fields=fields, table=table, letterhead=["מדינת ישראל", "האוצר - אגף התקציבים"],
                name_column="name", budget_history=history,
                source_url="https://example.org/21658.pdf",
                llm_usage={"model": "gemini-3.6-flash", "input_tokens": 100, "output_tokens": 20, "cost_usd": 0.0},
                request_id="21658", coalition_reason="הקצאת תקציב קואליציוני למבחן תמיכה",
                master_names={"231039": "שירותים קהילתיים – שירותים אישיים וחברתיים (שם מלא)"})


def test_render_summary_html():
    html = Reports().render_summary_html(**_sample())
    assert "סיכום פנייה תקציבית" in html and ">21658<" in html
    assert "12-205, 54-219" in html and "65 עד 70" in html        # request numbers intact
    assert "231039, 230120" in html                               # מס' תוכנית list
    assert "הקצאת תקציב קואליציוני למבחן תמיכה" in html           # coalition reason
    assert "(שם מלא)" in html                                     # master full name used
    assert html.count('class="prog m"') == 1 and html.count('class="prog"') == 1
    assert "+139,917" in html and "−90,000" in html               # signed deltas
    assert '<a href="https://www.gov.il/he/pages/dec550_2021">' in html
    assert "<b>139,917</b>" in html                               # amounts bold
    assert 'class="m"' in html                                    # master rows green (table + history)
    assert "gemini-3.6-flash" in html and "120" in html


def test_render_not_relevant_letter():
    sample = _sample()
    sample["table"] = sample["table"].assign(master="לא")
    html = Reports().render_summary_html(**sample)
    assert "אף תוכנית מהמאסטר" in html and "התוכניות של הקרן" not in html


def _chrome_available() -> bool:
    try:
        find_chrome()
        return True
    except ChromeNotFound:
        return False


@pytest.mark.skipif(not _chrome_available(), reason="Chrome not installed")
def test_write_summary_produces_pdf_and_html(tmp_path):
    pdf = Reports().write_summary(tmp_path / "x_summary.pdf", **_sample())
    assert os.path.getsize(pdf) > 10_000
    assert (tmp_path / "x_summary.html").is_file()


def test_find_chrome_env_override(monkeypatch, tmp_path):
    fake = tmp_path / "chrome"; fake.write_text("");
    monkeypatch.setenv("MONI_CHROME", str(fake))
    assert find_chrome() == str(fake)
    assert shutil.which is not None and html_to_pdf  # imported symbols exist


def test_split_final_letters_are_rejoined():
    from reports import join_split_letters
    assert join_split_letters("היסטוריה תקציבית של הפנייה - הוצאה נט ו") == "היסטוריה תקציבית של הפנייה - הוצאה נטו"
    assert join_split_letters("קוד תוכני ת") == "קוד תוכנית"
    assert join_split_letters("מקורי 2026") == "מקורי 2026"


def test_hyphenated_program_headings_are_split():
    text = ('הפניה נועדה לתקצוב סך של 103,500 אלפי ש"ח. עיקרי הפנייה בחלוקה לתוכניות מובאים להלן: '
            '19-42-02 - מנהל התרבו ת תיאור התוכנית: תכנית זו משמשת. מטרת השינוי: תקצוב 63 מיליוני ש"ח. '
            '19-43-03 - מינהל הספורט תיאור התכנית: ספורט. מטרת השינוי התקציבי: תקצוב 40,500 אלפי ש"ח. '
            'תוכנית 17-31-03: מטה אזרחי – 25,600 אלפי ש"ח בהוצאה מותנית בהכנסה תיאור התוכנית: מטה.')
    intro, programs = split_programs(text)
    assert [p.code for p in programs] == ["194202", "194303", "173103"]
    assert programs[0].heading.startswith("מנהל התרבו")
    assert programs[1].purpose.startswith("תקצוב 40,500")
    assert programs[2].heading.startswith("מטה אזרחי")


def test_no_headings_keeps_lines():
    intro, programs = split_programs("שורה ראשונה\nתיאור התוכנית: משהו\nמטרת השינוי: אחר")
    assert programs == [] and intro.count("\n") == 2


def test_join_split_letters_keeps_conjunction_between_numbers():
    from reports import join_split_letters
    assert join_split_letters('סך של 500 אלפי ש" ח') == 'סך של 500 אלפי ש"ח'
    assert join_split_letters("מנהל התרבו ת") == "מנהל התרבות"
    assert join_split_letters("מספר 2856 ו 2857") == "מספר 2856 ו 2857"


def test_history_cells_and_headers():
    from reports import format_history_cell, clean_header
    assert format_history_cell("12,900-") == "−12,900"
    assert format_history_cell("-115,990") == "−115,990"
    assert format_history_cell("1,703,020") == "1,703,020"
    assert format_history_cell("0") == "0" and format_history_cell("") == ""
    assert clean_header("מאושר 2025 בניכוי עודפים שעברו ב2025-") == "מאושר 2025 בניכוי עודפים שעברו ב-2025"


def test_master_table_uses_longest_name():
    sample = _sample()
    sample["master_names"] = {"231039": "שירותים קהילתיים -"}   # truncated in the workbook
    ctx = Reports().build_context(**sample)
    assert ctx["master_rows"][0]["name"] == "שירותים קהילתיים - שירותים אישיים וחברתיים"


def test_staffing_sentence_dropped_from_narrative_and_header_space():
    from reports import clean_header
    intro, programs = split_programs("19-42-02 - מנהל התרבות תיאור התוכנית: א. מטרת השינוי: ב. השפעה על כוח אדם: אין .")
    assert programs[0].purpose == "ב." and "השפעה" not in programs[0].purpose
    assert clean_header("מאושר 2025 בניכוי עודפים שעברו ב- 2025") == "מאושר 2025 בניכוי עודפים שעברו ב-2025"


def test_join_two_split_letters_in_a_row():
    from reports import join_split_letters
    assert join_split_letters("המוצע בפנייה ז ו") == "המוצע בפנייה זו"
    assert join_split_letters("הוצאה מותנית בהכנס ה") == "הוצאה מותנית בהכנסה"


def test_history_code_column_is_not_number_formatted_and_source_link_only_for_urls():
    sample = _sample()
    ctx = Reports().build_context(**sample)
    assert ctx["history"][0]["rows"][0]["cells"][0] == "231039"
    assert ctx["source_url"] == "https://example.org/21658.pdf"
    sample["source_url"] = "/tmp/letters/21658_original.pdf"
    ctx = Reports().build_context(**sample)
    assert ctx["source_url"] == "" and ctx["source_name"] == "21658_original.pdf"


def test_structure_summary_drops_table_rows_and_sign_off_and_repairs_labels():
    text = ("תוכנית 17-31-03: מטה אזרחי – 25,600 אלפי ש\"ח תיאור התוכני ת: תכנית זו. מטרת השינוי: תוספת.\n"
            "173102 0 72,882 70,582\n173103 25,600 75,190 55,350\nבכבוד רב,\nהעתק:\nהחשב הכללי")
    intro, programs = split_programs(text)
    assert len(programs) == 1
    assert programs[0].description == "תכנית זו." and programs[0].purpose == "תוספת."
    assert not programs[0].other


def test_split_letter_before_hyphen():
    from summary_text import join_split_letters
    assert join_split_letters("מט ה- מפקדת תיאום") == "מטה- מפקדת תיאום"


def test_prefix_letter_before_number_is_not_joined():
    from summary_text import join_split_letters
    assert join_split_letters("שעברו ב-2025 ו-2857") == "שעברו ב-2025 ו-2857"
