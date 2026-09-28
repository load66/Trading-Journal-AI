import json
from datetime import date

import ai_analysis


class FakeRows:
    def __init__(self, rows):
        self.rows = rows

    def fetchall(self):
        return self.rows


class FakeConn:
    def __init__(self):
        self.calls = []

    def execute(self, sql, params=()):
        self.calls.append((sql, tuple(params)))
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
    conn = FakeConn()
    raw = ai_analysis.build_brain_context(conn, account_id=4, question="How did I do on SPY?")
    context = json.loads(raw)

    assert context["overall"]["trades"] == 2
    assert context["overall"]["net_pnl"] == 75.0
    assert context["target_detection"]["tickers"] == ["SPY"]
    assert len(context["targeted_matches"]) == 1
    assert context["targeted_matches"][0]["ticker"] == "SPY"
    assert context["targeted_matches"][0]["strategy"] == "VWAP Reclaim"
    assert context["by_ticker"][0]["name"] == "SPY"
    assert context["management"]["mfe_coverage"]["pct"] == 100.0

    trade_sql, trade_params = next((sql, params) for sql, params in conn.calls if "FROM trades t" in sql)
    diary_sql, diary_params = next((sql, params) for sql, params in conn.calls if "FROM diary_entries" in sql)
    assert "t.account_id = ?" in trade_sql
    assert trade_params == (4,)
    assert "account_id = ?" in diary_sql
    assert diary_params == (4,)


def test_brain_all_accounts_avoids_ambiguous_null_parameters_for_postgres():
    conn = FakeConn()

    raw = ai_analysis.build_brain_context(
        conn,
        account_id=None,
        question="How's my trade last Friday?",
    )
    context = json.loads(raw)

    assert context["overall"]["trades"] == 2
    trade_sql, trade_params = next((sql, params) for sql, params in conn.calls if "FROM trades t" in sql)
    diary_sql, diary_params = next((sql, params) for sql, params in conn.calls if "FROM diary_entries" in sql)

    # PostgreSQL cannot infer the type of a NULL bind used only as "? IS NULL".
    # All-accounts mode therefore must omit the account predicate entirely.
    assert "IS NULL OR" not in trade_sql
    assert "account_id = ?" not in trade_sql
    assert trade_params == ()
    assert "IS NULL OR" not in diary_sql
    assert "account_id = ?" not in diary_sql
    assert diary_params == ()




def test_brain_resolves_last_friday_before_building_ai_context(monkeypatch):
    monkeypatch.setattr(ai_analysis, "_brain_today", lambda: date(2026, 9, 28))

    targets = ai_analysis._brain_extract_targets(
        "How's my trade last Friday?",
        [{"date": "2026-09-25", "ticker": "SPY", "strategy": None}],
    )

    assert targets["dates"] == ["2026-09-25"]
    assert targets["date_phrase"] == "last friday"


def test_brain_date_question_compacts_context_to_target_session(monkeypatch):
    monkeypatch.setattr(ai_analysis, "_brain_today", lambda: date(2026, 9, 28))
    conn = FakeConn()

    raw = ai_analysis.build_brain_context(
        conn,
        account_id=None,
        question="How's my trade last Friday?",
    )
    context = json.loads(raw)

    assert context["target_detection"]["dates"] == ["2026-09-25"]
    assert context["question_scope"]["mode"] == "targeted"
    assert context["question_scope"]["matched_trades"] == 2
    assert context["question_scope_stats"]["trades"] == 2
    assert {t["date"] for t in context["recent_trades"]} == {"2026-09-25"}
    assert len(raw) < 30000

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


def _le_context():
    return json.dumps({
        "journal_scope": {
            "completed_trades": 20,
            "date_range": ["2026-09-01", "2026-09-25"],
        },
        "question_scope_stats": {
            "trades": 20,
            "net_pnl": 450.0,
            "win_rate": 55.0,
            "profit_factor": 1.4,
        },
        "overall": {
            "trades": 20,
            "net_pnl": 450.0,
            "win_rate": 55.0,
            "profit_factor": 1.4,
            "date_from": "2026-09-01",
            "date_to": "2026-09-25",
        },
        "management": {
            "exit_efficiency_coverage": {"count": 10, "pct": 50.0},
            "avg_exit_efficiency": 61.0,
        },
        "le_playbook": {
            "name": "The LE Trading System — Guide Series",
            "version": "Complete collection, September 2026",
            "principles": ["No Flag, no Line, no Sign -> not worth your time."],
        },
        "le_compliance": {
            "summary": {
                "audited_trades": 8,
                "journal_completed_trades": 20,
                "audit_coverage_pct": 40.0,
                "classification_counts": {
                    "LE_VIOLATION": 4,
                    "INCOMPLETE_EVIDENCE": 3,
                    "LE_COMPLIANT": 1,
                },
                "rule_stats": [
                    {
                        "id": "not_chop_hour",
                        "label": "Not in Chop Hour?",
                        "pass": 5,
                        "fail": 3,
                        "unknown": 0,
                        "evaluated": 8,
                        "fail_net_pnl": -325.0,
                        "pass_net_pnl": 700.0,
                    },
                    {
                        "id": "trade_count_ok",
                        "label": "Trade Count OK?",
                        "pass": 6,
                        "fail": 2,
                        "unknown": 0,
                        "evaluated": 8,
                        "fail_net_pnl": -150.0,
                        "pass_net_pnl": 600.0,
                    },
                ],
            },
            "audited_trades": [
                {
                    "trade_group": "g8",
                    "date": "2026-09-25",
                    "ticker": "SPY",
                    "net_pnl": -125.0,
                    "classification": "LE_VIOLATION",
                    "failed_rule_ids": ["not_chop_hour"],
                    "unknown_rule_ids": ["flag_forming"],
                },
                {
                    "trade_group": "g7",
                    "date": "2026-09-24",
                    "ticker": "QQQ",
                    "net_pnl": 200.0,
                    "classification": "LE_COMPLIANT",
                    "failed_rule_ids": [],
                    "unknown_rule_ids": [],
                },
            ],
        },
    })


def test_brain_le_audit_is_deterministic_and_does_not_require_provider(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "gsk-test")
    monkeypatch.setattr(
        ai_analysis,
        "_groq_brain_response",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("provider should not be called")),
    )

    answer = ai_analysis.generate_brain_response(
        [{"role": "user", "content": "Audit my recent trades against the LE system"}],
        _le_context(),
    )

    assert "Recent LE audit" in answer
    assert "8/20 completed trades (40.0%)" in answer
    assert "Not in Chop Hour?" in answer
    assert "2026-09-25 SPY" in answer


def test_brain_le_rule_cost_answer_uses_negative_pnl_association_without_claiming_causation(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "gsk-test")

    answer = ai_analysis.generate_brain_response(
        [{"role": "user", "content": "Which LE rule is costing me the most money?"}],
        _le_context(),
    )

    assert "Not in Chop Hour?" in answer
    assert "$-325.00" in answer
    assert "descriptive association" in answer
    assert "proof" in answer


def test_brain_provider_context_has_hard_compact_budget():
    huge = json.dumps({
        "journal_scope": {"completed_trades": 500},
        "question_scope_stats": {"trades": 500, "net_pnl": 1234},
        "overall": {"trades": 500, "net_pnl": 1234},
        "recent_trades": [
            {"trade_group": f"g{i}", "notes": "x" * 5000, "net_pnl": i}
            for i in range(100)
        ],
        "recent_day_reviews": [
            {"date": "2026-09-25", "narrative": "y" * 10000}
            for _ in range(20)
        ],
    })

    compact = ai_analysis._brain_provider_context(
        huge,
        ai_analysis.BRAIN_PROVIDER_CONTEXT_CHARS,
    )

    assert len(compact) <= ai_analysis.BRAIN_PROVIDER_CONTEXT_CHARS
    parsed = json.loads(compact)
    assert parsed["overall"]["trades"] == 500


def test_groq_brain_retries_413_with_stricter_context(monkeypatch):
    calls = []

    class FakeResponse:
        def __init__(self, status_code, body=None):
            self.status_code = status_code
            self._body = body or {}

        def raise_for_status(self):
            if self.status_code >= 400:
                raise RuntimeError(f"http {self.status_code}")

        def json(self):
            return self._body

    def fake_post(_url, headers, json, timeout):
        calls.append(json)
        if len(calls) == 1:
            return FakeResponse(413)
        return FakeResponse(200, {
            "choices": [{"message": {"content": "Compact answer"}}]
        })

    monkeypatch.setattr(ai_analysis.httpx, "post", fake_post)

    long_context = json.dumps({
        "journal_scope": {"completed_trades": 200},
        "question_scope_stats": {"trades": 200},
        "overall": {"trades": 200},
        "recent_trades": [
            {"trade_group": f"g{i}", "notes": "z" * 4000}
            for i in range(80)
        ],
    })

    answer = ai_analysis._groq_brain_response(
        [{"role": "user", "content": "Review my trading"}],
        long_context,
        "gsk-test",
    )

    assert answer == "Compact answer"
    assert len(calls) == 2
    first = calls[0]["messages"][-1]["content"]
    second = calls[1]["messages"][-1]["content"]
    assert len(second) < len(first)
    assert "JOURNAL EVIDENCE" in second


def test_brain_provider_failure_returns_safe_journal_fallback_without_raw_provider_error(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "gsk-test")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(
        ai_analysis,
        "_groq_brain_response",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("413 Payload Too Large https://api.groq.com/private")),
    )

    answer = ai_analysis.generate_brain_response(
        [{"role": "user", "content": "Where am I losing the most money?"}],
        _le_context(),
    )

    assert "Journal analysis" in answer
    assert "413" not in answer
    assert "api.groq.com" not in answer


def _cohort_context():
    return json.dumps({
        "journal_scope": {"completed_trades": 191},
        "overall": {
            "trades": 191,
            "net_pnl": 4340.34,
            "win_rate": 52.9,
            "profit_factor": 1.36,
        },
        "le_diagnosis": {
            "cohort_glossary": [
                {
                    "id": "level_ema_snug",
                    "label": "Level break + EMA snug",
                    "definition": (
                        "The trade had a verified directional LE level break before entry and price was snug to the "
                        "last completed 10-minute 8 EMA at entry. In the current detector, snug means within 1.0% of that EMA; "
                        "more than 1.0% is treated as airgapped."
                    ),
                    "requirements": ["level_broken=pass", "ema_snug=pass"],
                    "aliases": ["level break + ema snug"],
                }
            ],
            "cohorts": [
                {
                    "id": "level_ema_snug",
                    "label": "Level break + EMA snug",
                    "trades": 70,
                    "wins": 44,
                    "losses": 26,
                    "win_rate": 62.9,
                    "net_pnl": 3662.38,
                    "avg_pnl": 52.32,
                    "profit_factor": 2.11,
                    "stable_sample": True,
                }
            ],
        },
        "le_compliance": {
            "summary": {
                "audited_trades": 191,
                "journal_completed_trades": 191,
                "audit_coverage_pct": 100.0,
                "classification_counts": {},
                "rule_stats": [],
            },
            "audited_trades": [],
        },
    })


def test_brain_recognizes_level_break_ema_snug_as_le_question():
    assert ai_analysis._brain_question_needs_le("What is Level break + EMA snug?") is True


def test_brain_answers_exact_level_break_ema_snug_question_without_provider(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "gsk-test")
    monkeypatch.setattr(
        ai_analysis,
        "_groq_brain_response",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("provider should not be called")),
    )

    answer = ai_analysis.generate_brain_response(
        [{"role": "user", "content": "What is Level break + EMA snug?"}],
        _cohort_context(),
    )

    assert "Level break + EMA snug" in answer
    assert "verified directional LE level break" in answer
    assert "within 1.0%" in answer
    assert "Trades: **70**" in answer
    assert "Win rate: **62.9%**" in answer
    assert "Net P&L: **$3,662.38**" in answer
    assert "Profit factor: **2.11**" in answer


def test_brain_provider_context_includes_le_cohort_glossary_and_stats():
    compact = ai_analysis._brain_provider_context(
        _cohort_context(),
        ai_analysis.BRAIN_PROVIDER_CONTEXT_CHARS,
    )
    parsed = json.loads(compact)

    assert parsed["le_diagnosis"]["cohort_glossary"][0]["id"] == "level_ema_snug"
    assert parsed["le_diagnosis"]["cohorts"][0]["trades"] == 70
