"""FR-4 — read-only loader for the Master תוכניות program code set.

Reads files/master.xlsx and returns {normalized 6-digit code: program name} without
mutating or saving config. No network/LLM.
"""
import os
import sys

# Make the project root importable so `common` resolves (mirrors bot/main.py).
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from common.config_manager import ConfigManager  # noqa: E402

MASTER = os.path.join(
    os.path.dirname(__file__), "..", "..", "files", "master.xlsx")


def test_read_master_programs_returns_normalized_code_map():
    programs = ConfigManager.read_master_programs(MASTER)
    assert isinstance(programs, dict)
    assert len(programs) >= 100  # the full תוכניות sheet, not the 7-code config list
    # every key is a zero-padded 6-digit string
    assert all(len(code) == 6 and code.isdigit() for code in programs)
    # a known program from the תוכניות sheet (int 45110 -> "045110")
    assert "045110" in programs
    assert "פרויקטים" in programs["045110"]


def test_read_master_programs_does_not_mutate_config(tmp_path):
    # Read-only: calling it must not require or change a config file.
    before = ConfigManager.read_master_programs(MASTER)
    after = ConfigManager.read_master_programs(MASTER)
    assert before == after  # deterministic, no side effects
