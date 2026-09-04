"""Provider switch: the API key and default model follow config's model_provider."""
import json
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "bot"))

from common.config_manager import ConfigManager  # noqa: E402
import agent  # noqa: E402


def _config(tmp_path, provider=None, model=""):
    p = tmp_path / "config.json"
    p.write_text(json.dumps({
        "model_name": model, "model_provider": provider, "mailing_list": [],
        "last_master_update": None, "last_master_filename": None,
        "schedule": [], "programs": [],
    }))
    return ConfigManager(str(p))


def test_gemini_provider_uses_gemini_key(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-openai")
    monkeypatch.setenv("GEMINI_API_KEY", "AIza-gemini")
    assert _config(tmp_path, provider="google_genai").get_api_key() == "AIza-gemini"


def test_openai_provider_uses_openai_key(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-openai")
    monkeypatch.setenv("GEMINI_API_KEY", "AIza-gemini")
    assert _config(tmp_path, provider="openai").get_api_key() == "sk-openai"
    assert _config(tmp_path, provider=None).get_api_key() == "sk-openai"  # default


def test_missing_gemini_key_is_none(tmp_path, monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    assert _config(tmp_path, provider="google_genai").get_api_key() is None


def test_agent_default_model_follows_provider():
    assert agent.Agent(set(), provider="google_genai").model == agent.DEFAULT_MODELS["google_genai"]
    assert agent.Agent(set(), provider="openai").model == agent.DEFAULT_MODELS["openai"]
    assert agent.Agent(set()).model == agent.DEFAULT_MODEL
    assert agent.Agent(set(), provider="google_genai", model="gemini-2.5-pro").model == "gemini-2.5-pro"


def test_agent_from_config_passes_provider_and_gemini_key(tmp_path, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "AIza-gemini")
    cfg = _config(tmp_path, provider="google_genai")
    a = agent.Agent.from_config(cfg, os.path.join(_ROOT, "files", "master.xlsx"))
    assert a.provider == "google_genai"
    assert a.api_key == "AIza-gemini"
    assert a.model == agent.DEFAULT_MODELS["google_genai"]
