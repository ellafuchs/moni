"""RequestFields — the typed result of extracting a budget letter's summary fields.

Both extraction methods (rule-based and LLM) return this same model, so callers know
exactly what comes back. Attribute names are English (valid Python identifiers, nice
in code and IDEs); the Hebrew labels used by the reports and existing outputs are kept
as aliases. `model_dump(by_alias=True)` yields the Hebrew-keyed dict the reports expect.
"""
from __future__ import annotations

import re

from pydantic import BaseModel, ConfigDict, Field


class RequestFields(BaseModel):
    """A budget-transfer request letter's summary fields."""

    model_config = ConfigDict(populate_by_name=True)

    date: str = Field(
        default="", alias="תאריך",
        description="תאריך הבקשה מדפי הטבלה, בפורמט DD/MM/YYYY. אם אינו מופיע, החזר מחרוזת ריקה.")
    request_number: str = Field(
        default="", alias="מס' פנייה",
        description=("רק מספרי הבקשה עצמם בפורמט NN-NNN בדיוק כפי שהם מופיעים בטקסט, "
                     "מופרדים בפסיק, ללא המילים 'בקשה מספר'. לאחר מכן הוסף 'מספר פנייה לועדה: X'."))
    program_number: str = Field(
        default="", alias="מס' תוכנית",
        description=("רק קודי התוכניות המופיעים ככותרת 'תכנית NNNNNN:' בתוך מקטע 'עיקרי הפנייה "
                     "בחלוקה לתוכניות'. אל תיקח קודים מטבלאות התקציב. אם יש כמה, החזר מופרדים בפסיק."))
    request_summary: str = Field(
        default="", alias="עיקרי הפנייה",
        description=("העתק מילה במילה את כל הנרטיב שאחרי הכותרת 'עיקרי הפנייה:' — הפסקה "
                     "הפותחת, כל בלוק 'NNNNNN: ... תיאור התוכנית: ...' וכל בלוק 'מטרת השינוי:'. "
                     "אל תסכם ואל תנסח מחדש."))
    decision_links: str = Field(
        default="", alias="קישורים להחלטות ממשלה",
        description="קישורים להחלטות ממשלה אם מופיעים בטקסט. אם אין, החזר מחרוזת ריקה.")
    coalition_funds: str = Field(
        default="", alias="האם יש התייחסות לכספים קואליציונים",
        description=("'כן' או 'לא' בלבד לפי ההצהרה המפורשת במכתב. "
                     "אם נכתב 'אינה כוללת כספים קואליציוניים' החזר 'לא'."))
    staffing_changes: str = Field(
        default="", alias="שינויים בכוח אדם",
        description="'כן' או 'לא' בלבד, לפי האם המכתב מציין שינויים בכוח אדם.")

    @classmethod
    def field_labels(cls) -> list[str]:
        """The Hebrew field labels, in declaration order (replaces the old REQUEST_FIELDS)."""
        return [info.alias for info in cls.model_fields.values()]

    def check(self) -> dict[str, str]:
        """Per-field sanity report — {Hebrew label: "OK" or a short ⚠ warning}.

        A soft check: it never raises and never changes the data. It just flags
        fields that look empty or malformed so a human can spot a bad extraction.
        `decision_links` empty is reported as "— none" (often legitimately empty),
        not as a warning.
        """
        yes_no = {"כן", "לא"}
        program_ok = all(
            re.fullmatch(r"\d{5,6}", tok.strip())
            for tok in self.program_number.split(",") if tok.strip()
        )
        checks = {
            "תאריך": self._status(
                self.date, re.fullmatch(r"\d{1,2}/\d{1,2}/\d{4}", self.date.strip()),
                "not DD/MM/YYYY"),
            "מס' פנייה": self._status(
                self.request_number, re.search(r"\d{3}-\d{2}", self.request_number),
                "no NN-NNN number"),
            "מס' תוכנית": self._status(
                self.program_number, self.program_number.strip() and program_ok,
                "not 5-6 digit codes"),
            "עיקרי הפנייה": self._status(self.request_summary, self.request_summary.strip(), ""),
            "קישורים להחלטות ממשלה": "OK" if self.decision_links.strip() else "— none",
            "האם יש התייחסות לכספים קואליציונים": self._status(
                self.coalition_funds, self.coalition_funds.strip() in yes_no, "not כן/לא"),
            "שינויים בכוח אדם": self._status(
                self.staffing_changes, self.staffing_changes.strip() in yes_no, "not כן/לא"),
        }
        return checks

    @staticmethod
    def _status(value: str, valid, malformed_warning: str) -> str:
        """OK if `valid` is truthy; "⚠ empty" if the value is blank; else the warning."""
        if not str(value).strip():
            return "⚠ empty"
        if valid:
            return "OK"
        return f"⚠ {malformed_warning}" if malformed_warning else "OK"
