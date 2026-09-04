"""FR-7 — golden tests over a real letter, driven by the single Agent.

The offline test exercises the Agent's deterministic tools on the saved raw text (no
network/LLM). The end-to-end test runs the whole Agent (incl. the one coalition LLM
call) on 21658's local PDF; it skips without an API key for the configured provider (there is no deterministic
fallback for coalition_funds).
"""
import os
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

MASTER = os.path.join(_ROOT, "files", "master.xlsx")
PDF_21658 = os.path.join(_ROOT, "bot", "tests", "test_files", "21658_original.pdf")
RAW_21658 = os.path.join(os.path.dirname(__file__), "fixtures", "golden", "21658_raw.txt")
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


@pytest.mark.skipif(not os.path.isfile(RAW_21658), reason="21658 raw text not present")
def test_program_number_includes_programs_from_summary_and_breakdown():
    # FR-3: program codes named anywhere in the narrative. 231039 is introduced in the
    # summary and only detailed in the breakdown — it must still appear. Deterministic,
    # so no LLM/network.
    raw = open(RAW_21658, encoding="utf-8").read()
    codes = [c.strip() for c in Agent._program_number(Agent._narrative_region(raw)).split(",")]
    for code in ("231039", "230120", "231038", "230723", "230242", "230243",
                 "231175", "231165", "231202", "230722", "231203"):
        assert code in codes, f"{code} missing from program_number ({codes})"


@pytest.mark.skipif(not (os.path.isfile(PDF_21658) and _HAS_KEY),
                    reason="21658 PDF or LLM API key (configured provider) not present")
def test_agent_extract_21658():
    """The whole Agent — deterministic fields + the one coalition LLM call + master."""
    result = _agent().extract(PDF_21658)

    # deterministic fields
    assert result.fields.date == "01/07/2026"
    assert result.fields.staffing_changes == "לא"
    assert "231039" in result.fields.program_number
    # the one LLM call
    assert result.fields.coalition_funds == "כן"

    # relevance (FR-5): 10 master matches -> relevant
    assert result.relevant is True
    assert len(result.matched_codes) == 10

    # master column (FR-6): כן/לא per row, correct for a known match and non-match
    assert MASTER_COLUMN in result.table.columns
    by_code = dict(zip(result.table["number"], result.table[MASTER_COLUMN]))
    assert by_code.get("231039") == "כן"   # in master
    assert by_code.get("470102") == "לא"   # not in master
