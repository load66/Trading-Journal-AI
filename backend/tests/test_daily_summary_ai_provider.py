import json

import pytest

import daily_summary


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._payload


def context():
    return {
        "date": "2026-09-26",
        "trades": [
            {
                "trade_group": "g1",
                "ticker": "SPY",
                "side": "LONG",
                "net_pnl": 125.0,
                "strategy": None,
                "r_multiple": None,
                "stop_loss": None,
                "risk_per_trade": None,
                "entry_reason": None,
                "exit_reason": None,
                "mistakes": None,
                "emotional_state": None,
                "executions": [{"time": "09:45:00"}],
            }
        ],
        "day_kpis": {
            "total_net_pnl": 125.0,
            "total_trades": 1,
            "winning_trades": 1,
            "losing_trades": 0,
            "win_rate": 100.0,
            "avg_win": 125.0,
            "avg_loss": 0,
            "profit_factor": None,
        },
        "alltime_kpis": {
            "win_rate": 55.0,
            "avg_win": 100.0,
            "avg_loss": -80.0,
            "profit_factor": 1.4,
        },
        "diary_summary": None,
    }


def result_payload():
    return {
        "narrative": "SPY was the only trade.",
        "mental_game": "Insufficient diary evidence.",
        "strengths": ["Positive execution"],
        "mistakes": [],
        "coaching": ["Keep risk defined"],
        "trade_grades": [
            {"trade_group": "g1", "ticker": "SPY", "grade": "B", "one_line": "Positive result."}
        ],
        "overall_grade": "B",
        "tomorrow_focus": ["Repeat the process"],
        "patterns": [],
    }


def test_daily_summary_prefers_groq(monkeypatch):
    seen = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        seen.update(url=url, headers=headers, payload=json, timeout=timeout)
        return FakeResponse({
            "choices": [{"message": {"content": __import__("json").dumps(result_payload())}}]
        })

    monkeypatch.setenv("GROQ_API_KEY", "gsk-test")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "anthropic-test")
    monkeypatch.setattr(daily_summary.httpx, "post", fake_post)

    result = daily_summary.generate_daily_summary(context())

    assert result["ai_provider"] == "groq"
    assert result["ai_model"] == "openai/gpt-oss-120b"
    assert result["overall_grade"] == "N/A"
    assert result["trade_grades"][0]["grade"] == "N/A"
    assert result["mental_game"].startswith("Insufficient evidence")
    assert result["evidence_locked"] is True
    assert result["evidence_version"] == 3
    assert seen["payload"]["model"] == "openai/gpt-oss-120b"
    assert seen["payload"]["response_format"] == {"type": "json_object"}
    assert seen["headers"]["Authorization"] == "Bearer gsk-test"


def test_daily_summary_falls_back_to_anthropic(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "gsk-test")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "anthropic-test")

    monkeypatch.setattr(
        daily_summary,
        "_groq_daily_summary",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("rate limited")),
    )
    monkeypatch.setattr(daily_summary, "_anthropic_daily_summary", lambda _text: result_payload())

    result = daily_summary.generate_daily_summary(context())
    assert result["ai_provider"] == "anthropic"
    assert result["overall_grade"] == "N/A"
    assert result["trade_grades"][0]["grade"] == "N/A"


def test_daily_summary_requires_one_provider(monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    with pytest.raises(ValueError, match="GROQ_API_KEY"):
        daily_summary.generate_daily_summary(context())


def test_daily_summary_groq_failure_is_not_mislabeled_as_missing_anthropic(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "gsk-test")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(
        daily_summary,
        "_groq_daily_summary",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("invalid credentials")),
    )

    with pytest.raises(RuntimeError, match="Groq daily coaching failed"):
        daily_summary.generate_daily_summary(context())
