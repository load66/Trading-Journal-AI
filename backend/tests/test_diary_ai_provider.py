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


def diary_payload():
    return {
        "diary_date": "2026-09-27",
        "overall_summary": "Stayed selective.",
        "patterns_identified": [],
        "improvement_areas": [],
        "trade_analyses": [],
    }


def test_diary_image_prefers_groq_vision(monkeypatch, tmp_path):
    captured = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        captured.update(url=url, headers=headers, payload=json, timeout=timeout)
        return FakeResponse({
            "choices": [{"message": {"content": __import__("json").dumps(diary_payload())}}]
        })

    image_path = tmp_path / "diary.png"
    image_path.write_bytes(b"fake-png")

    monkeypatch.setenv("GROQ_API_KEY", "gsk-test")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(ai_analysis.httpx, "post", fake_post)

    result = ai_analysis.analyze_diary_entry(str(image_path), "2026-09-27", [])

    assert result["ai_provider"] == "groq"
    assert result["ai_model"] == "qwen/qwen3.8-27b"
    assert captured["payload"]["model"] == "qwen/qwen3.8-27b"
    assert captured["payload"]["response_format"] == {"type": "json_object"}
    content = captured["payload"]["messages"][1]["content"]
    image_part = next(part for part in content if part["type"] == "image_url")
    assert image_part["image_url"]["url"].startswith("data:image/png;base64,")


def test_diary_text_prefers_groq(monkeypatch):
    captured = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        captured.update(url=url, headers=headers, payload=json, timeout=timeout)
        return FakeResponse({
            "choices": [{"message": {"content": __import__("json").dumps(diary_payload())}}]
        })

    monkeypatch.setenv("GROQ_API_KEY", "gsk-test")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(ai_analysis.httpx, "post", fake_post)

    result = ai_analysis.analyze_diary_text("SPY call. Good entry.", "2026-09-27", [])

    assert result["ai_provider"] == "groq"
    assert result["ai_model"] == "openai/gpt-oss-120b"
    assert captured["payload"]["model"] == "openai/gpt-oss-120b"
    assert captured["payload"]["response_format"] == {"type": "json_object"}


def test_diary_groq_failure_is_not_mislabeled_as_missing_anthropic(monkeypatch, tmp_path):
    image_path = tmp_path / "diary.png"
    image_path.write_bytes(b"fake-png")

    monkeypatch.setenv("GROQ_API_KEY", "gsk-test")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(
        ai_analysis,
        "_groq_diary_json",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("invalid credentials")),
    )

    with pytest.raises(RuntimeError, match="Groq diary image analysis failed"):
        ai_analysis.analyze_diary_entry(str(image_path), "2026-09-27", [])


def test_diary_requires_one_provider(monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    with pytest.raises(ValueError, match="GROQ_API_KEY"):
        ai_analysis.analyze_diary_text("notes", "2026-09-27", [])
