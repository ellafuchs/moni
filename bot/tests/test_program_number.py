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
    assert Agent._program_number(ONE_LINE) == "231039, 230120"


def test_codes_at_line_start_still_work():
    assert Agent._program_number("231039: שירותים\nתיאור\n230120: משרד\n") == "231039, 230120"


def test_dates_and_years_are_not_codes():
    assert Agent._program_number("מיום 24.10.2021 עד 2026: תוכנית לשנים 2025-2029") == ""
