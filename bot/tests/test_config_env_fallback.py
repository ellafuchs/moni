"""Secrets live ONLY in the environment (.env). config.json holds non-secret data.

OPENAI_API_KEY -> api key, MONI_SENDER -> sending address, GOOGLE_* -> Gmail OAuth.
Any secret found in config.json is ignored and dropped on the next save.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from common.config_manager import ConfigManager  # noqa: E402

SECRET_KEYS = {"api_key", "notifier"}


def _write_config(path, extra=None):
    body = {
        "model_name": "",
        "model_provider": None,
        "mailing_list": ["a@example.com"],
        "last_master_update": None,
        "last_master_filename": None,
        "schedule": [],
        "programs": [],
    }
    body.update(extra or {})
    path.write_text(json.dumps(body))
    return str(path)


def test_api_key_comes_from_env(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-from-env")
    config = ConfigManager(_write_config(tmp_path / "config.json"))
    assert config.get_api_key() == "sk-from-env"


def test_api_key_none_when_env_missing(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    config = ConfigManager(_write_config(tmp_path / "config.json"))
    assert config.get_api_key() is None


def test_notifier_comes_from_env(tmp_path, monkeypatch):
    monkeypatch.setenv("MONI_SENDER", "env@example.com")
    config = ConfigManager(_write_config(tmp_path / "config.json"))
    assert config.get_notifier_email() == "env@example.com"


def test_notifier_none_when_env_missing(tmp_path, monkeypatch):
    monkeypatch.delenv("MONI_SENDER", raising=False)
    config = ConfigManager(_write_config(tmp_path / "config.json"))
    assert config.get_notifier_email() is None


def test_secrets_in_config_are_ignored_and_dropped(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-from-env")
    monkeypatch.setenv("MONI_SENDER", "env@example.com")
    path = _write_config(tmp_path / "config.json", extra={
        "api_key": "sk-from-config",
        "notifier": {"email": "cfg@example.com", "password": "cfg-pass"},
    })
    config = ConfigManager(path)
    assert config.get_api_key() == "sk-from-env"
    assert config.get_notifier_email() == "env@example.com"
    config.set_model_name("gpt-4o")  # triggers _save
    saved = json.loads(open(path).read())
    assert not (SECRET_KEYS & saved.keys())
    assert saved["mailing_list"] == ["a@example.com"]  # non-secret data preserved


def test_config_without_notifier_key_loads(tmp_path):
    # A config.json written by the new code has no "notifier" block at all.
    config = ConfigManager(_write_config(tmp_path / "config.json"))
    assert config.get_mailing_list() == ["a@example.com"]
