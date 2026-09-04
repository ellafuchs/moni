"""The single extraction agent — one class, all tools + the one LLM call.

`Agent` owns ALL request-field extraction for a budget-transfer letter. Most fields are
produced by deterministic methods over the FULL letter text (no truncation). A single LLM
call over the narrative region does the two things that vary per letter: judging
`coalition_funds` and finding where the narrative ends (so `request_summary` is sliced
VERBATIM, no reflow). No deterministic fallback. `BudgetLetter` stays the document/table
provider; this class is the whole agent.

    agent = Agent(ConfigManager.read_master_programs("files/master.xlsx"), api_key=...)
    result = agent.extract(source)   # source = URL or local PDF path
    result.fields        # RequestFields
    result.table         # budget table + a `master` (כן/לא) column
    result.matched_codes # letter codes that are in the master set
    result.relevant      # True iff a summary should be produced
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass

import pandas as pd
from dotenv import load_dotenv
from pydantic import BaseModel, Field

from budget_letter import BudgetLetter
from request_fields import RequestFields
from summary_text import structure_summary

# Load OPENAI_API_KEY from .env even when imported standalone.
load_dotenv()

logger = logging.getLogger(__name__)

MASTER_COLUMN = "master"
DEFAULT_MODELS = {
    "openai": "gpt-4o",
    "google_genai": "gemini-3.6-flash",
}
DEFAULT_MODEL = DEFAULT_MODELS["openai"]

# Rough list price, USD per 1M tokens (input, output) — for the on-report cost estimate.
# Gemini Flash runs on the free tier here, so its cost is reported as 0.
PRICING_USD_PER_1M = {
    "gpt-4o": (2.50, 10.00),
    "gpt-4o-mini": (0.15, 0.60),
    "gemini-3.6-flash": (0.0, 0.0),
    "gemini-3.5-flash": (0.0, 0.0),
    "gemini-3.5-flash-lite": (0.0, 0.0),
}


class _Analysis(BaseModel):
    """The one LLM call's output: the coalition judgment (+ its reason) and the request
    text copied verbatim."""
    coalition_reason: str = Field(
        default="",
        description="One short Hebrew sentence quoting the letter's basis for the "
                    "coalition_funds answer (e.g. which program allocates coalition money, "
                    "or that the letter states it includes none). Empty if nothing relevant.")
    coalition_funds: str = Field(
        description="'כן' if the letter actually USES/allocates coalition funds, or asks "
                    "the committee to keep monitoring them going forward; else 'לא'. "
                    "Merely mentioning coalition is not enough.")
    request_summary: str = Field(
        description="Return the COMPLETE request text VERBATIM — this is NOT a summary. "
                    "Include the opening paragraph and, for EACH program, its "
                    "'NNNNNN: <שם> – <סכום>' line with its full 'תיאור התוכנית' and "
                    "'מטרת השינוי'. Copy every word exactly; do NOT summarize, shorten, "
                    "rephrase or omit any narrative sentence. The ONLY things to leave out "
                    "are non-prose artifacts: tables / number lists (e.g. the coalition "
                    "totals table ending in 'סה\"כ'), the links section, and the sign-off.")


@dataclass
class Extraction:
    """The agent's result for one letter."""
    fields: RequestFields
    table: pd.DataFrame        # budget table + a `master` (כן/לא) column
    matched_codes: set[str]    # letter codes that are in the master set
    relevant: bool             # a summary is produced iff this is True
    letter: BudgetLetter       # kept for rendering (letterhead, history, original PDF)
    llm_usage: dict | None = None   # token counts + cost of the one coalition call
    coalition_reason: str = ""      # the model's one-sentence basis for coalition_funds


class Agent:
    """The single extraction agent: deterministic tools + one coalition LLM call."""

    _ANALYSIS_SYSTEM = (
        "לפניך הטקסט הנרטיבי של פנייה תקציבית (מהכותרת 'עיקרי הפנייה'). בצע שתי משימות:\n"
        "1) coalition_funds: קבע 'כן' אם הפנייה עושה שימוש בפועל בכספים קואליציוניים, "
        "או מבקשת מהוועדה להמשיך ולעקוב אחריהם; אחרת 'לא'. רק אזכור של קואליציה, או "
        "'אינה כוללת כספים קואליציוניים', הם 'לא'. המשפט הכללי 'האם יש התייחסות לכספים "
        "קואליציונים' לבדו אינו מספיק.\n"
        "   coalition_reason: משפט קצר אחד שמצטט את הבסיס לתשובה (למשל איזו תכנית מקצה "
        "תקציב קואליציוני, או שהפנייה מצהירה שאינה כוללת כספים קואליציוניים).\n"
        "2) request_summary: החזר את הנרטיב של הפנייה כטקסט עברי נקי — פסקת הפתיחה, "
        "ולכל תכנית, בשורה חדשה, את שורת 'NNNNNN: <שם> – <סכום>', ואחריה בשורות נפרדות "
        "'תיאור התוכנית' ו'מטרת השינוי'. "
        "העתק את הניסוח בנאמנות; אל תמציא, אל תקצר ואל תנסח מחדש. אל תכלול טבלאות ורשימות "
        "מספרים (למשל טבלת סיכום קואליציונית המסתיימת ב'סה\"כ'), את מקטע הקישורים ואת החתימה."
    )

    def __init__(self, master_programs, *, api_key=None, model=None, provider=None):
        self.master = set(master_programs)
        self.api_key = api_key
        self.provider = provider
        self.model = model or DEFAULT_MODELS.get(provider or "openai", DEFAULT_MODEL)

    @classmethod
    def from_config(cls, config, master_path: str) -> "Agent":
        """Build an Agent from a ConfigManager and the master.xlsx path.

        Pulls the master code set and the LLM settings from config, so callers don't
        hand-plumb them. The plain constructor stays for tests (no config needed).
        """
        return cls(
            config.read_master_programs(master_path),
            api_key=config.get_api_key(),
            model=config.get_model_name(),
            provider=config.get_model_provider(),
        )

    # ------------------------------------------------------------------ entry
    def extract(self, source: str) -> Extraction:
        """Extract one letter (URL or local path) into fields + table + relevance."""
        letter = BudgetLetter(source)
        text = letter.doc.text

        region = self._narrative_region(text)
        # The ONE LLM call: coalition judgment (+ reason) + the full request text.
        coalition, reason, summary, usage = self._analyze(region)
        summary = structure_summary(summary)
        fields = RequestFields(
            date=self._date(text),
            request_number=self._request_number(text),
            program_number=self._program_number(summary),
            request_summary=summary,
            decision_links=self._decision_links(letter),
            staffing_changes=self._staffing(text),
            coalition_funds=coalition,
        )

        table, matched = self._table(letter)
        return Extraction(fields, table, matched, bool(matched), letter, usage, reason)

    # --------------------------------------------------- deterministic tools
    @staticmethod
    def _date(text: str) -> str:
        """The request date from the table pages ("תאריך הבקשה: DD/MM/YYYY")."""
        m = re.search(r"תאריך הבקשה\s*:?\s*(\d{1,4}[./]\d{1,2}[./]\d{2,4})", text)
        return m.group(1) if m else ""

    def _request_number(self, text: str) -> str:
        """The 'בקשה מספר NN-NNN' numbers + the committee number, canonicalized."""
        numbers = list(dict.fromkeys(
            m.replace(" ", "")
            for m in re.findall(r"בקשה מספר\s*(\d{2,3}\s*-\s*\d{2,3})", text)
        ))
        committee = re.search(r"מספר פני\S* לו?ועדה\s*:?\s*([^\n]+)", text)
        parts = []
        if numbers:
            parts.append(", ".join(numbers))
        if committee:
            value = committee.group(1).strip()
            # The line reversal that fixes Hebrew also reverses a number list:
            # '47, 72' reads back as '72 ,47'. Put such lists back in order.
            if re.fullmatch(r"\d+(?: ,\d+)+", value):
                value = ", ".join(reversed(value.split(" ,")))
            parts.append(f"מספר פנייה לועדה: {value}")
        return self._canonical_request_numbers(" | ".join(parts))

    @staticmethod
    def _narrative_region(text: str) -> str:
        """The narrative text (verbatim), from AFTER 'עיקרי הפנייה:' up to the budget-table
        pages — a bounded window the LLM inspects for the exact end boundary. The heading
        itself is excluded (the report's field label supplies it), so it isn't doubled."""
        m = re.search(r"עיקרי+ הפנייה\s*:", text)
        if not m:
            return ""
        # The narrative ends at the staffing line / sign-off / appendix, whichever comes
        # first; the budget-table pages ('תאריך הבקשה') are a last resort. Keeping the
        # appendix tables out of the window matters: a model asked to "copy verbatim"
        # will otherwise copy them too.
        end = min(len(text), m.start() + 16000)
        for marker in (r"השפעה על כו?ח אדם", r"בכבוד רב", r"היסטוריה תקציבית", r"תאריך הבקשה"):
            hit = re.search(marker, text[m.end():])
            if hit:
                end = min(end, m.end() + hit.start())
        return text[m.end():end].strip()

    @staticmethod
    def _program_number(scope: str) -> str:
        """Program codes named as 'NNNNNN:' in the given text, in order, de-duplicated."""
        # 'NNNNNN:' headings anywhere in the text (the model may return the narrative as
        # one paragraph, so line starts cannot be relied on); a code is 5-6 digits that
        # is not part of a longer number, a date or a hyphenated request number.
        code = r"(\d{5,6}|\d{2}-\d{2}-\d{2})"
        pattern = re.compile(
            rf"תו?כנית\s*:?\s*{code}(?![\d-])"            # 'תוכנית 231039' / 'תוכנית 17-31-03:'
            rf"|(?<![\d.,/-]){code}\s*[:\-–](?!\d)")     # '231039:' / '19-42-02 -' as a heading
        # One pass, in document order; '17-31-03' is the same code as '173103'.
        codes = [(m.group(1) or m.group(2)).replace("-", "") for m in pattern.finditer(scope)]
        return ", ".join(dict.fromkeys(codes))

    @staticmethod
    def _staffing(text: str) -> str:
        """'כן'/'לא' from the labeled statement 'השפעה על כוח אדם:' (or an explicit
        'שינוי בכוח אדם'); absence defaults to 'לא'."""
        m = re.search(r"השפעה על כוח אדם\s*:?\s*(\S+)", text)
        if m:
            value = m.group(1)
            if any(neg in value for neg in ("אין", "אינם", "ללא")):
                return "לא"
            return "כן"
        return "כן" if re.search(r"שינוי\S* בכוח אדם", text) else "לא"

    @staticmethod
    def _decision_links(letter: BudgetLetter) -> str:
        """Government-decision links, from the raw (LTR) text via BudgetLetter."""
        links = letter._decision_links()
        return "\n".join(f"{i}. {url}" for i, url in enumerate(links, 1))

    @staticmethod
    def _section(text: str, start: str, ends: list[str],
                 *, include_start: bool = False) -> str:
        """Text between `start` (a regex) and the earliest following `ends` marker."""
        m = re.search(start, text)
        if m is None:
            return ""
        i = m.start() if include_start else m.end()
        stop = len(text)
        for end in ends:
            j = text.find(end, m.end())
            if j != -1:
                stop = min(stop, j)
        return text[i:stop].strip()

    @staticmethod
    def _canonical_request_numbers(value: str) -> str:
        """Order every hyphenated request number as NN-NNN (2-digit group first)."""
        def fix(m: re.Match) -> str:
            a, b = m.group(1), m.group(2)
            return f"{b}-{a}" if len(a) == 3 and len(b) == 2 else f"{a}-{b}"
        return re.sub(r"(\d{2,3})-(\d{2,3})", fix, value)

    # ------------------------------------------------------- the one LLM call
    def _analyze(self, region: str):
        """The ONE LLM call over the narrative region — NO fallback.

        Returns (coalition 'כן'/'לא', reason, request text, usage dict). An empty region
        (no narrative) is ('לא', '', '', None) with no call. Otherwise the model returns the
        coalition judgment and where the narrative ends; if the model is unavailable the
        error surfaces — it is not masked by a deterministic answer.
        """
        if not region:
            return "לא", "", "", None

        from langchain.chat_models import init_chat_model

        params: dict = {"temperature": 0, "max_retries": 3}
        if self.api_key:
            params["api_key"] = self.api_key
        if self.provider:
            params["model_provider"] = self.provider
        llm = init_chat_model(self.model, **params).with_structured_output(
            _Analysis, include_raw=True)
        result = llm.invoke([("system", self._ANALYSIS_SYSTEM), ("user", region)])
        parsed = result["parsed"]
        coalition = "כן" if "כן" in (parsed.coalition_funds or "").strip() else "לא"
        return (coalition, (parsed.coalition_reason or "").strip(),
                (parsed.request_summary or "").strip(), self._usage(result.get("raw")))

    def _usage(self, raw) -> dict:
        """Token counts + a rough USD cost estimate from the raw AIMessage."""
        meta = getattr(raw, "usage_metadata", None) or {}
        in_tok = meta.get("input_tokens", 0)
        out_tok = meta.get("output_tokens", 0)
        rate = PRICING_USD_PER_1M.get(self.model)
        cost = (in_tok * rate[0] + out_tok * rate[1]) / 1_000_000 if rate else None
        return {"model": self.model, "input_tokens": in_tok,
                "output_tokens": out_tok, "cost_usd": cost}

    # ------------------------------------------------------- table + master
    def _table(self, letter: BudgetLetter):
        """The combined budget table + a `master` column and the matched codes."""
        table = letter.extract_combined_table().copy()
        table[MASTER_COLUMN] = [
            "כן" if self._normalize(c) in self.master else "לא"
            for c in table[letter.CODE_COLUMN]
        ]
        matched = {self._normalize(c) for c in table[letter.CODE_COLUMN]} & self.master
        return table, matched

    @staticmethod
    def _normalize(code) -> str:
        """A program code as a zero-padded 6-digit string (matches the master set)."""
        return str(code).strip().zfill(6)
