import json
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from behavior_rules import detect_daily_flags, deterministic_strengths  # noqa: E402
from excursion_analysis import calculate_trade_excursion  # noqa: E402


def fill(date, time, action, qty, price):
    return {"date": date, "time": time, "action": action, "qty": qty, "price": price}


def stock_trade(group, pnl, entry="09:30:10", exit="09:32:20", qty=10, entry_price=100, exit_price=104, ticker="SPY"):
    d = "2026-09-25"
    return {
        "trade_group": group,
        "date": d,
        "ticker": ticker,
        "instrument_type": "STOCK",
        "side": "LONG",
        "net_pnl": pnl,
        "executions": [
            fill(d, entry, "BOT", qty, entry_price),
            fill(d, exit, "SOLD", qty, exit_price),
        ],
    }


def test_stock_excursion_uses_actual_fill_prices():
    trade = stock_trade("s1", 40)
    bars = [
        {"t": "2026-09-25T13:30:00Z", "o": 100, "h": 103, "l": 99, "c": 102},
        {"t": "2026-09-25T13:31:00Z", "o": 102, "h": 110, "l": 98, "c": 108},
        {"t": "2026-09-25T13:32:00Z", "o": 108, "h": 109, "l": 103, "c": 104},
    ]
    result = calculate_trade_excursion(trade, bars)
    assert result["available"] is True
    assert result["basis"] == "execution_price"
    assert result["mfe_pct"] == 10.0
    assert result["mae_pct"] == 2.0
    assert result["exit_efficiency"] == 40.0


def test_long_put_excursion_uses_inverse_underlying_direction():
    d = "2026-09-25"
    trade = {
        "trade_group": "put1",
        "date": d,
        "ticker": "SPY",
        "instrument_type": "OPTION",
        "option_type": "PUT",
        "side": "LONG",
        "net_pnl": 50,
        "executions": [
            fill(d, "09:30:10", "BOT", 2, 1.00),
            fill(d, "09:32:20", "SOLD", 2, 1.25),
        ],
    }
    bars = [
        {"t": "2026-09-25T13:30:00Z", "o": 100, "h": 102, "l": 99, "c": 100},
        {"t": "2026-09-25T13:31:00Z", "o": 100, "h": 101, "l": 95, "c": 96},
        {"t": "2026-09-25T13:32:00Z", "o": 96, "h": 97, "l": 95.5, "c": 96},
    ]
    result = calculate_trade_excursion(trade, bars)
    assert result["basis"] == "underlying_1m"
    assert result["mfe_pct"] == 5.0
    assert result["mae_pct"] == 2.0
    assert result["exit_efficiency"] == 80.0


def test_behavior_rules_are_execution_based_not_psychological():
    d = "2026-09-25"
    first = stock_trade("loss1", -100, entry="09:30:00", exit="09:31:00", qty=10, entry_price=100, exit_price=90, ticker="SPY")
    second = stock_trade("r1", 20, entry="09:31:20", exit="09:32:00", qty=20, entry_price=90, exit_price=91, ticker="SPY")
    # Add below running average: 100 then 99.
    third = {
        "trade_group": "avg",
        "date": d,
        "ticker": "QQQ",
        "instrument_type": "STOCK",
        "side": "LONG",
        "net_pnl": -20,
        "executions": [
            fill(d, "10:00:00", "BOT", 10, 100),
            fill(d, "10:01:00", "BOT", 10, 99),
            fill(d, "10:05:00", "SOLD", 20, 98),
        ],
    }
    flags = detect_daily_flags([first, second, third])
    codes = {f["code"] for f in flags}
    assert "loss_reentry" in codes
    assert "rapid_reentry" in codes
    assert "size_escalation_after_loss" in codes
    assert "averaging_down" in codes
    assert all(f["evidence"] == "VERIFIED" for f in flags)
    assert all("revenge" not in (f["title"] + f["detail"]).lower() for f in flags)


def test_strengths_are_deterministic_numbers():
    rows = [
        stock_trade("w1", 200, ticker="SPY"),
        stock_trade("l1", -50, entry="10:00:00", exit="10:02:00", ticker="QQQ"),
    ]
    obs = deterministic_strengths(rows, {
        "total_net_pnl": 150,
        "profit_factor": 4.0,
        "avg_win": 200,
        "avg_loss": -50,
    })
    text = " ".join(o["text"] for o in obs)
    assert "USD 150.00" in text
    assert "4.00" in text
    assert "4.00×" in text
    assert all(o["evidence"] == "VERIFIED" for o in obs)
