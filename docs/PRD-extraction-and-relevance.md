# PRD — Budget-Letter Extraction, Master-Driven Relevance & Golden Tests

**Status:** Approved for implementation · **Date:** 2026-07-12
**Owner:** Ella · **Source of truth:** this document (supersedes chat scrollback)

---

## 1. Problem

The pipeline reads Israeli budget-transfer request letters (PDFs), extracts
summary fields + a budget table, decides whether the letter is relevant, and (if
so) renders a summary PDF. Two classes of defect were confirmed on request
**21658**:

1. **Fields silently dropped.** In `method="llm"`, `date` and `staffing_changes`
   come back empty and `program_number` is incomplete — because the letter text
   is truncated to 4,000 chars before the model sees it, and those fields live
   later in the document.
2. **The relevance decision is opaque and untested.** Whether a letter gets a
   summary depends on matching "master" program codes against the letter's table,
   but the matched code(s) aren't shown, the master set in use is stale, and there
   are no tests pinning any of it.

**Scope — applies to EVERY letter, not just 21658.** All requirements below are general
rules for any letter processed. **21658 is only the reference example** (§7) used to make
rules concrete. The golden suite (FR-7) grows one fixture per letter the user provides,
so each new letter is validated by the same rules.

## 2. Definitions (fixed vocabulary — use these exact terms)

| Term | Meaning |
|---|---|
| **Letter** | One budget-transfer request PDF, from its Knesset **URL** *or* a **local file path** — `PdfDocument`/`BudgetLetter` accept either interchangeably. |
| **Master** | `files/master.xlsx`. |
| **תוכניות sheet** | The sheet in Master listing program codes → names (`קוד`, `שם תוכנית`). |
| **Master code set** | The set of **all** program codes in the תוכניות sheet, normalized. |
| **Table** | The summary's budget table — a **faithful reproduction of the source PDF table** (§FR-3b): all source metric columns with exact-Hebrew headers, each item shown as two rows `מ-`/`ל-`, plus `מספר פרט` (code), `שם הפרט` (name), and the `master` column. |
| **Tracked / "problematic" row** | A table row whose `number` is in the Master code set. |
| **Relevant letter** | A letter with **≥ 1 tracked row**. Only relevant letters get a summary. |
| **Text fields** | The 8 `RequestFields`: date, request_number, program_number, request_summary, request_breakdown, decision_links, coalition_funds, staffing_changes. |
| **Normalized code** | A program code as a **zero-padded 6-digit string** (e.g. `45110` → `"045110"`). |

## 3. Architecture — ONE agent

There is a **single extraction agent** (LLM-based, LangChain), replacing the current
`parse` / `llm` / `hybrid` methods. The agent's **tools are deterministic extractors
that operate on the FULL letter text** (no `MAX_LLM_CHARS` cap). The agent orchestrates
the tools and provides **judgment only where a field genuinely needs it**. Purely
mechanical fields are produced by their tool directly, so they are exact, free, and
non-flaky; the LLM's reasoning is reserved for fuzzy fields.

### 3.1 Per-field classification (to confirm together — this is the key decision)

| Field | Proposed source | Rationale |
|---|---|---|
| `date` | **Deterministic tool** | fixed label `תאריך הבקשה:` |
| `request_number` | **Deterministic tool** | fixed `NN-NNN` + committee-number pattern |
| `program_number` | **Deterministic tool** | derived from the table codes |
| `request_summary` | **Deterministic tool** | verbatim `עיקרי הפנייה:` section |
| `request_breakdown` | **Deterministic tool** | verbatim breakdown section |
| `decision_links` | **Deterministic tool** | URLs from the raw (LTR) text |
| `staffing_changes` | **Deterministic tool** | labeled `השפעה על כוח אדם:` statement |
| `coalition_funds` | **LLM judgment — the ONE LLM call, NO fallback** | needs interpreting a *substantive* reference to coalition funding |
| Table (codes/amounts) | **Deterministic tool** | pdfplumber table parse |
| Tracked/"problematic" flag | **Deterministic tool** | membership in the Master code set |

> Everything is deterministic **except** `coalition_funds`, which is the single LLM
> call. There is **no deterministic fallback** for it: the model's answer stands, and if
> the model is unavailable the failure surfaces rather than being masked. (A letter with
> no coalition mention at all is `לא` — nothing to judge, not a fallback.) All logic lives
> in one `Agent` class (`bot/agent.py`).

## 4. Functional Requirements

> Each FR below is a **tool contract** under the single agent (§3). "Deterministic"
> means the tool alone produces the value; the LLM is not in the loop for that field.

### FR-1 — `date` is always extracted deterministically
- **Rule:** `date` is read from the full letter text via the parser pattern
  `תאריך הבקשה: DD/MM/YYYY`, regardless of extraction method. The LLM is never the
  source of `date`.
- **Acceptance:** for 21658, `date == "01/07/2026"` in `method="llm"` and
  `method="parse"`.

### FR-2 — `staffing_changes` is read from the labeled statement
- **Rule:** parse the statement `השפעה על כוח אדם:` — value `אין`/`אינם`/`ללא` → `לא`;
  any substantive value → `כן`. Fallback: the existing `שינוי בכוח אדם` phrase → `כן`.
  Sourced from the parser regardless of method.
- **Acceptance:** for 21658 (`השפעה על כוח אדם: אין`), `staffing_changes == "לא"`.
- **Constraint:** never invent — absence of a staffing statement defaults to `לא`.

### FR-2b — `decision_links`: use URLs, construct/capture when absent
- **Rule (decided):** collect government-decision references. Use explicit gov.il URLs
  when present. When a decision is referenced by number+date only
  (`החלטת ממשלה מספר NNN מיום DD.MM.YYYY`) with **no** URL: **always capture the reference
  text**, and **also** attempt a best-effort gov.il URL. The reference text is the
  reliable value (the slug format — `dec3610-2025` vs `dec549_2021` — is inconsistent),
  so a failed/uncertain URL never loses the reference.

### FR-3 — `program_number` is the plain list of described program codes
- **Rule:** `program_number` is the ordered, de-duplicated list of program codes written
  as `NNNNNN:` headings **anywhere in the narrative — summary AND breakdown** (a program
  can be introduced in `עיקרי הפנייה` and only detailed in the breakdown, e.g. `231039`
  in 21658). Deterministic and complete; format is a simple comma-separated list. Each
  program's own description already lives verbatim in `request_breakdown`.
- **Acceptance:** for 21658, `program_number` lists all programs named in the breakdown
  (230120, 231038, 230723, 230242, 230243, 231175, 231165, 231202, 230722, 231203,
  231039), not only the 4 the truncated LLM returned.

### FR-3b — Summary table faithfully reproduces the source PDF table
- **Rule — all columns:** keep **every** metric column present in the source budget
  table, not just expense. For these letters that is `הוצאה`, `הוצאה מותנית`,
  `הרשאה להתחייב`, `שיא כח אדם`, `עב"צ`. Column headers are the **exact Hebrew** used
  in the PDF (no English like `from`/`to`/`delta`).
- **Rule — two rows per item:** each budget item is rendered as **two** stacked rows,
  labeled **`מ-`** (from) and **`ל-`** (to), exactly as the PDF stacks them — not as
  `X from`/`X to` side-by-side columns.
- **Rule — one row-pair per occurrence (faithful, no dedupe):** a code that appears on
  N pages yields **N** `מ-`/`ל-` row-pairs, exactly as printed in the PDF. The table is
  not deduplicated.
- **Rule — delta is a COLUMN:** the change is a dedicated **`שינוי` column** (delta =
  `ל-` − `מ-`, the PDF's `סכומי השינוי`), **not** a third row. One `שינוי` column per
  metric that has a change. Its value is **per-item** (one value), rendered as a single
  cell spanning the item's two (`מ-`/`ל-`) rows.
- **Rule — per-item cells span two rows:** columns that are properties of the item
  rather than of a from/to row — `מספר פרט`, `שם הפרט`, `שינוי`, `master` — are rendered
  as one cell spanning the item's two rows, not repeated on each row.

### FR-3c — Keep only program-level rows (5–6 digit codes)
- **Rule:** include only rows whose code has **5–6 digits** (an actual program line,
  `תכנית`). Drop the aggregate rows — 2-digit `סעיף` (section, e.g. 12/47/54) and
  4-digit `תחום` (domain, e.g. 1211/4701). This is a length rule, **not** a hard-coded
  12/47 exclusion.
- **Acceptance:** for 21658, the table keeps only 6-digit program rows — one row-pair
  per occurrence, faithful to the PDF (24 `מ-`/`ל-` pairs, repeats included) — and drops
  every 2- and 4-digit aggregate row.

### FR-4 — Master code set is loaded from the תוכניות sheet
- **Rule:** provide a **read-only** loader that returns `{normalized_code: name}` for
  **every** row of the Master תוכניות sheet. It must NOT mutate or save config.
- **Acceptance:** the loader returns the full `תוכניות` code set (106 codes in the
  current master — see Appendix A); codes are normalized 6-digit strings; `045110`/
  `470102`-style letter codes and integer master codes compare equal.

### FR-5 — Relevance gate uses the full Master code set
- **Rule:** a letter is **relevant** iff at least one of its table `number` codes is in
  the Master code set (§FR-4). A summary is produced **iff** the letter is relevant.
- **Rule:** the config.json 7-code watchlist is **not** the relevance source; it is
  treated as stale.
- **Acceptance:** a letter with no tracked row produces **no** summary; a letter with
  ≥ 1 tracked row produces a summary. 21658 is relevant.

### FR-6 — The table shows a **"master"** column
- **Rule:** the table gains one column, header **`master`**, value `כן` when the item's
  normalized code is in the Master code set (§FR-4), else `לא`. It is a **per-item**
  value spanning the item's two (`מ-`/`ל-`) rows (§FR-3b), applied to the kept 5–6 digit
  rows (§FR-3c). Renders in the summary PDF.
- **Acceptance:** for 21658, every row whose code is in the `תוכניות` sheet shows
  `master = כן`; all other rows show `master = לא`. The summary is produced because at
  least one row has `master = כן` (§FR-5).

### FR-7 — Golden-test suite over real letters
- **Rule:** one fixture per letter at `bot/tests/fixtures/golden/<request>.json`:
  `{ request, source (URL **or** local path), method, text{8 fields}, table[rows incl. tracked flag], expect_summary }`.
  Ground truth (`text`, `table`, `expect_summary`) is authored by the user.
- **Rule:** a parametrized test runs extraction on each fixture's **source** (URL or
  local PDF path — `BudgetLetter` accepts either) and asserts:
  - **Text phase:** structured fields (date, request_number, program_number,
    coalition_funds, staffing_changes, decision_links) match **exactly**; free-text
    fields (request_summary, request_breakdown) match **whitespace-normalized**.
  - **Tables phase:** the combined table incl. the tracked column matches the fixture.
  - **Corrected phase:** `expect_summary == (letter has ≥ 1 tracked row)`.
- **Rule:** the test **skips** (not fails) when `OPENAI_API_KEY` is missing or the URL
  is unreachable.

## 5. Non-Goals

- Not sending raw truncated (or whole) document text to the LLM for it to eyeball —
  tools read the full text deterministically; the LLM sees tool outputs.
- Not replacing letter-provided table names with master names.
- Table changes are exactly those in FR-3b/FR-3c (all source metric columns, `מ-`/`ל-`
  two-row layout, `שינוי` + `master` columns, 5–6 digit rows). No *other* summary-PDF
  layout changes.
- The old `parse` / `llm` / `hybrid` method distinction and the parked
  validation-retry rewrite are **superseded** by the single agent (§3), not extended.

## 6. Non-Functional Requirements (performance)

- **NFR-1 — one LLM call, never twice.** At most a single LLM request per letter, with
  **no validation/retry round-trip**. "Don't send it twice."
- **NFR-2 — minimal payload.** The LLM receives only the **located snippet** the
  judgment needs (e.g. the coalition-funds paragraph found by a deterministic locator),
  never the full or truncated document. Small input = fast + cheap.
- **NFR-3 — deterministic fields cost nothing.** Fields marked deterministic in §3.1
  incur zero LLM latency/cost. If all fuzzy fields are absent in a letter, the agent
  makes **zero** LLM calls.

## 7. Reference ground truth (request 21658 — example only; rules are general per §1 Scope)

- `source`: `https://fs.knesset.gov.il/globaldocs/FINANCE/19689882-e675-f111-a13e-005056aa7c52/4_19689882-e675-f111-a13e-005056aa7c52_13_21658.pdf`
- `date`: `01/07/2026` · `staffing_changes`: `לא` · `coalition_funds`: `כן`
- Table: 24 rows — one `מ-`/`ל-` row-pair per 6-digit program-code occurrence, faithful
  to the PDF (16 distinct codes); `master` = `כן` for codes in the תוכניות sheet, else `לא`.
- `expect_summary`: `true` (relevant — ≥ 1 row with `master = כן`).

## 8. Open questions (defaults chosen — object if wrong)

- **Row dedupe:** RESOLVED — one row-pair **per occurrence, faithful to the PDF** (no
  dedupe). See FR-3b/FR-3c.
- **`staffing_changes` source (§3.1):** RESOLVED — fully deterministic (reads
  `השפעה על כוח אדם:`); no LLM.
- **`expect_summary`:** default = **derived** (`≥ 1 row with master=כן`), with a
  per-fixture manual override allowed.
- **`decision_links` with no URL (§FR-2b):** RESOLVED — capture reference text +
  best-effort URL.
- **`program_number` shape (§FR-3):** RESOLVED — plain code list; per-program
  descriptions stay in `request_breakdown`.

---

## Appendix A — Master program codes (תוכניות sheet)

All 106 program codes in `master.xlsx` → `תוכניות` sheet, normalized to
zero-padded 6-digit strings (§FR-4). This is the Master code set the relevance
gate (§FR-5) and the `master` column (§FR-6) check against.

- `045110` — פרויקטים והחלטות ממשלה
- `045201` — חברי הממשלה
- `045203` — משרד התפוצות
- `045204` — המשרד לנושאים אסטרטגים
- `045205` — המשרד למודיעין
- `045209` — לשכות שרים וסגני שרים
- `045211` — משרד ירושלים ומסורת
- `045212` — משרד מורשת
- `045213` — משרד ההתיישבות
- `045601` — שיתוף פעולה אזורי
- `045701` — המשרד לשוויון חברתי
- `045702` — הרשות לקידום מעמד
- `045703` — הרשות לפיתוח כלכלי של
- `046301` — שכר
- `046302` — תפעול
- `046303` — פיתוח הנגב והגליל
- `046401` — הרשות לפיתוח חברתי
- `130204` — פעילות ממשלתית רוחבית
- `130301` — אשראי לרשויות
- `130302` — פרוייקטים ברשויות
- `161303` — מענה לאיום בלתי
- `161602` — הקמה ואחזקת מרכיבי
- `161603` — מיגון
- `161605` — מיגון העורף
- `161703` — פרויקטי רשות חירום
- `173101` — מטה צבאי
- `173102` — שכר אזרחים
- `173103` — מטה אזרחי
- `173104` — תפיסות ופיקדונות
- `173105` — ארכיאולוגיה
- `173106` — מים
- `173108` — יחידת הפיקוח
- `173109` — פיתוח האזור
- `173110` — השתתפויות משרד הביטחון
- `173201` — מפקדה
- `173301` — מטה
- `173302` — שכר אזרחים
- `173303` — פעולות
- `181102` — מענקים אזוריים
- `181103` — מענקים שוטפים
- `181104` — מענקי פיתוח
- `181201` — קרן לצמצום פערים
- `194001` — פעילות משרד המדע
- `206201` — קדם יסודי
- `206202` — הגיל הרך
- `206302` — החינוך העצמאי
- `206303` — מעין החינוך התורני
- `206304` — מוכר שאינו רשמי
- `206305` — מוסדות הפטור
- `206501` — שירותי עזר, הסעות
- `206701` — פעילויות ופרוייקטים
- `206703` — מינהל החינוך הדתי
- `206901` — תרבות יהודית
- `206902` — מוסדות תורניים
- `211101` — השכלה גבוהה
- `220101` — מועצות דתיות
- `220102` — תמיכה בשירותי דת
- `220103` — בתי עלמין
- `220105` — שכר ותפעול
- `220201` — הרבנות הראשית
- `220301` — בתי הדין הרבניים
- `230242` — טיפול חוץ ביתי לאזרחים
- `230243` — שירותים קהילתיים
- `230513` — מחלקות לשירותים
- `230721` — טיפול חוץ ביתי באנשים
- `230722` — טיפול קהילתי באנשים עם
- `230723` — חוק שירותי רווחה
- `231038` — טיפול חוץ ביתי
- `231039` — שירותים קהילתיים
- `231202` — תמיכה במוסדות רווחה
- `310101` — הוצאות ביטחוניות
- `364001` — רגולציה, מחקר ואכיפה
- `364101` — סבסוד שהות ילדי הורים
- `364201` — עידוד תעסוקת אוכלוסיות
- `364202` — עידוד תעסוקת אוכלוסייה
- `364403` — הכשרה מקצועית, לרבות
- `364405` — הכשרת נוער - בתי
- `364406` — הכשרת הנדסאים וטכנאים
- `383001` — הפעלת הרשות לחדשנות
- `383002` — מענקי מחקר ופיתוח
- `383004` — מו"פ בין-לאומי
- `384001` — קידום השקעות ועידוד
- `405301` — הרשות לבטיחות בדרכים
- `405401` — רשות תחבורה ציבורית
- `405501` — רשות המטרו
- `420101` — מענקים וסבסוד ריבית
- `420103` — אשראי לדיור
- `420201` — סיוע בשכר דירה
- `544001` — הרשות לפיתוח והתיישבות
- `544002` — היחידה לשילוב
- `544003` — יישום החלטות ממשלה
- `545001` — מנהלת תקומה
- `545002` — תכניות ארוכות טווח
- `600210` — בניית כיתות לימוד
- `672503` — בינוי ופיתוח
- `701002` — בנייה חדשה
- `702001` — מרקם ותיק ופעולות
- `703001` — נכסים וניהול
- `703002` — משק דיור ציבורי
- `703003` — חוק מכר דירות
- `795101` — כבישים בין-עירוניים
- `795103` — כבישים לתחבורה ציבורית
- `830304` — ירושלים ומורשת - פיתוח
- `830305` — יהדות התפוצות
- `830402` — פרוייקטים בתכנון
- `830403` — פיתוח ההתישבות הבדואית
