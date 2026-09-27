import json
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from behavior_rules import detect_daily_flags, deterministic_strengths  # noqa: E402
from excursion_analysis import calculate_trade_excursion  # noqa: E402


def fill(date, time, action, qty, price, source_timezone="America/New_York"):
    return {
        "date": date,
        "time": time,
        "action": action,
        "qty": qty,
        "price": price,
        "source_timezone": source_timezone,
    }


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
    result = calculate_trade_excursion(trade, bars, bar_basis="stock_1m")
    assert result["available"] is True
    assert result["basis"] == "stock_1m"
    assert result["mfe_pct"] == 10.0
    assert result["mae_pct"] == 2.0
    assert result["exit_efficiency"] == 40.0


def test_option_excursion_uses_contract_premium_path_and_broker_fills():
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
        {"t": "2026-09-25T13:30:00Z", "o": 1.00, "h": 1.10, "l": 0.95, "c": 1.05},
        {"t": "2026-09-25T13:31:00Z", "o": 1.05, "h": 1.50, "l": 1.00, "c": 1.40},
        {"t": "2026-09-25T13:32:00Z", "o": 1.40, "h": 1.45, "l": 1.20, "c": 1.25},
    ]
    result = calculate_trade_excursion(trade, bars, bar_basis="option_premium_1m")
    assert result["basis"] == "option_premium_1m"
    assert result["mfe_pct"] == 50.0
    # Entry-minute low may predate the fill, so it is not claimed as confirmed MAE.\n    assert result["mae_pct"] == 0.0\n    assert result["exit_efficiency"] == 50.0


def test_option_excursion_rejects_underlying_path_as_profit_capture():
    d = "2026-09-25"
    trade = {
        "trade_group": "call1",
        "date": d,
        "ticker": "TSM",
        "instrument_type": "OPTION",
        "option_type": "CALL",
        "side": "LONG",
        "net_pnl": 65.94,
        "executions": [
            fill(d, "09:30:10", "BOT", 1, 1.00),
            fill(d, "09:32:20", "SOLD", 1, 1.25),
        ],
    }
    underlying_bars = [
        {"t": "2026-09-25T13:30:00Z", "o": 452.5, "h": 452.51, "l": 452.0, "c": 452.5},
        {"t": "2026-09-25T13:31:00Z", "o": 452.5, "h": 452.52, "l": 451.9, "c": 452.0},
        {"t": "2026-09-25T13:32:00Z", "o": 452.0, "h": 452.2, "l": 451.8, "c": 452.1},
    ]
    result = calculate_trade_excursion(trade, underlying_bars, bar_basis="stock_1m")
    assert result["available"] is False
    assert "option premium" in result["reason"].lower()



def test_profitable_option_efficiency_is_bounded_by_actual_premium_path():
    d = "2026-09-25"
    trade = {
        "trade_group": "winner",
        "date": d,
        "ticker": "TEM",
        "instrument_type": "OPTION",
        "option_type": "CALL",
        "side": "LONG",
        "net_pnl": 8.97,
        "executions": [
            fill(d, "09:30:10", "BOT", 1, 1.00),
            fill(d, "09:32:20", "SOLD", 1, 1.05),
        ],
    }
    bars = [
        {"t": "2026-09-25T13:30:00Z", "o": 1.00, "h": 1.01, "l": 0.98, "c": 1.00},
        {"t": "2026-09-25T13:31:00Z", "o": 1.00, "h": 1.02, "l": 0.99, "c": 1.01},
        {"t": "2026-09-25T13:32:00Z", "o": 1.01, "h": 1.03, "l": 1.00, "c": 1.02},
    ]
    result = calculate_trade_excursion(trade, bars, bar_basis="option_premium_1m")
    assert result["available"] is True
    assert result["mfe_pct"] == 5.0
    assert result["exit_efficiency"] == 100.0
    assert 0.0 <= result["exit_efficiency"] <= 100.0


def test_partial_exit_capture_uses_actual_execution_cashflows():
    d = "2026-09-25"
    trade = {
        "trade_group": "scaleout",
        "date": d,
        "ticker": "QQQ",
        "instrument_type": "OPTION",
        "option_type": "CALL",
        "side": "LONG",
        "net_pnl": 350.0,
        "executions": [
            fill(d, "09:30:10", "BOT", 10, 1.00),
            fill(d, "09:31:20", "SOLD", 5, 1.50),
            fill(d, "09:32:20", "SOLD", 5, 1.20),
        ],
    }
    bars = [
        {"t": "2026-09-25T13:30:00Z", "o": 1.00, "h": 1.10, "l": 0.95, "c": 1.05},
        {"t": "2026-09-25T13:31:00Z", "o": 1.05, "h": 1.60, "l": 1.00, "c": 1.40},
        {"t": "2026-09-25T13:32:00Z", "o": 1.40, "h": 1.45, "l": 1.15, "c": 1.20},
    ]
    result = calculate_trade_excursion(trade, bars, bar_basis="option_premium_1m")
    # Actual per-contract cash flow: -10 + 7.5 + 6 = +3.5.
    # Best attainable P&L on the actual scale-out path is +5.0 at the first exit.
    assert result["path_max_pnl_per_unit"] == 5.0
    assert result["exit_efficiency"] == 70.0


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



def test_strengths_ignore_legacy_option_underlying_efficiency():
    row = {
        "trade_group": "legacy",
        "date": "2026-09-25",
        "ticker": "TSM",
        "instrument_type": "OPTION",
        "option_type": "CALL",
        "side": "LONG",
        "net_pnl": 65.94,
        "exit_efficiency": -12600.0,
        "excursion_basis": "underlying_1m",
        "executions": [
            fill("2026-09-25", "09:30:10", "BOT", 1, 1.00),
            fill("2026-09-25", "09:32:20", "SOLD", 1, 1.25),
        ],
    }
    observations = deterministic_strengths([row], {
        "total_net_pnl": 0,
        "profit_factor": None,
        "avg_win": 0,
        "avg_loss": 0,
    })
    assert all("exit efficiency" not in o["text"].lower() for o in observations)

def test_stock_excursion_does_not_look_ahead_to_later_scale_in():
    d = "2026-09-25"
    trade = {
        "trade_group": "scale",
        "date": d,
        "ticker": "SPY",
        "instrument_type": "STOCK",
        "side": "LONG",
        "net_pnl": 10,
        "executions": [
            fill(d, "09:30:10", "BOT", 10, 100),
            fill(d, "09:31:10", "BOT", 10, 80),
            fill(d, "09:32:20", "SOLD", 20, 90),
        ],
    }
    bars = [
        {"t": "2026-09-25T13:30:00Z", "o": 100, "h": 105, "l": 99, "c": 100},
        {"t": "2026-09-25T13:31:00Z", "o": 90, "h": 92, "l": 79, "c": 80},
        {"t": "2026-09-25T13:32:00Z", "o": 88, "h": 91, "l": 87, "c": 90},
    ]
    result = calculate_trade_excursion(trade, bars, bar_basis="stock_1m")
    assert result["entry_reference"] == 100
    # Every held minute contains a fill, so no intraminute high/low is\n    # claimed without ordering evidence. Whole-trade economic MAE is $200 on\n    # $1,800 entry cost after the second fill.\n    assert result["mfe_pct"] == 0.0\n    assert result["mae_pct"] == 11.1111


def test_high_trade_count_requires_personal_history():
    rows = [
        stock_trade(f"t{i}", 5, entry=f"10:{i:02d}:00", exit=f"10:{i:02d}:30", ticker=f"T{i}")
        for i in range(12)
    ]
    no_history = detect_daily_flags(rows, [])
    assert "high_trade_count" not in {f["code"] for f in no_history}

    flags = detect_daily_flags(rows, [3, 4, 5, 4, 6, 5, 7, 4, 6, 5, 4, 5])
    high = next(f for f in flags if f["code"] == "high_trade_count")
    assert high["metric"]["trade_count"] == 12
    assert high["evidence"] == "VERIFIED"


def test_legacy_schwab_central_clock_is_converted_before_bar_matching():
    d = "2026-09-25"
    trade = {
        "trade_group": "wmt-clock",
        "date": d,
        "ticker": "WMT",
        "instrument_type": "OPTION",
        "option_type": "CALL",
        "side": "LONG",
        "net_pnl": -172.10,
        "executions": [
            fill(d, "14:43:00", "BOT", 2, 3.35, "America/Chicago"),
            fill(d, "14:51:00", "BOT", 1, 3.27, "America/Chicago"),
            fill(d, "14:52:00", "SOLD", 3, 2.76, "America/Chicago"),
        ],
    }
    # 14:43 CT = 15:43 ET = 19:43Z during CDT.
    bars = [
        {"t": "2026-09-25T19:43:00Z", "o": 3.35, "h": 3.36, "l": 3.20, "c": 3.25},
        {"t": "2026-09-25T19:44:00Z", "o": 3.25, "h": 3.30, "l": 3.10, "c": 3.15},
        {"t": "2026-09-25T19:45:00Z", "o": 3.15, "h": 3.20, "l": 3.00, "c": 3.05},
        {"t": "2026-09-25T19:46:00Z", "o": 3.05, "h": 3.10, "l": 2.95, "c": 3.00},
        {"t": "2026-09-25T19:47:00Z", "o": 3.00, "h": 3.05, "l": 2.90, "c": 2.95},
        {"t": "2026-09-25T19:48:00Z", "o": 2.95, "h": 3.00, "l": 2.85, "c": 2.90},
        {"t": "2026-09-25T19:49:00Z", "o": 2.90, "h": 2.95, "l": 2.80, "c": 2.85},
        {"t": "2026-09-25T19:50:00Z", "o": 2.85, "h": 2.90, "l": 2.75, "c": 2.80},
        {"t": "2026-09-25T19:51:00Z", "o": 2.80, "h": 2.82, "l": 2.70, "c": 2.74},
        {"t": "2026-09-25T19:52:00Z", "o": 2.74, "h": 2.78, "l": 2.70, "c": 2.76},
    ]
    result = calculate_trade_excursion(trade, bars, bar_basis="option_premium_1m")
    assert result["available"] is True
    # Gross exit loss is $169 on $997 entry premium, so MAE cannot be smaller.
    assert result["mae_pct"] >= 16.95
    assert result["exit_efficiency"] is None


def test_multifill_excursion_uses_whole_trade_economic_path():
    d = "2026-09-25"
    trade = {
        "trade_group": "scale-economic",
        "date": d,
        "ticker": "SPY",
        "instrument_type": "OPTION",
        "option_type": "CALL",
        "side": "LONG",
        "net_pnl": -50,
        "executions": [
            fill(d, "09:30:10", "BOT", 1, 1.00),
            fill(d, "09:31:10", "BOT", 1, 0.80),
            fill(d, "09:32:20", "SOLD", 2, 0.65),
        ],
    }
    bars = [
        {"t": "2026-09-25T13:30:00Z", "o": 1.00, "h": 1.02, "l": 0.98, "c": 1.00},
        {"t": "2026-09-25T13:31:00Z", "o": 0.90, "h": 0.92, "l": 0.78, "c": 0.80},
        {"t": "2026-09-25T13:32:00Z", "o": 0.70, "h": 0.72, "l": 0.60, "c": 0.65},
    ]
    result = calculate_trade_excursion(trade, bars, bar_basis="option_premium_1m")
    # Entry premium = 1.80. Exit value = 1.30 => realized gross loss 0.50.
    assert result["mae_pct"] >= round(0.50 / 1.80 * 100, 4)
    assert result["exit_efficiency"] is None
