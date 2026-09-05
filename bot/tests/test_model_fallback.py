"""When the first model is out of daily quota, the same call runs once on the fallback."""
import os
import sys
from types import SimpleNamespace

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(_ROOT, "bot"))

import agent  # noqa: E402

QUOTA = RuntimeError("429 RESOURCE_EXHAUSTED. Quota exceeded for metric: generate_content_free_tier_requests, limit: 20")


def _agent_with(monkeypatch, behaviour):
    """behaviour(model) -> parsed result or raises; records the models tried."""
    tried = []

    def fake_invoke(self, model, region):
        tried.append(model)
        out = behaviour(model)
        return {"parsed": SimpleNamespace(coalition_funds="לא", coalition_reason="", request_summary=out),
                "raw": SimpleNamespace(usage_metadata={"input_tokens": 5, "output_tokens": 2})}

    monkeypatch.setattr(agent.Agent, "_invoke", fake_invoke)
    return agent.Agent(set(), provider="google_genai", model="gemini-3.5-flash",
                       fallback_model="gemini-3.6-flash"), tried


def test_falls_back_on_quota_and_reports_the_model_used(monkeypatch):
    def behaviour(model):
        if model == "gemini-3.5-flash":
            raise QUOTA
        return "text from fallback"
    a, tried = _agent_with(monkeypatch, behaviour)
    coalition, reason, text, usage = a._analyze("עיקרי הפנייה ...")
    assert tried == ["gemini-3.5-flash", "gemini-3.6-flash"]
    assert text == "text from fallback"
    assert usage["model"] == "gemini-3.6-flash"


def test_no_fallback_call_when_the_first_model_works(monkeypatch):
    a, tried = _agent_with(monkeypatch, lambda model: "fine")
    _, _, text, usage = a._analyze("עיקרי הפנייה ...")
    assert tried == ["gemini-3.5-flash"] and text == "fine" and usage["model"] == "gemini-3.5-flash"


def test_other_errors_are_not_retried(monkeypatch):
    def behaviour(model):
        raise ValueError("bad response")
    a, tried = _agent_with(monkeypatch, behaviour)
    with pytest.raises(ValueError):
        a._analyze("עיקרי הפנייה ...")
    assert tried == ["gemini-3.5-flash"]


def test_quota_error_without_fallback_surfaces(monkeypatch):
    monkeypatch.setattr(agent.Agent, "_invoke", lambda self, m, r: (_ for _ in ()).throw(QUOTA))
    with pytest.raises(RuntimeError):
        agent.Agent(set(), model="gemini-3.5-flash")._analyze("עיקרי הפנייה ...")


def test_fallback_equal_to_the_main_model_is_ignored():
    assert agent.Agent(set(), model="m", fallback_model="m").fallback_model is None
