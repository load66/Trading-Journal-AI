import json

from performance_report import build_performance_report


def trade(group, date, ticker, pnl, entry, exit_, qty=1, price=10.0,
          instrument="OPTION", side="LONG", extra_entries=None, open_position=False):
    entry_action, exit_action = ("BOT", "SOLD") if side == "LONG" else ("SOLD", "BOT")
    executions = [
        {"date": date, "time": entry, "action": entry_action, "qty": qty, "price": price, "commission": 0},
    ]
    for when, add_qty, add_price in (extra_entries or []):
        executions.append({"date": date, "time": when, "action": entry_action, "qty": add_qty, "price": add_price, "commission": 0})
    if not open_position:
        total_qty = qty + sum(x[1] for x in (extra_entries or []))
        executions.append({"date": date, "time": exit_, "action": exit_action, "qty": total_qty, "price": price + 1, "commission": 0})
    return {
        "id": len(group), "account_id": 1, "trade_group": group, "date": date,
        "ticker": ticker, "instrument_type": instrument, "side": side,
        "gross_pnl": pnl, "net_pnl": pnl, "commissions": 0,
        "executions": json.dumps(executions),
        "option_expiry": None, "option_strike": None, "option_type": None,
        "source": "imported",
    }


def test_exact_hold_time_boundaries_and_open_positions():
    rows = [
        trade("a", "2026-09-01", "SPY", 100, "09:30:00", "09:30:29"),
        trade("b", "2026-09-01", "QQQ", -50, "09:31:00", "09:31:30"),
        trade("c", "2026-09-01", "IWM", 25, "09:32:00", "09:33:00"),
        trade("open", "2026-09-01", "AAPL", 0, "10:00:00", "10:10:00",
              instrument="STOCK", qty=10, price=200, open_position=True),
    ]
    report = build_performance_report(rows)

    buckets = {r["bucket"]: r for r in report["hold_time"]}
    assert buckets["Under 30 sec"]["trade_count"] == 1
    assert buckets["30s-1min"]["trade_count"] == 1
    assert buckets["1-2min"]["trade_count"] == 1
    assert report["meta"]["trade_count"] == 3
    assert report["meta"]["open_position_count"] == 1
    assert report["matching"]["open_positions"][0]["ticker"] == "AAPL"


def test_short_trade_timestamps_and_option_size_bucket():
    rows = [
        trade("short", "2026-09-02", "SPY", 200, "10:00:00", "10:06:00",
              qty=10, price=5.0, side="SHORT"),
    ]
    report = build_performance_report(rows)
    assert report["hold_time"][0]["bucket"] == "5-10min"
    sizes = {r["bucket"]: r for r in report["position_size"]["options"]}
    assert sizes["6-10"]["trade_count"] == 1


def test_revenge_depth_chase_and_averaging_down():
    rows = [
        trade("t1", "2026-09-03", "SPY", -100, "09:30:00", "09:31:00"),
        trade("t2", "2026-09-03", "SPY", -50, "09:31:20", "09:32:00"),
        trade("t3", "2026-09-03", "SPY", 25, "09:32:20", "09:33:00"),
        trade("avg", "2026-09-03", "QQQ", -200, "10:00:00", "10:06:00",
              qty=1, price=10.0, extra_entries=[("10:01:00", 1, 9.0)]),
    ]
    report = build_performance_report(rows)
    revenge = {r["depth"]: r for r in report["behavior"]["revenge_trading"]}
    assert revenge["1st re-entry"]["trade_count"] == 1
    assert revenge["2nd re-entry"]["trade_count"] == 1
    assert report["behavior"]["chasing_fomo"]["trade_count"] == 2
    assert report["behavior"]["averaging_down"]["averaged_down"]["trade_count"] == 1


def test_daily_stop_model_stops_after_breach():
    rows = [
        trade("d1", "2026-09-04", "SPY", -100, "09:30:00", "09:31:00"),
        trade("d2", "2026-09-04", "QQQ", -100, "09:32:00", "09:33:00"),
        trade("d3", "2026-09-04", "IWM", -500, "09:34:00", "09:35:00"),
        trade("d4", "2026-09-04", "AAPL", 400, "09:36:00", "09:37:00", instrument="STOCK", qty=10, price=100),
    ]
    report = build_performance_report(rows)
    assert report["daily_pnl"][0]["total_pnl"] == -300
    levels = report["daily_stop_model"]["levels"]
    assert levels
    assert any(level["breach_count"] == 1 for level in levels)


def test_first_ten_minutes_and_ticker_labels():
    rows = [
        trade("e1", "2026-09-08", "SPY", 100, "09:31:00", "09:37:00"),
        trade("e2", "2026-09-08", "SPY", 100, "10:31:00", "10:37:00"),
        trade("l1", "2026-09-08", "TSLA", -300, "11:00:00", "11:06:00"),
    ]
    report = build_performance_report(rows)
    assert report["time_analysis"]["first_10_minutes"]["trade_count"] == 1
    ranking = {r["ticker"]: r for r in report["ticker_ranking"]}
    assert ranking["SPY"]["label"] == "EDGE"
    assert ranking["TSLA"]["label"] in {"LEAK", "BLEEDING", "HEMORRHAGE"}


def test_negative_opening_window_is_ranked_as_behavior_flaw():
    rows = [
        trade("o1", "2026-09-09", "SPY", -200, "09:31:00", "09:36:00"),
        trade("m1", "2026-09-09", "QQQ", 50, "10:31:00", "10:36:00"),
    ]
    report = build_performance_report(rows)
    names = [r["name"] for r in report["behavior"]["ranked_flaws"]]
    assert "First 30 minutes" in names


def test_option_and_stock_sizing_are_normalized_separately():
    rows = [
        trade("os", "2026-09-10", "SPY", 50, "09:30:00", "09:36:00",
              qty=2, price=2.0, instrument="OPTION"),
        trade("ob", "2026-09-10", "QQQ", -40, "09:40:00", "09:41:00",
              qty=10, price=2.0, instrument="OPTION"),
        trade("ss", "2026-09-10", "AMD", 100, "10:00:00", "10:06:00",
              qty=10, price=100, instrument="STOCK"),
        trade("sb", "2026-09-10", "NVDA", -100, "10:10:00", "10:11:00",
              qty=100, price=100, instrument="STOCK"),
    ]
    report = build_performance_report(rows)
    cross = report["position_size"]["cross_reference"]
    assert cross["typical_option_contracts"] == 6.0
    assert cross["typical_share_notional"] == 5500.0
    assert cross["small_size_long_hold"]["trade_count"] == 2
    assert cross["big_size_short_hold"]["trade_count"] == 2


def test_averaging_down_uses_running_average_not_first_fill_only():
    # 100 then 102 gives a running average of 101. Adding at 100.5 is below
    # the running average, so it is an add after price moved against the position.
    row = trade("avg-running", "2026-09-11", "SPY", -25, "09:30:00", "09:40:00",
                qty=1, price=100, instrument="STOCK",
                extra_entries=[("09:31:00", 1, 102), ("09:32:00", 1, 100.5)])
    report = build_performance_report([row])
    assert report["behavior"]["averaging_down"]["averaged_down"]["trade_count"] == 1


def test_tilt_size_is_reported_as_multiple_of_typical_instrument_size():
    rows = [
        trade("a1", "2026-09-12", "SPY", -100, "09:30:00", "09:31:00", qty=2),
        trade("a2", "2026-09-12", "QQQ", -100, "09:32:00", "09:33:00", qty=2),
        trade("a3", "2026-09-12", "IWM", -100, "09:34:00", "09:35:00", qty=2),
        trade("a4", "2026-09-12", "DIA", -100, "09:36:00", "09:37:00", qty=6),
    ]
    report = build_performance_report(rows)
    tilt = report["behavior"]["tilt_escalation"]
    assert tilt["size_unit"] == "multiple of typical size within instrument family"
    assert tilt["first3_avg_size"] is not None
    assert tilt["post_threshold_avg_size"] is not None
