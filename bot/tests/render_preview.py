"""Fast summary-PDF preview — render straight from a saved golden fixture.

The normal pipeline (bot/main.py) re-parses the 18 MB / 378-page source PDF with
pdfplumber and makes an LLM call before it can render — many seconds per run. The
golden fixtures already hold the extracted `fields` and `table`, so this script skips
both and renders the summary PDF in ~1s, for eyeballing layout changes in reports.py.

The letterhead and the 'היסטוריה תקציבית' appendix table are NOT in the golden data —
they come from the source PDF. We extract them ONCE from the local test_files PDF and
cache them in fixtures/golden/<req>_extras.json, so the first run for a request is slow
but every later run is fast and the preview stays faithful (incl. the appendix table).

    python bot/tests/render_preview.py            # defaults to 21658
    python bot/tests/render_preview.py 21656
"""
import json
import os
import re
import sys

import pandas as pd

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "bot"))

from common.config_manager import ConfigManager  # noqa: E402
from reports import Reports  # noqa: E402
from request_fields import RequestFields  # noqa: E402

GOLDEN = os.path.join(os.path.dirname(__file__), "fixtures", "golden")
MASTER = os.path.join(_ROOT, "files", "master.xlsx")
TEST_FILES = os.path.join(os.path.dirname(__file__), "test_files")
OUTPUT_DIR = os.path.join(_ROOT, "files", "outputs")
FALLBACK_LETTERHEAD = ["מדינת ישראל", "האוצר - אגף התקציבים", "תקציב רגיל"]


def _local_pdf(request: str) -> str | None:
    """The test_files PDF for a request (filename embeds the request number), if present."""
    for fname in os.listdir(TEST_FILES):
        if not fname.endswith(".pdf"):
            continue
        m = re.search(r"_13_(\d+)\.pdf$", fname) or re.match(r"(\d+)_original\.pdf$", fname)
        if m and m.group(1) == request:
            return os.path.join(TEST_FILES, fname)
    return None


def _extras(request: str) -> dict:
    """Load (or build + cache) the letterhead + budget-history the golden data lacks."""
    cache = os.path.join(GOLDEN, f"{request}_extras.json")
    if os.path.isfile(cache):
        return json.load(open(cache, encoding="utf-8"))

    pdf = _local_pdf(request)
    if pdf is None:  # no local PDF -> render without the appendix (still useful)
        return {"letterhead": FALLBACK_LETTERHEAD, "history": None}

    from budget_letter import BudgetLetter
    print(f"building {request}_extras.json from {os.path.basename(pdf)} (one-time)...")
    letter = BudgetLetter(pdf)
    extras = {
        "letterhead": letter.extract_letterhead(),
        # budget_history is now a list of (title, DataFrame) pairs — one per metric.
        "history": [{"title": title, "columns": list(df.columns), "data": df.values.tolist()}
                    for title, df in letter.extract_budget_history()],
    }
    json.dump(extras, open(cache, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    return extras


def render(request: str) -> str:
    golden = json.load(open(os.path.join(GOLDEN, f"{request}.json"), encoding="utf-8"))
    extras = _extras(request)
    budget_history = [(h["title"], pd.DataFrame(h["data"], columns=h["columns"]))
                      for h in (extras["history"] or [])]

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    out = os.path.join(OUTPUT_DIR, f"{request}_preview.pdf")
    return Reports().write_summary(
        out,
        # write_summary looks up Hebrew field labels; golden["text"] has the English
        # attribute names, so rebuild the RequestFields (model_dump by_alias -> Hebrew).
        fields=RequestFields(**golden["text"]),
        table=pd.DataFrame(golden["table"]),
        letterhead=extras["letterhead"],
        name_column="name",
        budget_history=budget_history,
        source_url=golden.get("source"),
        llm_usage=None,
        request_id=request,
        master_names=(ConfigManager.read_master_names(MASTER) if os.path.isfile(MASTER) else None),
    )


if __name__ == "__main__":
    req = sys.argv[1] if len(sys.argv) > 1 else "21658"
    print("wrote", render(req))
