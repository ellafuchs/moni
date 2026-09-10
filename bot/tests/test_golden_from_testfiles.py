"""Golden tests driven by the local PDFs in bot/tests/test_files/.

Each test_files PDF whose request has a golden fixture (bot/tests/fixtures/golden/
<req>.json) becomes a parametrized case. The deterministic test runs offline (no
network, no LLM) and checks the fields the Agent derives without the model; the
full-extraction test runs the whole Agent (incl. the one coalition LLM call) and
is skipped without an API key for the provider set in files/config.json.
"""
import json
import os
import re
import sys

import pytest
from dotenv import load_dotenv

# Project root importable so `common` resolves (mirrors bot/main.py).
_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "bot"))
load_dotenv()

from common.config_manager import ConfigManager  # noqa: E402
from agent import Agent, MASTER_COLUMN  # noqa: E402
from budget_letter import BudgetLetter  # noqa: E402

MASTER = os.path.join(_ROOT, "files", "master.xlsx")
TEST_FILES = os.path.join(os.path.dirname(__file__), "test_files")
GOLDEN = os.path.join(os.path.dirname(__file__), "fixtures", "golden")
CONFIG = os.path.join(_ROOT, "files", "config.json")


def _agent() -> Agent:
    """The Agent as the app builds it: provider/model/key from files/config.json (+ .env);
    falls back to the OpenAI defaults when no local config exists."""
    if os.path.isfile(CONFIG):
        return Agent.from_config(ConfigManager(CONFIG), MASTER)
    return Agent(ConfigManager.read_master_programs(MASTER))


def _has_key() -> bool:
    if os.path.isfile(CONFIG):
        return bool(ConfigManager(CONFIG).get_api_key())
    return bool(os.getenv("OPENAI_API_KEY"))


_HAS_KEY = _has_key()


def _cases():
    """(request, pdf_path, golden_dict) for every test_files PDF that has a fixture."""
    out = []
    for fname in sorted(os.listdir(TEST_FILES)):
        if not fname.endswith(".pdf"):
            continue
        m = re.search(r"_13_(\d+)\.pdf$", fname) or re.match(r"(\d+)_original\.pdf$", fname)
        if not m:
            continue
        req = m.group(1)
        golden_path = os.path.join(GOLDEN, f"{req}.json")
        if os.path.isfile(golden_path):
            out.append((req, os.path.join(TEST_FILES, fname),
                        json.load(open(golden_path, encoding="utf-8"))))
    return out


CASES = _cases()
IDS = [c[0] for c in CASES]


@pytest.mark.skipif(not CASES, reason="no test_files PDF has a golden fixture")
@pytest.mark.parametrize("req,pdf,golden", CASES, ids=IDS)
def test_deterministic_fields_match_golden(req, pdf, golden):
    """Fields the Agent derives WITHOUT the LLM — offline, no network."""
    letter = BudgetLetter(pdf)
    text = letter.doc.text
    master = set(ConfigManager.read_master_programs(MASTER))
    agent = Agent(master)

    assert Agent._date(text) == golden["text"]["date"]
    assert agent._request_number(text) == golden["text"]["request_number"]
    assert Agent._staffing(text) == golden["text"]["staffing_changes"]

    # table + master relevance (no LLM involved)
    table, matched = agent._table(letter)
    assert MASTER_COLUMN in table.columns
    assert sorted(matched) == golden["master_matched"]
    assert bool(matched) == golden["expect_summary"]


@pytest.mark.skipif(not (CASES and _HAS_KEY),
                    reason="test_files fixtures or LLM API key (configured provider) not present")
@pytest.mark.parametrize("req,pdf,golden", CASES, ids=IDS)
def test_full_extraction_matches_golden(req, pdf, golden):
    """The whole Agent, incl. the one coalition LLM call — needs an API key."""
    result = _agent().extract(pdf)

    assert result.fields.date == golden["text"]["date"]
    assert result.fields.staffing_changes == golden["text"]["staffing_changes"]
    assert result.fields.coalition_funds == golden["text"]["coalition_funds"]
    assert result.fields.program_number == golden["text"]["program_number"]
    assert sorted(result.matched_codes) == golden["master_matched"]
    assert result.relevant == golden["expect_summary"]
