# LLM Extractor — SOTA Structured Extraction Design

**Date:** 2026-07-12
**Module:** `bot/llm_extractor.py` (rewrite), `bot/request_fields.py` (additions)

## Goal

Modernize the budget-letter field extractor to the current state of the art for
*structured extraction* while keeping multi-provider flexibility. After
evaluation, the state of the art for a fixed-schema, one-shot extraction task is
**constrained structured decoding against a rich schema, plus a
validation-driven retry** — not an agent loop. `create_agent`/LangGraph is the
flagship pattern for multi-step tool-using tasks, which this is not, so it is
deliberately out of scope (see Rejected Alternatives).

## What stays

- The core call remains `model.with_structured_output(RequestFields)`. This is
  already the correct, current foundation and is not being replaced.
- `init_chat_model(model_name, **params)` with the existing temperature /
  `api_key` / `provider` handling — preserves OpenAI/Anthropic/Ollama swapping.
- Public API is unchanged:
  `extract_request_fields(text, *, api_key, model, provider) -> (RequestFields, dict)`.
  Callers (`budget_letter.py`, `evaluate.py`, `utils_function.py`) are untouched.
- `RequestFields` Hebrew aliases, `field_labels()`, and `check()` behavior.
- `MAX_LLM_CHARS` truncation; `_usage_and_cost` dict shape
  (`model`, `input_tokens`, `output_tokens`, `cost_usd`, `duration_s`).

## What changes

### 1. Per-field rules move into the schema (`request_fields.py`)

Each field on `RequestFields` gains a `Field(description=...)` carrying its
extraction rule, currently embedded in the large text `USER_PROMPT`:

- `date` — use "תאריך הבקשה" from the table pages, `DD/MM/YYYY`.
- `request_number` — only the `NN-NNN` numbers exactly as they appear, comma
  separated, no "בקשה מספר", then append "מספר פנייה לועדה: X".
- `program_number` — only codes appearing as the heading `תכנית NNNNNN:` inside
  the "עיקרי הפנייה בחלוקה לתוכניות" section; never from budget tables.
- `request_summary` — verbatim copy of the paragraph after "עיקרי הפנייה:".
- `request_breakdown` — verbatim copy of everything after "עיקרי הפנייה בחלוקה
  לתוכניות", including the `תכנית NNNNNN: ...` lines.
- `coalition_funds` — "כן"/"לא" only, per the explicit statement.
- `staffing_changes` — "כן"/"לא" only.
- `decision_links` — links to government decisions if present.

The model reads these descriptions during constrained decoding, improving
adherence and keeping each rule next to its field. Descriptions must avoid literal
example values that could be mistaken for data (use "format illustration only"
phrasing already present in the current prompt).

### 2. Slimmed prompts (`llm_extractor.py`)

- `SYSTEM_PROMPT` keeps the "precise extractor, never invent, empty if absent"
  role (unchanged).
- `USER_PROMPT` shrinks to essentially "extract the requested fields from the
  following budget letter; if a field is absent, return an empty string:\n{text}",
  since per-field rules now live in the schema.

### 3. Validation-driven retry (the reliability upgrade)

New, deterministic, bounded to **one** retry:

1. First structured call → `fields`.
2. Run `fields.check()`.
3. Select fields whose status is a **format** warning
   (`not DD/MM/YYYY`, `no NN-NNN number`, `not 5-6 digit codes`, `not כן/לא`).
   An empty-but-absent field (`⚠ empty` with no format rule) is **not** a retry
   trigger — this preserves the "never invent" guarantee and avoids fabrication
   pressure.
4. If any format errors: make **one** follow-up structured call. The follow-up
   message includes the original letter text plus a short correction note listing
   exactly which fields are malformed and why, instructing the model to re-read
   and fix only those fields (and to leave genuinely-absent fields empty). Use the
   second result.
5. If no format errors: return the first result.

A helper `_format_errors(fields: RequestFields) -> dict[str, str]` encapsulates
step 3 (pure, unit-testable, no LLM).

### 4. Usage & cost

`_usage_and_cost` keeps its output shape but sums `usage_metadata` across the up
to two raw messages so the retry's tokens are included. `duration_s` covers the
whole operation (both calls).

## Data flow

```
text
  -> truncate to MAX_LLM_CHARS
  -> init_chat_model(model, params).with_structured_output(RequestFields, include_raw=True)
  -> call #1 -> fields_1, raw_1
  -> _format_errors(fields_1)
       empty? -> return fields_1, usage(raw_1)
       nonempty -> call #2 (text + correction note) -> fields_2, raw_2
                -> return fields_2, usage(raw_1 + raw_2)
```

## Error handling

- `init_chat_model`'s `max_retries=5` still covers transient API errors on each
  call.
- The validation retry is a *quality* retry, independent of transport retries.
- If the second call still returns malformed fields, return it anyway (no third
  attempt); `check()` output remains available to callers/reports to flag it.

## Testing (`bot/tests/`)

- **Unit (no LLM):** `_format_errors` — malformed date/request_number/
  program_number/coalition_funds each selected; valid values and legitimately
  empty fields not selected.
- **Unit (no LLM):** schema descriptions present on all rule-bearing fields
  (guards against silent loss of a rule during refactor).
- **Integration (opt-in, behind existing model/provider knobs):** end-to-end
  extraction on a sample letter; asserts a `RequestFields` and a usage dict with
  summed tokens.

## Rejected alternatives

- **`create_agent` + validation tool loop:** the flagship LangChain pattern, but
  designed for multi-step tool-using tasks. For one-shot extraction it adds
  agent-loop unpredictability, higher/variable token cost, and LangGraph
  recursion semantics without a capability the deterministic retry lacks.
- **Single-shot `create_agent` (no tool):** newest import, but behaviorally
  equivalent to today's structured call — cosmetic.

## Open decisions (resolved)

- Retry count: **one** (a second re-read rarely fixes a format the model missed
  twice, and raises fabrication pressure).
- Correction delivery: **follow-up message including the original text** — simple
  and provider-agnostic.
- Loop gating by model: **none** — a clean first extraction never triggers a
  retry, so cheap models stay cheap without a brittle heuristic.
