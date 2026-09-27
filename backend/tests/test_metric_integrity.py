import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from excursion_analysis import EXCURSION_ENGINE_VERSION, calculate_trade_excursion, sanitize_excursion_metrics  # noqa: E402
from trade_metrics import (  # noqa: E402
    execution_datetime,
    execution_financials,
    first_entry_minutes,
    performance_summary,
    trade_pl_percent,
)


def fill(date, time, action, qty, price, commission=0.0):
    return {
        "date": date,
        "time": time,
        "action": action,
        "qty": qty,
        "price": price,
        "commission": commission,
    }


def test_wmt_sep25_reconciles_exactly_to_schwab_executions():
    trade = {
        "date": "2026-09-25",
        "ticker": "WMT",
        "instrument_type": "OPTION",
        "option_type": "CALL",
        "side": "LONG",
        "net_pnl": -172.10,
        "executions": [
            fill("2026-09-25", "14:43:00", "BOT", 2, 3.35, 1.02),
            fill("2026-09-25", "14:51:00", "BOT", 1, 3.27, 0.51),
            fill("2026-09-25", "14:52:00", "SOLD", 3, 2.76, 1.57),
        ],
    }

    result = execution_financials(trade)
    assert result["gross_pnl"] == -169.00
    assert result["commissions"] == 3.10
    assert result["net_pnl"] == -172.10
    assert trade_pl_percent(trade) == -17.26


def test_legacy_schwab_clock_is_interpreted_as_central_then_converted_to_eastern():
    execution = fill("2026-09-25", "14:43:00", "BOT", 2, 3.35)
    et = execution_datetime(execution, target_timezone="America/New_York")
    assert et is not None
    assert (et.hour, et.minute) == (15, 43)

    trade = {
        "date": "2026-09-25",
        "side": "LONG",
        "instrument_type": "OPTION",
        "executions": [execution, fill("2026-09-25", "14:52:00", "SOLD", 2, 2.76)],
    }
    assert first_entry_minutes(trade, "America/New_York") == 15 * 60 + 43


def test_wmt_loser_excursion_cannot_report_near_zero_adverse_move_or_profit_capture():
    trade = {
        "trade_group": "wmt-audit",
        "date": "2026-09-25",
        "ticker": "WMT",
        "instrument_type": "OPTION",
        "option_type": "CALL",
        "side": "LONG",
        "net_pnl": -172.10,
        "executions": [
            fill("2026-09-25", "14:43:00", "BOT", 2, 3.35, 1.02),
            fill("2026-09-25", "14:51:00", "BOT", 1, 3.27, 0.51),
            fill("2026-09-25", "14:52:00", "SOLD", 3, 2.76, 1.57),
        ],
    }
    # Alpaca bars are UTC. Sep 25, 2026 is EDT, so 14:43 CT == 19:43 UTC.
    bars = [
        {"t": "2026-09-25T19:43:00Z", "o": 3.35, "h": 3.36, "l": 3.32, "c": 3.34},
        {"t": "2026-09-25T19:44:00Z", "o": 3.34, "h": 3.36, "l": 3.25, "c": 3.28},
        {"t": "2026-09-25T19:45:00Z", "o": 3.28, "h": 3.30, "l": 3.10, "c": 3.15},
        {"t": "2026-09-25T19:46:00Z", "o": 3.15, "h": 3.18, "l": 3.00, "c": 3.05},
        {"t": "2026-09-25T19:47:00Z", "o": 3.05, "h": 3.10, "l": 2.95, "c": 3.00},
        {"t": "2026-09-25T19:48:00Z", "o": 3.00, "h": 3.05, "l": 2.90, "c": 2.95},
        {"t": "2026-09-25T19:49:00Z", "o": 2.95, "h": 3.00, "l": 2.85, "c": 2.90},
        {"t": "2026-09-25T19:50:00Z", "o": 2.90, "h": 2.95, "l": 2.80, "c": 2.85},
        {"t": "2026-09-25T19:51:00Z", "o": 2.85, "h": 2.90, "l": 2.75, "c": 2.80},
        {"t": "2026-09-25T19:52:00Z", "o": 2.80, "h": 2.82, "l": 2.74, "c": 2.76},
    ]

    result = calculate_trade_excursion(trade, bars, bar_basis="option_premium_1m")
    assert result["available"] is True
    assert result["engine_version"] == EXCURSION_ENGINE_VERSION
    assert result["mae_pct"] >= 17.26
    assert result["exit_efficiency"] is None


def test_stale_excursion_values_are_never_exposed_as_current():
    stale = sanitize_excursion_metrics({
        "instrument_type": "OPTION",
        "net_pnl": -172.10,
        "mfe_pct": 0.1205,
        "mae_pct": 0.0093,
        "exit_efficiency": 76.92,
        "excursion_basis": None,
        "excursion_version": None,
    })
    assert stale["excursion_current"] is False
    assert stale["mfe_pct"] is None
    assert stale["mae_pct"] is None
    assert stale["exit_efficiency"] is None


def test_performance_summary_excludes_open_positions_and_uses_net_day_result():
    closed_green = {
        "date": "2026-09-01",
        "side": "LONG",
        "instrument_type": "OPTION",
        "net_pnl": 100.0,
        "executions": [fill("2026-09-01", "09:30", "BOT", 1, 1.0), fill("2026-09-01", "09:35", "SOLD", 1, 2.0)],
    }
    closed_loss_same_day = {
        "date": "2026-09-01",
        "side": "LONG",
        "instrument_type": "OPTION",
        "net_pnl": -150.0,
        "executions": [fill("2026-09-01", "10:00", "BOT", 1, 2.0), fill("2026-09-01", "10:10", "SOLD", 1, 0.5)],
    }
    closed_green_day = {
        "date": "2026-09-02",
        "side": "LONG",
        "instrument_type": "OPTION",
        "net_pnl": 25.0,
        "executions": [fill("2026-09-02", "09:30", "BOT", 1, 1.0), fill("2026-09-02", "09:31", "SOLD", 1, 1.25)],
    }
    open_position = {
        "date": "2026-09-02",
        "side": "LONG",
        "instrument_type": "OPTION",
        "net_pnl": 0.0,
        "executions": [fill("2026-09-02", "11:00", "BOT", 2, 1.0)],
    }

    summary = performance_summary([closed_green, closed_loss_same_day, closed_green_day, open_position])
    assert summary["total_trades"] == 3
    assert summary["total_net_pnl"] == -25.0
    assert summary["trading_days"] == 2
    assert summary["green_days"] == 1
    assert summary["red_days"] == 1
    assert summary["day_win_rate"] == 50.0
    assert summary["expectancy"] == pytest.approx(-8.33, abs=0.01)


def test_manual_option_financials_use_100x_multiplier():
    trade = {
        "date": "2026-09-25",
        "ticker": "SPY",
        "instrument_type": "OPTION",
        "side": "LONG",
        "executions": [
            fill("2026-09-25", "09:30", "BOT", 2, 1.00, 1.30),
            fill("2026-09-25", "09:35", "SOLD", 2, 1.50, 1.34),
        ],
    }
    result = execution_financials(trade)
    assert result["gross_pnl"] == 100.0
    assert result["net_pnl"] == 97.36
