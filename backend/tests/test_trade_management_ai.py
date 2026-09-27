import json

import pytest

import trade_management_ai as tm


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._payload


def evidence():
    return tm.build_management_evidence(
        range_key="7D",
        date_from="2026-09-19",
        date_to="2026-09-25",
        account_type="day_trading",
        kpis={
            "total_trades": 47,
            "total_net_pnl": 500.0,
            "exit_efficiency": 65.0,
            "capture_n": 27,
            "capture_winner_total": 27,
            "capture_coverage_pct": 100.0,
            "capture_confidence": "RELIABLE",
            "avg_mfe": 24.29,
            "avg_mae": 12.56,
            "median_mfe": 15.0,
            "median_mae": 8.0,
            "winner_median_mfe": 20.0,
            "loser_median_mfe": 4.0,
            "winner_median_mae": 6.8,
            "loser_median_mae": 15.7,
            "loser_mfe_le_5_pct": 55.0,
            "loser_mfe_le_10_pct": 70.0,
            "loser_mae_ge_25_pct": 20.0,
            "winner_mae_le_20_pct": 90.0,
            "excursion_n": 47,
            "management_coverage_pct": 100.0,
            "excursion_confidence": "RELIABLE",
        },
        edge={
            "hold_time": {
                "winners_avg_min": 22.5,
                "losers_avg_min": 8.8,
                "winners_median_min": 14.0,
                "losers_median_min": 8.5,
                "winner_count": 27,
                "loser_count": 19,
                "sample_count": 46,
                "coverage_pct": 97.9,
                "overnight_excluded_count": 1,
                "overnight_excluded": [
                    {
                        "trade_group": "u-overnight",
                        "ticker": "U",
                        "hold_minutes": 1220.0,
                        "net_pnl": -96.06,
                    }
                ],
            }
        },
        goals={"exit_efficiency": 50.0},
    )


def model_payload():
    return {
        "diagnosis": (
            "Profit capture is above goal and same-session losers are held for less time "
            "than winners. Risk evidence points to early failure as the clearest area to monitor."
        ),
        "strongest_behavior": "Covered winners retain a solid share of favorable movement.",
        "primary_improvement": "Review early adverse expansion on losing trades.",
        "next_focus": "Track failed trades that cannot produce +5% favorable progress.",
        "headline": "This should be overwritten by the evidence lock.",
    }


def test_management_evidence_uses_corrected_day_trade_holding_sample():
    data = evidence()

    assert data["holding"]["loser_avg_min"] == 8.8
    assert data["holding"]["loser_median_min"] == 8.5
    assert data["holding"]["overnight_excluded_count"] == 1
    assert data["holding"]["overnight_excluded"][0]["ticker"] == "U"
    assert data["holding"]["signal"] == "no_loser_hold_leak"
    assert data["risk"]["signal"] == "early_failure_pattern"
    assert data["deterministic_priority"] == "entry_quality_early_invalidation"


def test_management_signature_changes_when_verified_evidence_changes():
    first = evidence()
    sig = tm.management_context_signature(first)

    changed = evidence()
    changed["capture"]["exit_efficiency_pct"] = 58.0

    assert tm.management_context_signature(changed) != sig


def test_trade_management_ai_prefers_groq_and_locks_headline(monkeypatch):
    seen = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        seen.update(url=url, headers=headers, payload=json, timeout=timeout)
        return FakeResponse({
            "choices": [{"message": {"content": __import__("json").dumps(model_payload())}}]
        })

    monkeypatch.setenv("GROQ_API_KEY", "gsk-test")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "anthropic-test")
    monkeypatch.setattr(tm.httpx, "post", fake_post)

    result = tm.generate_trade_management_analysis(evidence())

    assert result["ai_provider"] == "groq"
    assert result["ai_model"] == "openai/gpt-oss-120b"
    assert result["focus_area"] == "entry_quality_early_invalidation"
    assert result["headline"] == "Early invalidation is the clearest improvement candidate."
    assert result["evidence_locked"] is True
    assert result["evidence_version"] == 1
    assert seen["payload"]["response_format"] == {"type": "json_object"}
    assert seen["headers"]["Authorization"] == "Bearer gsk-test"
    prompt = seen["payload"]["messages"][1]["content"]
    assert '"overnight_excluded_count": 1' in prompt
    assert '"loser_avg_min": 8.8' in prompt


def test_trade_management_ai_falls_back_to_anthropic(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "gsk-test")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "anthropic-test")

    monkeypatch.setattr(
        tm,
        "_groq_management_analysis",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("rate limited")),
    )
    monkeypatch.setattr(tm, "_anthropic_management_analysis", lambda _text: model_payload())

    result = tm.generate_trade_management_analysis(evidence())
    assert result["ai_provider"] == "anthropic"
    assert result["focus_area"] == "entry_quality_early_invalidation"
    assert result["headline"] == "Early invalidation is the clearest improvement candidate."


def test_trade_management_ai_requires_provider(monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    with pytest.raises(ValueError, match="GROQ_API_KEY"):
        tm.generate_trade_management_analysis(evidence())


def test_mixed_holding_evidence_cannot_be_promoted_to_hold_leak():
    data = tm.build_management_evidence(
        range_key="30D",
        date_from="2026-08-27",
        date_to="2026-09-25",
        account_type="day_trading",
        kpis={
            "total_trades": 20,
            "capture_confidence": "LOW",
            "excursion_confidence": "LOW",
        },
        edge={
            "hold_time": {
                "winners_avg_min": 10.0,
                "losers_avg_min": 20.0,
                "winners_median_min": 12.0,
                "losers_median_min": 7.0,
                "winner_count": 10,
                "loser_count": 10,
                "sample_count": 20,
                "coverage_pct": 100.0,
            }
        },
        goals={"exit_efficiency": 50.0},
    )

    assert data["holding"]["signal"] == "mixed"
    assert data["deterministic_priority"] == "mixed_evidence"
