"""gmail_auth lets python-dotenv find and load the .env file instead of guessing its path."""
import importlib
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "bot"))


def test_env_path_is_absolute_and_points_at_a_dotenv_file():
    import gmail_auth
    assert gmail_auth.ENV_PATH.is_absolute()
    assert gmail_auth.ENV_PATH.name == ".env"


def test_values_come_from_the_dotenv_file_that_python_dotenv_finds(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("MONI_TEST_MARKER=found-by-dotenv\n", encoding="utf-8")
    monkeypatch.delenv("MONI_TEST_MARKER", raising=False)
    monkeypatch.setattr("dotenv.find_dotenv", lambda *a, **k: str(env))
    import gmail_auth
    importlib.reload(gmail_auth)
    assert gmail_auth.ENV_PATH == env
    assert os.environ.get("MONI_TEST_MARKER") == "found-by-dotenv"
