""".env is resolved once, in common.config_manager, and gmail_auth writes to that same file."""
import importlib
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "bot"))


def test_env_path_is_absolute_and_points_at_a_dotenv_file():
    import common.config_manager as config_manager
    assert config_manager.ENV_PATH.is_absolute()
    assert config_manager.ENV_PATH.name == ".env"


def test_gmail_auth_writes_to_the_same_env_file_the_app_reads():
    import common.config_manager as config_manager
    import gmail_auth
    assert gmail_auth.ENV_PATH is config_manager.ENV_PATH


def test_values_come_from_the_dotenv_file_that_python_dotenv_finds(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("MONI_TEST_MARKER=found-by-dotenv\n", encoding="utf-8")
    monkeypatch.delenv("MONI_TEST_MARKER", raising=False)
    monkeypatch.setattr("dotenv.find_dotenv", lambda *a, **k: str(env))
    import common.config_manager as config_manager
    importlib.reload(config_manager)
    try:
        assert config_manager.ENV_PATH == env
        assert os.environ.get("MONI_TEST_MARKER") == "found-by-dotenv"
    finally:
        monkeypatch.undo()
        importlib.reload(config_manager)   # back to the real .env for the other tests
