import json

import pytest

import ai_analysis


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._payload


def _diagnosis():
    return {
        "headline": "Test diagnosis",
        "edge": {"where_it_lives": [], "where_it_dies": []},
        "two_traders": {"disciplined": "", "destructive": ""},
        "top_flaws": [],
        "daily_stop": {"recommended_candidate": None, "reason": ""},
        "action_plan": [],
        "limitations": [],
    }


def test_groq_is_primary_provider(monkeypatch):
    captured = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        captured.update(url=url, headers=headers, payload=json, timeout=timeout)
        return FakeResponse({
            "choices": [{"message": {"content": json_module.dumps(_diagnosis())}}]
        })

    json_module = __import__("json")
    monkeypatch.setenv("GROQ_API_KEY", "gsk-test")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "anthropic-test")
    monkeypatch.setattr(ai_analysis.httpx, "post", fake_post)

    result = ai_analysis.generate_performance_diagnosis({"meta": {"trade_count": 10}})

    assert result["provider"] == "groq"
    assert result["model"] == "openai/gpt-oss-120b"
    assert result["diagnosis"]["headline"] == "Test diagnosis"
    assert captured["url"] == "https://api.groq.com/openai/v1/chat/completions"
    assert captured["headers"]["Authorization"] == "Bearer gsk-test"
    assert captured["payload"]["reasoning_effort"] == "medium"
    assert captured["payload"]["response_format"] == {"type": "json_object"}
    assert captured["payload"]["model"] == "openai/gpt-oss-120b"
    assert "source-of-truth" in captured["payload"]["messages"][0]["content"]


def test_groq_failure_falls_back_to_anthropic(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "gsk-test")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "anthropic-test")

    def fail_groq(*args, **kwargs):
        raise RuntimeError("rate limited")

    monkeypatch.setattr(ai_analysis, "_groq_performance_diagnosis", fail_groq)
    monkeypatch.setattr(
        ai_analysis,
        "_anthropic_performance_diagnosis",
        lambda report, key: _diagnosis(),
    )

    result = ai_analysis.generate_performance_diagnosis({"meta": {"trade_count": 10}})
    assert result["provider"] == "anthropic"
    assert result["diagnosis"]["headline"] == "Test diagnosis"


def test_no_provider_is_reported_as_unconfigured(monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    assert ai_analysis.performance_ai_is_configured() is False
    with pytest.raises(ValueError, match="GROQ_API_KEY"):
        ai_analysis.generate_performance_diagnosis({"meta": {}})


def test_json_fence_cleanup():
    raw = "~~~json\n" + json.dumps(_diagnosis()) + "\n~~~"
    parsed = json.loads(ai_analysis._strip_json_fence(raw))
    assert parsed["headline"] == "Test diagnosis"
