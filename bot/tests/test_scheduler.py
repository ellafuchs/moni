"""The web scheduler fires only on an enabled day and minute inside an active range."""
import os
import sys
from datetime import datetime

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, _ROOT)

from web.scheduler import due_now  # noqa: E402

SCHEDULE = [{
    "from": "2026-09-01", "to": "2026-09-30",
    "days": {"sat": {"enabled": True, "time": "01:57"}, "sun": {"enabled": False, "time": "01:57"}},
}]


def test_fires_on_enabled_day_and_minute():
    assert due_now(SCHEDULE, datetime(2026, 9, 5, 1, 57, 30))   # a Saturday


def test_not_on_other_minute_or_disabled_day():
    assert not due_now(SCHEDULE, datetime(2026, 9, 5, 1, 58))
    assert not due_now(SCHEDULE, datetime(2026, 9, 6, 1, 57))   # Sunday, disabled


def test_not_outside_the_date_range_or_with_junk():
    assert not due_now(SCHEDULE, datetime(2026, 10, 3, 1, 57))  # a Saturday in October
    assert not due_now([{"from": "bad"}, {}], datetime(2026, 9, 5, 1, 57))
    assert not due_now(None, datetime(2026, 9, 5, 1, 57))
