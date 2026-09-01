# LLM Extractor SOTA Structured Extraction — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Modernize `bot/llm_extractor.py` to move per-field extraction rules into the `RequestFields` schema and add one deterministic validation-driven retry, while keeping the public API and multi-provider support unchanged.

**Architecture:** Keep the SOTA foundation — `init_chat_model(...).with_structured_output(RequestFields)`. Add (1) `Field(description=...)` rules on the schema so the model reads them during constrained decoding, and (2) a bounded single retry: run `RequestFields.check()`, and if any field is *malformed* (not merely empty), re-prompt once with a correction note. No agent / LangGraph.

**Tech Stack:** Python 3.14, LangChain 1.3.12 (`langchain`, `langchain-openai`, `langchain-ollama`), Pydantic v2, pytest, uv.

## Global Constraints

- Public API unchanged: `extract_request_fields(text, *, api_key=None, model=None, provider=None) -> tuple[RequestFields, dict]`. Callers (`bot/budget_letter.py`, `bot/evaluate.py`, `bot/utils_function.py`) must not need edits.
- Preserve multi-provider support via `init_chat_model` (`model_provider` from `provider`, gpt-5/o-series skip `temperature`, `max_retries=5`, `api_key` passthrough).
- Preserve `MAX_LLM_CHARS = 4000` truncation.
- Usage dict keeps its shape: `{"model", "input_tokens", "output_tokens", "cost_usd", "duration_s"}`.
- Retry is bounded to **one** extra call; empty-but-absent fields never trigger a retry (no fabrication pressure).
- Tests run from the `bot/` directory: `cd bot && uv run pytest tests/ -v` (bare imports, no package prefix).
- All prompt/description strings are Hebrew, matching the existing module.

## File Structure

- `bot/request_fields.py` — modify: add `description=` to the 8 fields. Responsibility unchanged (the typed result + `check()`).
- `bot/llm_extractor.py` — rewrite: slim prompts, `_format_errors`, `_correction_note`, `_sum_usage`, retry in `extract_request_fields`.
- `bot/tests/test_llm_extractor.py` — create: pure unit tests for `_format_errors`, `_correction_note`, and schema descriptions (no LLM).

---

### Task 1: Move per-field rules into the RequestFields schema

**Files:**
- Modify: `bot/request_fields.py:20-27` (the 8 `Field(...)` declarations)
- Test: `bot/tests/test_llm_extractor.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `RequestFields` fields each carry a non-empty `.description`. `field_labels()`, aliases, and `check()` behavior are unchanged.

- [ ] **Step 1: Write the failing test**

Create `bot/tests/test_llm_extractor.py`:

```python
"""Unit tests for the LLM extractor helpers and schema — no network/LLM needed."""
from request_fields import RequestFields


def test_all_rule_fields_have_descriptions():
    rule_fields = [
        "date", "request_number", "program_number", "request_summary",
        "request_breakdown", "coalition_funds", "staffing_changes",
    ]
    for name in rule_fields:
        info = RequestFields.model_fields[name]
        assert info.description, f"{name} is missing a Field(description=...)"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd bot && uv run pytest tests/test_llm_extractor.py::test_all_rule_fields_have_descriptions -v`
Expected: FAIL (`date is missing a Field(description=...)`).

- [ ] **Step 3: Add descriptions to the schema**

In `bot/request_fields.py`, replace the field block (lines 20-27) with:

```python
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
        description=("העתק מילה במילה את הפסקה שאחרי הכותרת 'עיקרי הפנייה:'. "
                     "אל תסכם, אל תנסח מחדש ואל תשנה מספרים."))
    request_breakdown: str = Field(
        default="", alias="עיקרי הפנייה בחלוקה לתוכניות",
        description=("העתק מילה במילה את כל הטקסט שאחרי הכותרת 'עיקרי הפנייה בחלוקה לתוכניות', "
                     "כולל שורות 'תכנית NNNNNN: ...' ותיאוריהן. אל תסכם ואל תנסח מחדש."))
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd bot && uv run pytest tests/test_llm_extractor.py::test_all_rule_fields_have_descriptions -v`
Expected: PASS.

- [ ] **Step 5: Verify existing behavior is intact**

Run: `cd bot && uv run python -c "from request_fields import RequestFields; print(RequestFields.field_labels())"`
Expected: prints the 8 Hebrew labels (aliases unchanged).

- [ ] **Step 6: Commit**

```bash
git add bot/request_fields.py bot/tests/test_llm_extractor.py
git commit -m "feat: embed per-field extraction rules in RequestFields schema"
```

---

### Task 2: Add the `_format_errors` malformed-field selector

**Files:**
- Modify: `bot/llm_extractor.py` (add helper near the other module-level helpers)
- Test: `bot/tests/test_llm_extractor.py`

**Interfaces:**
- Consumes: `RequestFields.check()` (returns `{hebrew_label: "OK" | "— none" | "⚠ empty" | "⚠ <reason>"}`).
- Produces: `_format_errors(fields: RequestFields) -> dict[str, str]` — Hebrew label → warning string, containing only *format* warnings (present-but-malformed). Excludes `"⚠ empty"` and non-warning statuses.

- [ ] **Step 1: Write the failing tests**

Append to `bot/tests/test_llm_extractor.py`:

```python
from llm_extractor import _format_errors


def test_format_errors_flags_malformed_date():
    fields = RequestFields(date="2020-31-12")  # not DD/MM/YYYY
    assert "תאריך" in _format_errors(fields)


def test_format_errors_flags_bad_coalition_value():
    fields = RequestFields(coalition_funds="אולי")  # not כן/לא
    assert "האם יש התייחסות לכספים קואליציונים" in _format_errors(fields)


def test_format_errors_flags_missing_request_number_pattern():
    fields = RequestFields(request_number="בקשה ללא מספר")  # no NNN-NN
    assert "מס' פנייה" in _format_errors(fields)


def test_format_errors_ignores_empty_field():
    # An absent field must NOT trigger a retry (no fabrication pressure).
    fields = RequestFields(coalition_funds="")
    assert "האם יש התייחסות לכספים קואליציונים" not in _format_errors(fields)


def test_format_errors_empty_when_all_valid():
    fields = RequestFields(
        date="31/12/2020",
        request_number="202-15 | מספר פנייה לועדה: 3",
        program_number="123456",
        request_summary="תקציר כלשהו",
        request_breakdown="פירוט כלשהו",
        coalition_funds="לא",
        staffing_changes="לא",
    )  # decision_links empty -> "— none", not a warning
    assert _format_errors(fields) == {}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd bot && uv run pytest tests/test_llm_extractor.py -k format_errors -v`
Expected: FAIL (`ImportError: cannot import name '_format_errors'`).

- [ ] **Step 3: Implement `_format_errors`**

Add to `bot/llm_extractor.py` (after the imports / near `_usage_and_cost`):

```python
def _format_errors(fields: RequestFields) -> dict[str, str]:
    """Fields that are present but malformed, per RequestFields.check().

    Returns {hebrew_label: warning}. Excludes "⚠ empty" (an absent field is allowed
    to stay empty; retrying on emptiness would pressure the model to invent data) and
    non-warning statuses ("OK", "— none").
    """
    return {label: status for label, status in fields.check().items()
            if status.startswith("⚠") and status != "⚠ empty"}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd bot && uv run pytest tests/test_llm_extractor.py -k format_errors -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add bot/llm_extractor.py bot/tests/test_llm_extractor.py
git commit -m "feat: add _format_errors selector for malformed extracted fields"
```

---

### Task 3: Rewrite the extractor with slim prompts and a validation retry

**Files:**
- Modify: `bot/llm_extractor.py` (prompts, `_sum_usage`, `_correction_note`, `extract_request_fields`)
- Test: `bot/tests/test_llm_extractor.py`

**Interfaces:**
- Consumes: `_format_errors` (Task 2), `RequestFields` with descriptions (Task 1), `init_chat_model`.
- Produces: `_correction_note(errors: dict[str, str]) -> str`; `_sum_usage(raws: list, model_name: str) -> dict`; unchanged public `extract_request_fields(...)`.

- [ ] **Step 1: Write the failing test for `_correction_note`**

Append to `bot/tests/test_llm_extractor.py`:

```python
from llm_extractor import _correction_note


def test_correction_note_lists_each_malformed_field():
    note = _correction_note({"תאריך": "⚠ not DD/MM/YYYY",
                             "האם יש התייחסות לכספים קואליציונים": "⚠ not כן/לא"})
    assert "תאריך" in note
    assert "האם יש התייחסות לכספים קואליציונים" in note
    # Must reinforce "do not invent" so empty stays empty.
    assert "אל תמציא" in note
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd bot && uv run pytest tests/test_llm_extractor.py::test_correction_note_lists_each_malformed_field -v`
Expected: FAIL (`ImportError: cannot import name '_correction_note'`).

- [ ] **Step 3: Rewrite `bot/llm_extractor.py`**

Replace the whole file with:

```python

from __future__ import annotations

import logging
import re
import time

from dotenv import load_dotenv
from langchain.chat_models import init_chat_model

from request_fields import RequestFields

# Ensure OPENAI_API_KEY is loaded from .env even when this module is imported
# standalone rather than through main.py.
load_dotenv()

logger = logging.getLogger(__name__)

# Max characters of letter text sent to the LLM. The request-letter fields all live
# in the leading narrative; capping keeps a full 16-page letter under gpt-4o's
# 30k tokens-per-minute limit.
MAX_LLM_CHARS = 4000

DEFAULT_MODEL = "gpt-4o"

# Rough list price, USD per 1M tokens (input, output). Used only for the on-report
# cost estimate — the provider's usage dashboard is authoritative.
PRICING_USD_PER_1M = {
    "gpt-4o": (2.50, 10.00),
    "gpt-4o-mini": (0.15, 0.60),
}

# A stable "role" for the model: a precise extractor that never invents data. The
# per-field rules now live in the RequestFields schema descriptions, which the model
# reads during structured decoding — so the user prompt stays minimal.
SYSTEM_PROMPT = (
    "אתה מנוע חילוץ מדויק של שדות מתוך פניות תקציביות של אגף התקציבים. "
    "החזר אך ורק מידע המופיע במפורש בטקסט — אל תמציא, אל תשלים ואל תנחש מידע חסר. "
    "אם שדה אינו מופיע בטקסט, החזר עבורו מחרוזת ריקה."
)

USER_PROMPT = (
    "הטקסט הבא הוא פנייה תקציבית של אגף התקציבים. חלץ את השדות המבוקשים לפי "
    "התיאור של כל שדה בסכימה. אם שדה אינו מופיע בטקסט, החזר עבורו מחרוזת ריקה.\n\n"
    "{text}"
)


def _sum_usage(raws: list, model_name: str) -> dict:
    """Token counts + a rough USD cost estimate, summed across all raw AIMessages
    (the validation retry makes more than one call)."""
    in_tok = sum((getattr(r, "usage_metadata", None) or {}).get("input_tokens", 0)
                 for r in raws)
    out_tok = sum((getattr(r, "usage_metadata", None) or {}).get("output_tokens", 0)
                  for r in raws)
    rate = PRICING_USD_PER_1M.get(model_name)
    cost = (in_tok * rate[0] + out_tok * rate[1]) / 1_000_000 if rate else None
    return {"model": model_name, "input_tokens": in_tok,
            "output_tokens": out_tok, "cost_usd": cost}


def _format_errors(fields: RequestFields) -> dict[str, str]:
    """Fields that are present but malformed, per RequestFields.check().

    Returns {hebrew_label: warning}. Excludes "⚠ empty" (an absent field is allowed
    to stay empty; retrying on emptiness would pressure the model to invent data) and
    non-warning statuses ("OK", "— none").
    """
    return {label: status for label, status in fields.check().items()
            if status.startswith("⚠") and status != "⚠ empty"}


def _correction_note(errors: dict[str, str]) -> str:
    """A short Hebrew instruction listing malformed fields for the single retry."""
    lines = "\n".join(f"- {label}: {status}" for label, status in errors.items())
    return (
        "בחילוץ הקודם השדות הבאים חזרו בפורמט שגוי. קרא שוב את הטקסט ותקן אך ורק את "
        "השדות הללו לפורמט הנכון. אם שדה אינו מופיע בטקסט, השאר אותו ריק — אל תמציא:\n"
        + lines
    )


def extract_request_fields(text: str, *, api_key: str | None = None,
                           model: str | None = None,
                           provider: str | None = None) -> tuple[RequestFields, dict]:
    """Extract the request fields from letter `text` via LangChain structured output.

    Foundation: init_chat_model(...).with_structured_output(RequestFields). After the
    first call, RequestFields.check() is run; if any field is present-but-malformed
    (a format error, not merely empty), the model is re-prompted ONCE with a correction
    note listing those fields. Empty/absent fields never trigger a retry, preserving the
    "never invent" guarantee.

    `provider` (e.g. "openai", "ollama") is passed to init_chat_model when set; when
    None it is inferred from the model name (gpt-* -> openai).
    """
    text = text[:MAX_LLM_CHARS]

    model_name = model or DEFAULT_MODEL
    params: dict = {"max_retries": 5}
    if not re.match(r"(gpt-5|o\d)", model_name):
        params["temperature"] = 0
    if api_key:
        params["api_key"] = api_key
    if provider:
        params["model_provider"] = provider

    # include_raw=True returns {"parsed": RequestFields, "raw": AIMessage, ...} so we
    # can read token usage off the raw message alongside the parsed fields.
    llm = init_chat_model(model_name, **params).with_structured_output(
        RequestFields, include_raw=True)

    base_user = USER_PROMPT.format(text=text)
    start = time.perf_counter()
    result = llm.invoke([("system", SYSTEM_PROMPT), ("user", base_user)])
    fields = result["parsed"]
    raws = [result.get("raw")]

    errors = _format_errors(fields)
    if errors:
        retry_user = base_user + "\n\n" + _correction_note(errors)
        result = llm.invoke([("system", SYSTEM_PROMPT), ("user", retry_user)])
        fields = result["parsed"]
        raws.append(result.get("raw"))

    elapsed = time.perf_counter() - start
    usage = _sum_usage(raws, model_name)
    usage["duration_s"] = elapsed
    logger.info(
        "llm extraction via LangChain (%s): %s call(s), %s tokens (~$%s)",
        model_name, len(raws), usage["input_tokens"] + usage["output_tokens"],
        f"{usage['cost_usd']:.4f}" if usage["cost_usd"] is not None else "n/a")
    return fields, usage
```

- [ ] **Step 4: Run the note test to verify it passes**

Run: `cd bot && uv run pytest tests/test_llm_extractor.py::test_correction_note_lists_each_malformed_field -v`
Expected: PASS.

- [ ] **Step 5: Run the full unit suite (still no LLM)**

Run: `cd bot && uv run pytest tests/test_llm_extractor.py -v`
Expected: PASS (all `_format_errors`, `_correction_note`, and description tests).

- [ ] **Step 6: Confirm the public API and callers are intact**

Run: `cd bot && uv run python -c "import inspect, llm_extractor; print(inspect.signature(llm_extractor.extract_request_fields))"`
Expected: `(text: str, *, api_key: str | None = None, model: str | None = None, provider: str | None = None) -> tuple[request_fields.RequestFields, dict]`

Run: `cd bot && uv run python -c "import budget_letter, evaluate, utils_function; print('imports OK')"`
Expected: `imports OK` (no signature/attribute errors in callers).

- [ ] **Step 7: Commit**

```bash
git add bot/llm_extractor.py bot/tests/test_llm_extractor.py
git commit -m "feat: slim extractor prompts and add validation-driven retry"
```

---

### Task 4: End-to-end verification (opt-in, real LLM)

**Files:**
- None (manual/optional verification; requires `OPENAI_API_KEY`).

**Interfaces:**
- Consumes: `extract_request_fields`, the existing live fixtures.

- [ ] **Step 1: Run the existing live LLM path against a real letter**

Run: `cd bot && uv run pytest tests/test_budget_letter_live.py::test_live_letter_llm_summary_pdf -v`
Expected: PASS, or SKIP if the Knesset site is unreachable / no API key. (This exercises `method="llm"` → `extract_request_fields` end to end.)

- [ ] **Step 2: Spot-check a single extraction and its usage dict**

Run:
```bash
cd bot && uv run python -c "
from budget_letter import BudgetLetter
from tests.test_budget_letter_live import LETTER_URL
letter = BudgetLetter.from_url(LETTER_URL)
fields = letter.extract_request_fields(method='llm')
print(fields.model_dump(by_alias=True))
print(letter.last_llm_usage)
"
```
Expected: a fields dict with Hebrew keys and a usage dict containing `input_tokens`, `output_tokens`, `cost_usd`, `duration_s`. (If `BudgetLetter.from_url` is not the exact constructor, use the same construction the live test uses.)

- [ ] **Step 3: Run the whole test suite for regressions**

Run: `cd bot && uv run pytest tests/ -v`
Expected: PASS or SKIP (no failures).

---

## Self-Review

**Spec coverage:**
- "Rules move into schema" → Task 1. ✓
- "Single structured call / slim prompts" → Task 3 (prompts + `extract_request_fields`). ✓
- "Validation-driven retry, one attempt, format-only, empty excluded" → Task 2 (`_format_errors`) + Task 3 (retry). ✓
- "Usage sums across calls, same shape" → Task 3 (`_sum_usage`). ✓
- "Public API unchanged, callers untouched" → Task 3 Step 6 verification. ✓
- "Tests: `_format_errors` selector, descriptions present, integration opt-in" → Tasks 1, 2, 4. ✓
- "Reject create_agent" → no LangGraph anywhere. ✓

**Placeholder scan:** No TBD/TODO; every code step shows complete code. ✓

**Type consistency:** `_format_errors(RequestFields) -> dict[str, str]`, `_correction_note(dict[str,str]) -> str`, `_sum_usage(list, str) -> dict` used consistently across Tasks 2–3; public signature identical to the original. ✓
