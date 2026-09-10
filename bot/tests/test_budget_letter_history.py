"""A budget-history table whose header spans several rows gets merged column names."""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(_ROOT, "bot"))

from budget_letter import BudgetLetter  # noqa: E402


def _letter():
    return BudgetLetter.__new__(BudgetLetter)   # _clean_history_table needs no document


THREE_ROW_HEADER = [   # the shape pdfplumber returns for letter 21766
    ["", "השינוי התקציב", "", "מאושר 2025", ""],
    ["קוד תוכנית", "המוצע בפנייה", "מקורי 2026", "בניכוי עודפים", "מקורי 2025"],
    ["", "הוצאות נטו", "", "שעברו ב-2025", ""],
    ["702001", "4,090", "591,451", "497,409", "434,840"],
]


def test_three_header_rows_are_merged_and_data_starts_at_the_code_row():
    df = _letter()._clean_history_table(THREE_ROW_HEADER)
    assert list(df.columns) == [
        "קוד תוכנית", "השינוי התקציב המוצע בפנייה הוצאות נטו", "מקורי 2026",
        "מאושר 2025 בניכוי עודפים שעברו ב-2025", "מקורי 2025",
    ]
    assert len(df) == 1 and list(df.iloc[0]) == ["702001", "4,090", "591,451", "497,409", "434,840"]
    assert len(set(df.columns)) == len(df.columns)   # no duplicate or blank names


def test_single_header_row_is_unchanged():
    df = _letter()._clean_history_table([["קוד תוכנית", "מקורי 2024"], ["045101", "1,000"]])
    assert list(df.columns) == ["קוד תוכנית", "מקורי 2024"] and list(df.iloc[0]) == ["045101", "1,000"]


def test_header_rows_are_capped_at_three():
    rows = [["א", "ב"], ["ג", "ד"], ["ה", "ו"], ["ז", "ח"], ["1", "2"]]
    df = _letter()._clean_history_table(rows)
    assert len(df) == 2 and list(df.iloc[0]) == ["ז", "ח"]
