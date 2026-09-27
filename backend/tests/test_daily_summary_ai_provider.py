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
    assert result["overall_grade"] == "B"
    assert result["trade_grades"][0]["grade"] == "B"
    assert result["mental_game"] == "Insufficient diary evidence."
    assert result["diagnostic_mode"] == "unfiltered"
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
    assert result["overall_grade"] == "B"
    assert result["trade_grades"][0]["grade"] == "B"
    assert result["diagnostic_mode"] == "unfiltered"


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

def test_daily_context_signature_changes_when_day_evidence_changes():
    base = context()
    first = daily_summary.daily_context_signature(base)

    changed = context()
    changed["trades"][0]["stop_loss"] = 499.5
    assert daily_summary.daily_context_signature(changed) != first

    diary_changed = context()
    diary_changed["diary_summary"] = {
        "overall_summary": "Waited for the planned setup.",
        "patterns_identified": [],
        "improvement_areas": [],
    }
    assert daily_summary.daily_context_signature(diary_changed) != first


def test_daily_context_signature_ignores_unrelated_all_time_benchmark_changes():
    base = context()
    first = daily_summary.daily_context_signature(base)

    later_history = context()
    later_history["alltime_kpis"]["win_rate"] = 61.2
    later_history["alltime_kpis"]["profit_factor"] = 1.9

    assert daily_summary.daily_context_signature(later_history) == first

def test_daily_cache_matches_identical_evidence_even_after_version_bump():
    content = {
        "input_signature": "same-day-evidence",
        "evidence_version": 1,
        "analytics_engine_version": "old-engine",
    }
    assert daily_summary.daily_cache_matches(content, "same-day-evidence") is True


def test_daily_cache_invalidates_when_day_evidence_changes():
    content = {
        "input_signature": "old-day-evidence",
        "evidence_version": 999,
        "analytics_engine_version": "new-engine",
    }
    assert daily_summary.daily_cache_matches(content, "new-day-evidence") is False


def test_daily_cache_does_not_trust_legacy_summary_without_signature():
    content = {
        "evidence_version": 4,
        "analytics_engine_version": "current",
    }
    assert daily_summary.daily_cache_matches(content, "current-day-evidence") is False

def test_day_review_preserves_ai_wmt_mistake_without_evidence_gate(monkeypatch):
    ctx = context()
    ctx["trades"][0]["ticker"] = "WMT"
    ctx["behavior_flags"] = [
        {
            "code": "averaging_down",
            "title": "Added against the position",
            "detail": "WMT added after price moved against the running average entry.",
            "evidence": "VERIFIED",
            "severity": "high",
            "trade_group": "g1",
            "ticker": "WMT",
            "observed_pnl": -58.0,
        }
    ]
    ctx["verified_strengths"] = []
    ctx["recorded_observations"] = []

    payload = result_payload()
    payload["mental_game"] = "The trader became less selective later in the session."
    payload["mistakes"] = [
        "WMT was added to while the position was moving against the entry."
    ]
    payload["trade_grades"] = [
        {
            "trade_group": "g1",
            "ticker": "WMT",
            "grade": "D",
            "one_line": "Adding against the position degraded the trade management.",
        }
    ]
    payload["overall_grade"] = "C"

    monkeypatch.setenv("GROQ_API_KEY", "gsk-test")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(daily_summary, "_groq_daily_summary", lambda *_args, **_kwargs: payload)

    result = daily_summary.generate_daily_summary(ctx)

    assert result["mistakes"] == payload["mistakes"]
    assert result["mental_game"] == payload["mental_game"]
    assert result["trade_grades"] == payload["trade_grades"]
    assert result["overall_grade"] == "C"
    assert result["diagnostic_mode"] == "unfiltered"
    assert result["behavior_flags"] == ctx["behavior_flags"]


def test_day_review_does_not_filter_unanchored_ai_interpretation(monkeypatch):
    ctx = context()
    ctx["behavior_flags"] = []
    ctx["verified_strengths"] = []
    ctx["recorded_observations"] = []

    payload = result_payload()
    payload["mental_game"] = "The trader appeared impatient after the first loss."
    payload["mistakes"] = ["SPY looked like an impulsive re-entry."]
    payload["overall_grade"] = "C+"

    monkeypatch.setenv("GROQ_API_KEY", "gsk-test")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(daily_summary, "_groq_daily_summary", lambda *_args, **_kwargs: payload)

    result = daily_summary.generate_daily_summary(ctx)

    assert result["mental_game"] == payload["mental_game"]
    assert result["mistakes"] == payload["mistakes"]
    assert result["overall_grade"] == "C+"
    assert result["diagnostic_mode"] == "unfiltered"

def test_day_review_normalizes_only_verbatim_highlights():
    payload = {
        "narrative": "QCOM showed patient execution. WMT was averaged down into weakness.",
        "mental_game": "Discipline faded late in the session.",
        "highlights": {
            "good": [
                "QCOM showed patient execution",
                "not actually in the prose",
                "QCOM showed patient execution",
            ],
            "bad": [
                "WMT was averaged down into weakness",
                "Discipline faded late in the session",
                "QCOM showed patient execution",
            ],
        },
    }

    assert daily_summary._normalize_highlights(payload) == {
        "good": ["QCOM showed patient execution"],
        "bad": [
            "WMT was averaged down into weakness",
            "Discipline faded late in the session",
        ],
    }


def test_day_review_preserves_structured_highlights(monkeypatch):
    ctx = context()
    payload = result_payload()
    payload["narrative"] = (
        "SPY execution stayed controlled early, but the later re-entry was impulsive."
    )
    payload["mental_game"] = "Patience weakened late in the session."
    payload["highlights"] = {
        "good": ["SPY execution stayed controlled early"],
        "bad": [
            "later re-entry was impulsive",
            "Patience weakened late in the session",
        ],
    }

    monkeypatch.setenv("GROQ_API_KEY", "gsk-test")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(daily_summary, "_groq_daily_summary", lambda *_args, **_kwargs: payload)

    result = daily_summary.generate_daily_summary(ctx)

    assert result["highlights"] == payload["highlights"]
    assert result["diagnostic_mode"] == "unfiltered"



def test_daily_auto_generation_limit_reuses_same_local_day():
    from datetime import datetime, timezone

    now = datetime(2026, 9, 27, 20, 0, tzinfo=timezone.utc)
    assert daily_summary.daily_auto_generation_is_fresh(
        "2026-09-27 15:20:00",
        now=now,
    ) is True


def test_daily_auto_generation_limit_allows_next_local_day():
    from datetime import datetime, timezone

    now = datetime(2026, 9, 27, 15, 0, tzinfo=timezone.utc)
    assert daily_summary.daily_auto_generation_is_fresh(
        "2026-09-27 04:30:00",
        now=now,
    ) is False


def test_daily_auto_generation_limit_accepts_timezone_aware_timestamp():
    from datetime import datetime, timezone

    now = datetime(2026, 9, 27, 15, 0, tzinfo=timezone.utc)
    assert daily_summary.daily_auto_generation_is_fresh(
        "2026-09-27T09:45:00-05:00",
        now=now,
    ) is True
