"""Tests never call a paid model unless asked.

By default the model keys are blanked before any test module loads, so the golden tests
(the only ones that talk to Gemini / OpenAI) skip themselves and `uv run pytest` is always
free. To run them on purpose, once, set MONI_LIVE_TESTS=1:

    MONI_LIVE_TESTS=1 uv run pytest -q      # about seven model calls

Blanking here works because python-dotenv never overrides a variable that already exists
in the environment, so the .env keys stay untouched on disk and unread by the tests.
"""
import os

if os.environ.get("MONI_LIVE_TESTS") != "1":
    os.environ["OPENAI_API_KEY"] = ""
    os.environ["GEMINI_API_KEY"] = ""
