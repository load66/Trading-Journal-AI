import csv
import io
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))


def _report():
    return {
        "id": 7,
        "title": '<September & "YTD">',
        "date_from": "2026-09-01",
        "date_to": "2026-09-30",
        "generated_at": "2026-09-26T12:00:00-05:00",
        "report_version": "1",
        "analytics_engine_version": "2026.09.26.1",
        "behavior_version": "2026.09.26.1",
        "analysis_provider": "openai",
        "analysis_model": "gpt-test",
        "data_fingerprint": "abc123",
        "is_stale": False,
        "source_metrics": {
            "scoreboard": {
                "net_pnl": 50.0,
                "gross_pnl": 75.0,
                "fees": 25.0,
                "win_rate": 50.0,
                "profit_factor": 2.0,
                "avg_winner": 100.0,
                "avg_loser": 50.0,
                "reward_risk": 2.0,
                "max_drawdown": -50.0,
                "active_days": 2,
                "best_day": {"date": "2026-09-21", "pnl": 100.0},
                "worst_day": {"date": "2026-09-22", "pnl": -50.0},
            },
            "two_traders": {
                "disciplined": {"trade_count": 1, "total_pnl": 100.0, "win_rate": 100.0, "avg_pnl": 100.0},
                "destructive": {"trade_count": 1, "total_pnl": -50.0, "win_rate": 0.0, "avg_pnl": -50.0},
            },
            "hold_time": [{"bucket": "5-10min", "trade_count": 2, "total_pnl": 50.0, "win_rate": 50.0, "avg_pnl": 25.0}],
            "trade_ledger": [
                {
                    "trade_group": "a",
                    "date": "2026-09-21",
                    "ticker": "=SPY",
                    "instrument_type": "OPTION",
                    "side": "LONG",
                    "entry_time": "2026-09-21 09:30:00",
                    "exit_time": "2026-09-21 09:36:00",
                    "hold_sec": 360.0,
                    "entry_size": 2.0,
                    "gross_pnl": 125.0,
                    "commissions": 25.0,
                    "net_pnl": 100.0,
                    "hold_bucket": "5-10min",
                    "size_bucket": "1-3",
                },
                {
                    "trade_group": "b",
                    "date": "2026-09-22",
                    "ticker": "QQQ",
                    "instrument_type": "OPTION",
                    "side": "LONG",
                    "entry_time": "2026-09-22 10:00:00",
                    "exit_time": "2026-09-22 10:10:00",
                    "hold_sec": 600.0,
                    "entry_size": 5.0,
                    "gross_pnl": -50.0,
                    "commissions": 0.0,
                    "net_pnl": -50.0,
                    "hold_bucket": "10-15min",
                    "size_bucket": "4-5",
                },
            ],
        },
        "diagnosis": {
            "headline": '<script>alert("x")</script> Patience is the edge.',
            "edge": {
                "where_it_lives": ["5-10 minute holds"],
                "where_it_dies": ["Averaging down"],
            },
            "limitations": ["Sample is limited."],
        },
        "action_plan": [
            {"priority": 1, "rule": "No averaging down", "why": "Observed negative cohort"}
        ],
    }


def test_html_export_is_standalone_escaped_and_data_first():
    from smoking_gun_exports import render_saved_report_html

    html = render_saved_report_html(_report())

    assert "&lt;September &amp; &quot;YTD&quot;&gt;" in html
    assert '<script>alert("x")</script>' not in html
    assert "&lt;script&gt;alert(&quot;x&quot;)&lt;/script&gt;" in html
    assert "Executive Scoreboard" in html
    assert "DATA" in html
    assert "DIAGNOSIS" in html
    assert "FIX" in html
    assert "Behavioral Cohort Split" in html
    assert "Rule-aligned cohort" in html
    assert "Comparison cohort" in html
    assert "Evidence Standards" in html
    assert "Association, not causation" in html
    assert "Thin sample" in html
    assert "plotly" not in html.lower()
    assert "https://" not in html.lower()
    assert html.index("Executive Scoreboard") < html.index("DIAGNOSIS")


def test_csv_export_has_exact_header_reconciles_and_blocks_formula_injection():
    from smoking_gun_exports import render_trade_ledger_csv

    csv_text = render_trade_ledger_csv(_report())
    rows = list(csv.DictReader(io.StringIO(csv_text)))

    assert csv_text.splitlines()[0] == (
        "trade_group,date,ticker,instrument_type,side,entry_time,exit_time,"
        "hold_sec,entry_size,gross_pnl,commissions,net_pnl,hold_bucket,size_bucket"
    )
    assert len(rows) == 2
    assert round(sum(float(row["net_pnl"]) for row in rows), 2) == 50.0
    assert rows[0]["ticker"] == "'=SPY"
