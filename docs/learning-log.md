# Learning log: what was written in the code, and why

Every code change from here on is recorded here: the problem, the code that was written,
how it was tested, and what to learn from it. The full code is always in `git log -p`;
this file keeps only the pieces that explain the idea.

---

## 5 Sep 2026, night: four changes

### 1. The web pages call the API on the same port they were served from

**The problem.** On a Mac, port 5000 is taken by Apple's AirPlay Receiver. When the browser
asked for `127.0.0.1:5000`, macOS answered "403" before Flask could. We ran the site on
port 5001, but the pages kept calling 5000 because the address was hard-coded.

**The code.** `public/app.js`, function `apiRequest`, one line:

```diff
-  const response = await fetch("http://127.0.0.1:5000"+path, options);
+  const response = await fetch(path, options);
```

**What to learn.** `fetch("/api/v1/...")` without a full address goes to the server the
page came from. The code neither knows nor needs to know which port it runs on. A fixed
address in code is almost always a bug waiting to happen.

---

### 2. The scheduler: the site runs the bot at the saved time (issue 4)

**The problem.** The "agent" page saved a schedule to `config.json`, but nothing read it.
The bot ran only when someone typed a command. Amit asked: "use flask management, not
linux cron".

**The code.** A new file, `web/scheduler.py`. Its heart is a pure function, no clock and no
files, so it is easy to test:

```python
DAY_KEYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]   # datetime.weekday(): Monday is 0

def due_now(schedule, now) -> bool:
    """True when the current minute is an enabled day and time inside an active range."""
    today = now.date()
    hhmm = now.strftime("%H:%M")
    key = DAY_KEYS[now.weekday()]
    for rng in schedule or []:
        try:
            start = date.fromisoformat(str(rng["from"]))
            end = date.fromisoformat(str(rng["to"]))
        except (KeyError, TypeError, ValueError):
            continue                       # a broken range must not stop the scheduler
        if not start <= today <= end:
            continue
        day = (rng.get("days") or {}).get(key) or {}
        if day.get("enabled") and day.get("time") == hhmm:
            return True
    return False
```

And a thread that runs in the background inside the Flask process:

```python
class Scheduler(threading.Thread):
    def run(self):
        if not self._acquire():            # file lock: only one process runs the schedule
            return
        while True:
            now = datetime.now()
            key = now.strftime("%Y-%m-%d %H:%M")
            if key != self.last_run and due_now(self.config.get_schedule(), now):
                self.process = start_bot()  # subprocess: bot/main.py in its own process
                self.last_run = key         # at most once per matching minute
            time.sleep(self.interval)       # 15 seconds
```

Wired into the app with one line in `web/app.py`: `scheduler.start(app)`. A new route,
`GET /api/v1/schedule/status`, reports when the last run started and whether the bot is
still running.

**How it was tested.** Three tests in `bot/tests/test_scheduler.py` on `due_now`: fires on
the right day and minute, does not fire on another minute or a disabled day, does not fire
outside the range or on broken data. Then for real: a schedule for 02:02, and the bot
started by itself at 02:02 and sent its email at 02:09.

**What to learn.**
- Separate the decision ("is it time?") from the action ("run"). The decision is a pure
  function that takes `now` as a parameter, so it can be tested with any date without
  waiting for Saturday night.
- `subprocess.Popen` instead of calling `main()` directly: the bot runs for minutes and the
  site must keep answering meanwhile. A separate process also cannot crash the site.
- The lock (`fcntl.flock`) solves a real problem: `flask --debug` runs two processes, and
  without the lock there were two schedulers and two emails.
- An automatic run mails the real mailing list, with no `--to`. For tests, put yourself on
  the list.

---

### 3. Memory of handled letters: a daily run touches only what is new

**The problem.** The Knesset feed returns every pending letter (tonight: 12). Every run
read all of them again and made 12 Gemini calls, out of 20 a day on the free tier.

**The code.** `bot/main.py`. A memory file, `files/outputs/processed.json`, and two small
functions:

```python
def load_processed(path=PROCESSED_PATH) -> dict:
    """{slug: {"date": ..., "relevant": bool}} of letters already handled; {} if none."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}

def remember_processed(processed, slug, relevant, path=PROCESSED_PATH) -> None:
    processed[slug] = {"date": date.today().isoformat(), "relevant": relevant}
    path.write_text(json.dumps(processed, ensure_ascii=False, indent=1), encoding="utf-8")
```

And in the main loop:

```python
    processed = {} if args.all else load_processed()
    for url in urls:
        slug = _slug(url)
        if slug in processed:
            logger.info("%s: already handled on %s, skipped", slug, processed[slug]["date"])
            continue
        try:
            result = extractor.extract(url)
        except Exception:
            logger.warning("%s: skipped, could not extract (will retry next run)", slug)
            continue                        # a failure is not remembered, so it retries tomorrow
        remember_processed(processed, slug, result.relevant)
```

A new flag, `--all`, ignores the memory when everything should be read again.

**How it was tested.** Three tests in `bot/tests/test_main_cli.py`: the memory round-trips,
a broken file returns `{}` instead of crashing the run, and the `--all` flag parses.

**What to learn.**
- The memory is written **right after each letter**, not at the end of the run. If the run
  dies halfway, what was already done is not repeated.
- What is **not** remembered matters as much: a letter that failed (quota, 503) is not
  recorded, so it gets retried.
- A broken file must not stop the program. `except ... return {}` is the difference
  between "the bot did not run this morning" and "the bot re-read everything once".

---

### 4. Fallback model: when the first model is out of quota, the same call on a second one

**The problem.** On the free tier each model gets 20 calls a day. Tonight four letters
failed with `429 RESOURCE_EXHAUSTED`. Each model has its own quota, so a second model
means another 20.

**The code.** `bot/agent.py`. The call itself moved into a function that takes the model name:

```python
    def _invoke(self, model: str, region: str):
        llm = init_chat_model(model, **params).with_structured_output(_Analysis, include_raw=True)
        return llm.invoke([("system", self._ANALYSIS_SYSTEM), ("user", region)])

    @staticmethod
    def _is_quota_error(exc) -> bool:
        text = f"{type(exc).__name__}: {exc}"
        return "RESOURCE_EXHAUSTED" in text or "insufficient_quota" in text or " 429" in text
```

And `_analyze` tries the fallback **only** on a quota error:

```python
        try:
            result, model = self._invoke(self.model, region), self.model
        except Exception as exc:
            if not (self.fallback_model and self._is_quota_error(exc)):
                raise                       # any other error surfaces; nothing is hidden
            logger.warning("%s is out of quota, retrying on %s", self.model, self.fallback_model)
            result, model = self._invoke(self.fallback_model, region), self.fallback_model
        ...
        return (..., self._usage(result.get("raw"), model))   # the page shows the model that actually answered
```

The setting: `model_fallback` in `config.json` (`common/config_manager.py`:
`get_model_fallback` / `set_model_fallback`), read and written through `/api/v1/llm`.
Current values: main `gemini-3.5-flash`, fallback `gemini-3.6-flash`.

**How it was tested.** Five tests in `bot/tests/test_model_fallback.py`, no network: `_invoke`
is replaced by a fake that records which models were tried. Quota error: two attempts and
the second model is reported. Success: one attempt. Another error: no retry. Quota with no
fallback: the error surfaces. Fallback equal to the main model: ignored.

**What to learn.**
- Pulling out "the thing we talk to" (`_invoke`) into its own function is what makes both
  the fallback and the offline tests possible: the test replaces only that.
- Retry **only** on the error you understand. Retrying on every error would hide real bugs.
- The page shows the model that actually answered, not the one that was configured. A
  small truth that saves a lot of confusion.

---

## How to read it all yourself

```bash
git log --oneline -10            # what was done, one line per commit
git show <sha>                   # one commit with its code
git log -p -- bot/agent.py       # the history of one file, with the code
OPENAI_API_KEY="" GEMINI_API_KEY="" uv run pytest -q   # the tests, without a model
```
