"""Run the bot on the schedule saved from the web app (issue 4).

A daemon thread inside the Flask process wakes every few seconds, reads the saved
schedule (so edits made on the site take effect without a restart) and, when the
current minute matches an enabled day and time inside an active date range, starts
`bot/main.py` as a separate process. A run starts at most once per matching minute.

Only one process may own the scheduler. With `flask --debug` the reloader runs two
processes, so the thread takes an exclusive lock on `files/scheduler.lock` and the
loser quietly does nothing.
"""
from __future__ import annotations

import fcntl
import logging
import subprocess
import sys
import threading
import time
from datetime import date, datetime
from pathlib import Path

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent
BOT_MAIN = ROOT / "bot" / "main.py"
# datetime.weekday(): Monday is 0. The site saves days under these keys.
DAY_KEYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


def due_now(schedule: list[dict] | None, now: datetime) -> bool:
    """True when `now` falls on an enabled day and time of an active range."""
    today = now.date()
    hhmm = now.strftime("%H:%M")
    key = DAY_KEYS[now.weekday()]
    for rng in schedule or []:
        try:
            start = date.fromisoformat(str(rng["from"]))
            end = date.fromisoformat(str(rng["to"]))
        except (KeyError, TypeError, ValueError):
            continue
        if not start <= today <= end:
            continue
        day = (rng.get("days") or {}).get(key) or {}
        if day.get("enabled") and day.get("time") == hhmm:
            return True
    return False


def start_bot() -> subprocess.Popen:
    """Start one pipeline run in its own process; the site keeps serving meanwhile."""
    return subprocess.Popen([sys.executable, str(BOT_MAIN)], cwd=ROOT)


class Scheduler(threading.Thread):
    def __init__(self, config, lock_path: Path, interval: float = 15.0):
        super().__init__(name="moni-scheduler", daemon=True)
        self.config = config
        self.lock_path = lock_path
        self.interval = interval
        self.last_run: str | None = None   # "YYYY-MM-DD HH:MM" of the last start
        self.process: subprocess.Popen | None = None
        self._lock_file = None

    def _acquire(self) -> bool:
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock_file = open(self.lock_path, "w")
        try:
            fcntl.flock(self._lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except OSError:
            return False

    @property
    def running(self) -> bool:
        return self.process is not None and self.process.poll() is None

    def run(self) -> None:
        if not self._acquire():
            logger.info("scheduler: another process owns it, not starting here")
            return
        logger.info("scheduler: watching the saved schedule")
        while True:
            now = datetime.now()
            key = now.strftime("%Y-%m-%d %H:%M")
            if key != self.last_run and due_now(self.config.get_schedule(), now):
                if self.running:
                    logger.warning("scheduler: %s is due but the previous run is still going", key)
                else:
                    logger.info("scheduler: %s, starting the bot", key)
                    self.process = start_bot()
                self.last_run = key
            time.sleep(self.interval)


def start(app) -> Scheduler:
    """Attach a scheduler to the Flask app and start it."""
    scheduler = Scheduler(app.config["config_manager"], Path(app.config["files_dir"]) / "scheduler.lock")
    app.config["scheduler"] = scheduler
    scheduler.start()
    return scheduler
