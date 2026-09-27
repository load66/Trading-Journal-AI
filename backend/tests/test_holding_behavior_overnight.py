import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path


BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))


def fresh_main(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "journal.db"))
    monkeypatch.setenv("UPLOAD_DIR", str(tmp_path / "uploads"))
    monkeypatch.setenv("AUTH_REQUIRED", "false")

    for name in ("auth", "database", "main"):
        sys.modules.pop(name, None)

    import main
    return main


def canonical_trade_executions(entry_utc: str, hold_minutes: float):
    entry = datetime.fromisoformat(entry_utc.replace("Z", "+00:00"))
    exit_ = entry + timedelta(minutes=hold_minutes)
    return json.dumps([
        {
            "action": "BOT",
            "qty": 1,
            "price": 1.0,
            "timestamp_utc": entry.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
        },
        {
            "action": "SOLD",
            "qty": 1,
            "price": 2.0,
            "timestamp_utc": exit_.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
        },
    ])


def add_trade(main, conn, account_id, group, ticker, pnl, entry_utc, hold_minutes, *, option=False):
    conn.execute(
        """INSERT INTO trades
           (account_id, trade_group, date, ticker, instrument_type, side,
            gross_pnl, net_pnl, commissions, executions,
            option_expiry, option_strike, option_type, source,
            mfe_pct, mae_pct, exit_efficiency, excursion_basis,
            excursion_version, excursion_calculated_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            account_id,
            group,
            "2026-09-21",
            ticker,
            "OPTION",
            "LONG",
            pnl,
            pnl,
            0.0,
            canonical_trade_executions(entry_utc, hold_minutes),
            "2026-10-16" if option else None,
            45.0 if option else None,
            "CALL" if option else None,
            "imported",
            20.0,
            -5.0,
            80.0 if pnl > 0 else None,
            "option_premium_1m",
            main.EXCURSION_ENGINE_VERSION,
            "2026-09-22T18:00:00Z",
        ),
    )


def seed_account(main, conn, account_type, prefix):
    account_id = main.insert_and_get_id(
        conn,
        "INSERT INTO accounts (name, type, broker) VALUES (?,?,?)",
        (prefix, account_type, "schwab"),
    )
    for index, (duration, pnl) in enumerate(zip((5.5, 10, 18, 56.5), (10, 20, 30, 40)), start=1):
        add_trade(
            main, conn, account_id, f"{prefix}-winner-{index}", f"W{index}", pnl,
            f"2026-09-21T{13 + index:02d}:00:00Z", duration,
        )
    for index, (duration, pnl) in enumerate(zip((5, 8, 9, 13), (-10, -20, -30, -40)), start=1):
        add_trade(
            main, conn, account_id, f"{prefix}-loser-{index}", f"L{index}", pnl,
            f"2026-09-21T{13 + index:02d}:30:00Z", duration,
        )
    # 15:50 ET Sep 21 to 12:10 ET Sep 22: exactly 1,220 elapsed minutes.
    add_trade(
        main, conn, account_id, f"{prefix}-overnight", "U", 500,
        "2026-09-21T19:50:00Z", 1220, option=True,
    )
    return account_id


def edge_report(main, conn, account_id):
    return main.get_edge_report(
        account_id=account_id,
        date_from="2026-09-21",
        date_to="2026-09-22",
        conn=conn,
    )


def test_overnight_detection_uses_market_local_dates_from_canonical_timestamps():
    from trade_metrics import is_overnight_trade

    same_market_date = {
        "side": "LONG",
        "executions": json.loads(canonical_trade_executions("2026-09-21T23:55:00Z", 10)),
    }
    overnight = {
        "side": "LONG",
        "executions": json.loads(canonical_trade_executions("2026-09-21T19:50:00Z", 1220)),
    }

    # Conflicting legacy fields prove timestamp_utc is the authoritative source.
    same_market_date["executions"][0].update({"date": "2026-09-20", "time": "23:55:00"})
    same_market_date["executions"][1].update({"date": "2026-09-21", "time": "00:05:00"})
    overnight["executions"][0].update({"date": "2026-09-21", "time": "09:30:00"})
    overnight["executions"][1].update({"date": "2026-09-21", "time": "09:40:00"})

    # The first trade crosses midnight UTC but both fills are Sep 21 in New York.
    assert is_overnight_trade(same_market_date) is False
    assert is_overnight_trade(overnight) is True


def test_day_trading_holding_behavior_excludes_overnight_only(monkeypatch, tmp_path):
    main = fresh_main(monkeypatch, tmp_path)
    main.init_db()
    conn = main.get_db()
    try:
        day_id = seed_account(main, conn, "day_trading", "day")
        swing_id = seed_account(main, conn, "swing_trading", "swing")
        investment_id = seed_account(main, conn, "investment", "investment")
        conn.commit()

        day = edge_report(main, conn, day_id)
        swing = edge_report(main, conn, swing_id)
        investment = edge_report(main, conn, investment_id)

        assert day["hold_time"] == {
            "winners_avg_min": 22.5,
            "losers_avg_min": 8.8,
            "winners_median_min": 14.0,
            "losers_median_min": 8.5,
            "winner_count": 4,
            "loser_count": 4,
            "sample_count": 8,
            "coverage_pct": 88.9,
            "source": "broker_csv_executions",
            "overnight_excluded_count": 1,
            "overnight_excluded": [{
                "trade_group": "day-overnight",
                "ticker": "U",
                "option_expiry": "2026-10-16",
                "option_strike": 45.0,
                "option_type": "CALL",
                "hold_minutes": 1220.0,
                "net_pnl": 500.0,
            }],
        }

        for unchanged in (swing, investment):
            assert unchanged["hold_time"]["winners_avg_min"] == 262.0
            assert unchanged["hold_time"]["winners_median_min"] == 18.0
            assert unchanged["hold_time"]["sample_count"] == 9
            assert unchanged["hold_time"]["overnight_excluded_count"] == 0
            assert unchanged["hold_time"]["overnight_excluded"] == []

        # The account type changes only Holding Behavior, not other Edge metrics.
        for key in (
            "time_of_day", "day_of_week", "r_multiple_dist", "emotion_outcomes",
            "mistake_frequency", "expectancy", "total_trades",
        ):
            assert day[key] == swing[key]
            assert day[key] == investment[key]
        assert day["total_trades"] == 9
        assert day["expectancy"] == 55.56
        assert sum(row["trade_count"] for row in day["time_of_day"]) == 9
        assert sum(row["trade_count"] for row in day["day_of_week"]) == 9

        # The overnight U contract remains in KPIs, the calendar, reports,
        # excursion/profit-capture fields, and journal history.
        kpis = main.get_kpis(
            account_id=day_id, date_from="2026-09-21", date_to="2026-09-22", conn=conn,
        )
        assert kpis["total_trades"] == 9
        assert kpis["total_net_pnl"] == 500
        assert kpis["win_rate"] == 55.56

        calendar = main.get_calendar(account_id=day_id, year=2026, month=9, conn=conn)
        assert calendar == [{
            "date": "2026-09-21",
            "net_pnl": 500.0,
            "trade_count": 9,
            "winners": 5,
            "losers": 4,
            "win_rate": 55.6,
            "has_diary": False,
        }]

        reports = main.get_reports(
            account_id=day_id, date_from="2026-09-21", date_to="2026-09-22", conn=conn,
        )
        assert reports["trade_count"] == 9
        assert reports["summary"]["net_pnl"] == 500.0
        assert sum(bucket["trades"] for bucket in reports["by_hold_time"]) == 9

        history = main.list_trades(
            account_id=day_id,
            instrument_type=None,
            date_from="2026-09-21",
            date_to="2026-09-22",
            ticker=None,
            open_only=False,
            closed_only=False,
            sort_by=None,
            limit=None,
            conn=conn,
        )
        assert len(history) == 9
        overnight_history = next(trade for trade in history if trade["ticker"] == "U")
        assert overnight_history["net_pnl"] == 500
        assert overnight_history["mfe_pct"] == 20.0
        assert overnight_history["mae_pct"] == -5.0
        assert overnight_history["exit_efficiency"] == 80.0
    finally:
        conn.close()
