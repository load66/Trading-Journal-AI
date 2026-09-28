import json

import ai_analysis


class FakeRows:
    def __init__(self, rows):
        self.rows = rows

    def fetchall(self):
        return self.rows


class FakeConn:
    def execute(self, sql, params=()):
        if "FROM trades t" in sql:
            return FakeRows([
                {
                    "id": 1,
                    "trade_group": "g1",
                    "date": "2026-09-25",
                    "ticker": "SPY",
                    "side": "LONG",
                    "instrument_type": "OPTION",
                    "net_pnl": 125.0,
                    "gross_pnl": 130.0,
                    "commissions": 5.0,
                    "executions": json.dumps([
                        {"action": "BOT", "qty": 1, "price": 2.0, "timestamp_utc": "2026-09-25T13:45:00Z"},
                        {"action": "SOLD", "qty": 1, "price": 3.25, "timestamp_utc": "2026-09-25T14:15:00Z"},
                    ]),
                    "mfe_pct": 40.0,
                    "mae_pct": -8.0,
                    "exit_efficiency": 72.0,
                    "strategy": "VWAP Reclaim",
                    "r_multiple": 1.5,
                    "emotional_state": "disciplined",
                    "mistakes": None,
                    "stop_loss": 1.8,
                    "target_price": 3.5,
                    "risk_per_trade": 100.0,
                    "risk_reward": 2.0,
                    "entry_reason": "VWAP reclaimed",
                    "exit_reason": "trimmed into strength",
                    "ai_feedback": "Good patience.",
                    "idea_source": "Watchlist",
                },
                {
                    "id": 2,
                    "trade_group": "g2",
                    "date": "2026-09-25",
                    "ticker": "QQQ",
                    "side": "SHORT",
                    "instrument_type": "OPTION",
                    "net_pnl": -50.0,
                    "gross_pnl": -48.0,
                    "commissions": 2.0,
                    "executions": json.dumps([
                        {"action": "SOLD", "qty": 1, "price": 2.0, "timestamp_utc": "2026-09-25T17:45:00Z"},
                        {"action": "BOT", "qty": 1, "price": 2.5, "timestamp_utc": "2026-09-25T18:00:00Z"},
                    ]),
                    "mfe_pct": 5.0,
                    "mae_pct": -20.0,
                    "exit_efficiency": 10.0,
                    "strategy": "Range Break",
                    "r_multiple": -0.5,
                    "emotional_state": "frustrated",
                    "mistakes": "Late entry",
                    "stop_loss": None,
                    "target_price": None,
                    "risk_per_trade": None,
                    "risk_reward": None,
                    "entry_reason": "range break",
                    "exit_reason": "stopped",
                    "ai_feedback": "Wait for confirmation.",
                    "idea_source": "Scanner",
                },
            ])
        if "FROM diary_entries" in sql:
            return FakeRows([])
        if "FROM daily_summaries" in sql:
            return FakeRows([])
        raise AssertionError(sql)


def test_brain_context_is_question_aware_and_uses_full_journal_aggregates():
    raw = ai_analysis.build_brain_context(FakeConn(), account_id=4, question="How did I do on SPY?")
    context = json.loads(raw)

    assert context["overall"]["trades"] == 2
    assert context["overall"]["net_pnl"] == 75.0
    assert context["target_detection"]["tickers"] == ["SPY"]
    assert len(context["targeted_matches"]) == 1
    assert context["targeted_matches"][0]["ticker"] == "SPY"
    assert context["targeted_matches"][0]["strategy"] == "VWAP Reclaim"
    assert context["by_ticker"][0]["name"] == "SPY"
    assert context["management"]["mfe_coverage"]["pct"] == 100.0


def test_brain_prefers_groq_when_production_key_is_available(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "gsk-test")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(ai_analysis, "_groq_brain_response", lambda messages, context, key: "Journal-backed answer")

    answer = ai_analysis.generate_brain_response(
        [{"role": "user", "content": "What is my best ticker?"}],
        '{"overall":{"trades":2}}',
    )

    assert answer == "Journal-backed answer"


def test_brain_falls_back_to_anthropic_when_groq_fails(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "gsk-test")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "anthropic-test")
    monkeypatch.setattr(
        ai_analysis,
        "_groq_brain_response",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("rate limited")),
    )
    monkeypatch.setattr(ai_analysis, "_anthropic_brain_response", lambda *_args, **_kwargs: "Fallback answer")

    assert ai_analysis.generate_brain_response(
        [{"role": "user", "content": "Review my trading"}],
        "{}",
    ) == "Fallback answer"
