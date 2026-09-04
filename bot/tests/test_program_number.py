"""Program codes are found anywhere in the narrative, not only at line starts.

Gemini returns the request text as one paragraph (no newlines), so 'NNNNNN:' headings
sit mid-line; the extractor must still list every code, in order, without duplicates.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "bot"))

from agent import Agent  # noqa: E402

ONE_LINE = ("הפנייה נועדה לתקצוב סך של 190,709 אלפי ש\"ח. 231039: שירותים קהילתיים – 139,917 אלפי ש\"ח "
            "בהוצאה . תיאור התוכנית: ... מטרת השינוי: ... 230120: שירותי משרד מרכזיים - 104,678 אלפי "
            "ש\"ח בהוצאה . תיאור: ... 231039: (חזרה) ... החלטת ממשלה מספר 550 מיום 24.10.2021 "
            "לשנים 2025-2029. תוכנית 17-31-03: מטה אזרחי")


def test_codes_found_mid_line_in_order_without_duplicates():
    # the trailing 'תוכנית 17-31-03' is a hyphenated code and is listed normalised
    assert Agent._program_number(ONE_LINE) == "231039, 230120, 173103"


def test_codes_at_line_start_still_work():
    assert Agent._program_number("231039: שירותים\nתיאור\n230120: משרד\n") == "231039, 230120"


def test_dates_and_years_are_not_codes():
    assert Agent._program_number("מיום 24.10.2021 עד 2026: תוכנית לשנים 2025-2029") == ""


def test_narrative_region_accepts_double_yod_heading():
    text = "בלה\nעיקריי הפנייה :\nהפנייה התקציבית נועדה לתקצוב.\nתאריך הבקשה: 26/07/2026\n"
    assert Agent._narrative_region(text).startswith("הפנייה התקציבית")
    assert Agent._narrative_region("עיקרי הפנייה: טקסט\nתאריך הבקשה: 1/1/2026").startswith("טקסט")


def test_hyphenated_codes_are_listed_normalised():
    assert Agent._program_number("תוכנית 17-31-03: מטה אזרחי – 25,600 אלפי ש\"ח. 17-31-08 – יחידת הפיקוח") == "173103, 173108"


def test_committee_number_double_vav():
    a = Agent(set())
    assert "מספר פנייה לועדה: 123" in a._request_number("בקשה מספר 17-207\nמספר פניה לוועדה: 123\n")


def test_narrative_region_stops_before_staffing_line_and_appendix():
    text = ("עיקרי הפנייה:\nהפנייה נועדה.\n231039: שירותים – 1 אלפי ש\"ח\nתיאור התוכנית: א.\n"
            "השפעה על כוח אדם: אין.\nבכבוד רב,\nהיסטוריה תקציבית של הפנייה\n231039 1 2 3\nתאריך הבקשה: 1/1/2026")
    region = Agent._narrative_region(text)
    assert region.endswith("תיאור התוכנית: א.")
    assert "היסטוריה" not in region and "בכבוד רב" not in region


def test_committee_list_order_is_restored():
    a = Agent(set())
    assert a._request_number("בקשה מספר 19-206\nמספר פניה לוועדה: 72 ,47\n").endswith("מספר פנייה לועדה: 47, 72")
    assert a._request_number("בקשה מספר 30-205\nמספר פניה לועדה: 41 עד 43\n").endswith("מספר פנייה לועדה: 41 עד 43")
