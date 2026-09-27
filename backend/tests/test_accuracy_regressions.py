import asyncio
import sys

import pytest
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))


def fresh_main(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "journal.db"))
    monkeypatch.setenv("UPLOAD_DIR", str(tmp_path / "uploads"))
    monkeypatch.setenv("APCA_API_KEY_ID", "test-key")
    monkeypatch.setenv("APCA_API_SECRET_KEY", "test-secret")
    monkeypatch.setenv("AUTH_REQUIRED", "false")

    for name in ("auth", "database", "main"):
        sys.modules.pop(name, None)

    import main
    return main


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


class FakeClient:
    def __init__(self, payload):
        self.payload = payload
        self.calls = 0

    async def get(self, *_args, **_kwargs):
        self.calls += 1
        return FakeResponse(self.payload)


def test_alpaca_null_bars_is_valid_empty_result(monkeypatch, tmp_path):
    main = fresh_main(monkeypatch, tmp_path)
    client = FakeClient({"bars": None, "next_page_token": None})

    bars = asyncio.run(
        main._fetch_alpaca_bars(
            client,
            "https://example.test/bars",
            {"timeframe": "1Min"},
            {"APCA-API-KEY-ID": "x"},
        )
    )

    assert bars == []
    assert client.calls == 1


def test_chart_empty_market_response_returns_clear_warning(monkeypatch, tmp_path):
    main = fresh_main(monkeypatch, tmp_path)

    async def no_bars(*_args, **_kwargs):
        return []

    monkeypatch.setattr(main, "_fetch_alpaca_bars", no_bars)
    result = asyncio.run(main.get_chart("SPY", "2026-09-25", "1Min", 1))

    assert result["bars"] == []
    assert "No Alpaca 1Min bars were returned for SPY" in result["warning"]
    assert "NoneType" not in result["warning"]


def test_primary_profit_factor_uses_net_pnl(monkeypatch, tmp_path):
    main = fresh_main(monkeypatch, tmp_path)

    trades = [
        {"net_pnl": 100.0, "gross_pnl": 110.0},
        {"net_pnl": 50.0, "gross_pnl": 60.0},
        {"net_pnl": -75.0, "gross_pnl": -60.0},
    ]

    # Net PF = 150 / 75 = 2.00. Gross PF would be 170 / 60 = 2.83.
    assert main._net_profit_factor(trades) == 2.0


def test_trade_pl_percent_option_uses_100x_multiplier(monkeypatch, tmp_path):
    main = fresh_main(monkeypatch, tmp_path)
    trade = {
        "side": "LONG",
        "instrument_type": "OPTION",
        "net_pnl": 100.0,
        "executions": [
            {"action": "BOT", "qty": 2, "price": 2.50},
            {"action": "SOLD", "qty": 2, "price": 3.00},
        ],
    }
    # $500 entry premium (2 x $2.50 x 100); $100 net profit = 20%.
    assert main._trade_pl_percent(trade) == 20.0


def test_trade_pl_percent_stock_uses_entry_notional(monkeypatch, tmp_path):
    main = fresh_main(monkeypatch, tmp_path)
    trade = {
        "side": "LONG",
        "instrument_type": "STOCK",
        "net_pnl": 50.0,
        "executions": [
            {"action": "BOT", "qty": 10, "price": 100.0},
            {"action": "SOLD", "qty": 10, "price": 105.0},
        ],
    }
    # $1,000 entry notional; $50 net profit = 5%.
    assert main._trade_pl_percent(trade) == 5.0


def test_avg_trade_pl_percent_reuses_canonical_trade_percent(monkeypatch, tmp_path):
    main = fresh_main(monkeypatch, tmp_path)
    trades = [
        {
            "side": "LONG",
            "instrument_type": "OPTION",
            "net_pnl": 100.0,
            "executions": [
                {"action": "BOT", "qty": 2, "price": 2.50},
                {"action": "SOLD", "qty": 2, "price": 3.00},
            ],
        },
        {
            "side": "LONG",
            "instrument_type": "STOCK",
            "net_pnl": -50.0,
            "executions": [
                {"action": "BOT", "qty": 10, "price": 100.0},
                {"action": "SOLD", "qty": 10, "price": 95.0},
            ],
        },
    ]
    # Option = +20%; stock = -5%; average = +7.5%.
    assert main._avg_trade_pl_percent(trades) == 7.5


def test_kpis_context_breakdowns_respect_date_range_and_keep_setup_separate(monkeypatch, tmp_path):
    main = fresh_main(monkeypatch, tmp_path)
    main.init_db()
    conn = main.get_db()
    try:
        account_id = main.insert_and_get_id(
            conn,
            "INSERT INTO accounts (name, type, broker) VALUES (?,?,?)",
            ("Test", "day_trading", "schwab"),
        )
        rows = [
            ("old", "2026-08-20", "OLD_SETUP", "Old Strategy", 50.0),
            ("new", "2026-09-20", "BREAKOUT", "Momentum", 100.0),
        ]
        for group, date, setup, strategy, pnl in rows:
            conn.execute(
                """INSERT INTO trades
                   (account_id, trade_group, date, ticker, instrument_type, side,
                    gross_pnl, net_pnl, commissions, executions, source, setup)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    account_id, group, date, "SPY", "OPTION", "LONG",
                    pnl, pnl, 0.0, "[]", "imported", setup,
                ),
            )
            conn.execute(
                """INSERT INTO trade_analysis (trade_group, ticker, date, strategy)
                   VALUES (?,?,?,?)""",
                (group, "SPY", date, strategy),
            )
        conn.commit()

        result = main.get_kpis(
            account_id=account_id,
            date_from="2026-09-01",
            date_to="2026-09-30",
            conn=conn,
        )

        assert result["total_trades"] == 1
        assert [row["strategy"] for row in result["by_strategy"]] == ["Momentum"]
        assert [row["setup"] for row in result["by_setup"]] == ["BREAKOUT"]
        assert result["by_strategy"][0]["count"] == 1
        assert result["by_setup"][0]["count"] == 1
    finally:
        conn.close()


def test_kpis_average_r_uses_recorded_r_and_respects_date_range(monkeypatch, tmp_path):
    main = fresh_main(monkeypatch, tmp_path)
    main.init_db()
    conn = main.get_db()
    try:
        account_id = main.insert_and_get_id(
            conn,
            "INSERT INTO accounts (name, type, broker) VALUES (?,?,?)",
            ("R Test", "day_trading", "schwab"),
        )
        trades = [
            ("old-r", "2026-08-20", 25.0, -1.0),
            ("new-r", "2026-09-20", 100.0, 0.75),
            ("new-no-r", "2026-09-21", -20.0, None),
        ]
        for group, date, pnl, r_multiple in trades:
            conn.execute(
                """INSERT INTO trades
                   (account_id, trade_group, date, ticker, instrument_type, side,
                    gross_pnl, net_pnl, commissions, executions, source)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    account_id, group, date, "SPY", "OPTION", "LONG",
                    pnl, pnl, 0.0, "[]", "imported",
                ),
            )
            if r_multiple is not None:
                conn.execute(
                    """INSERT INTO trade_analysis
                       (trade_group, ticker, date, r_multiple)
                       VALUES (?,?,?,?)""",
                    (group, "SPY", date, r_multiple),
                )
        conn.commit()

        result = main.get_kpis(
            account_id=account_id,
            date_from="2026-09-01",
            date_to="2026-09-30",
            conn=conn,
        )

        assert result["total_trades"] == 2
        assert result["avg_r"] == 0.75
        assert result["r_sample_count"] == 1
    finally:
        conn.close()


def test_goals_merge_new_skill_baselines_into_older_saved_payload(monkeypatch, tmp_path):
    main = fresh_main(monkeypatch, tmp_path)
    main.init_db()
    conn = main.get_db()
    try:
        conn.execute(
            "INSERT INTO settings (account_id, key, value) VALUES (0, 'goals', ?)",
            ('{"win_rate": 70.0, "profit_factor": 1.8}',),
        )
        conn.commit()

        result = main.get_goals(account_id=None, conn=conn)

        assert result["win_rate"] == 70.0
        assert result["profit_factor"] == 1.8
        assert result["avg_r"] == 0.5
        assert result["loss_containment"] == 2.0
    finally:
        conn.close()



def test_le_risk_plan_persists_per_account_and_date(monkeypatch, tmp_path):
    main = fresh_main(monkeypatch, tmp_path)
    main.init_db()
    conn = main.get_db()
    try:
        body = main.LERiskPlanBody(
            account_id=7,
            date="2026-09-28",
            capital=10000,
            exposure_pct=30,
            direction="call",
            option_price=4.20,
            delta=0.62,
            underlying_entry=150,
            stop_price=148.50,
            rule_committed=True,
            trade1="green",
        )
        saved = main.put_le_risk_plan(body=body, conn=conn)
        assert saved["capital"] == 10000
        assert saved["rule_committed"] is True
        assert saved["trade1"] == "green"

        loaded = main.get_le_risk_plan(plan_date="2026-09-28", account_id=7, conn=conn)
        assert loaded["option_price"] == 4.20
        assert loaded["delta"] == 0.62

        other_account = main.get_le_risk_plan(plan_date="2026-09-28", account_id=8, conn=conn)
        assert other_account["capital"] is None
        assert other_account["trade1"] == ""

        cleared = main.delete_le_risk_plan(plan_date="2026-09-28", account_id=7, conn=conn)
        assert cleared["capital"] is None
        assert main.get_le_risk_plan(plan_date="2026-09-28", account_id=7, conn=conn)["capital"] is None
    finally:
        conn.close()


def test_le_risk_plan_rejects_exposure_outside_le_range(monkeypatch, tmp_path):
    main = fresh_main(monkeypatch, tmp_path)
    main.init_db()
    conn = main.get_db()
    try:
        body = main.LERiskPlanBody(account_id=1, date="2026-09-28", capital=10000, exposure_pct=35)
        with pytest.raises(main.HTTPException) as exc:
            main.put_le_risk_plan(body=body, conn=conn)
        assert exc.value.status_code == 422
        assert "20%" in exc.value.detail
    finally:
        conn.close()

def test_recent_closed_trades_sort_by_broker_exit_time(monkeypatch, tmp_path):
    main = fresh_main(monkeypatch, tmp_path)
    main.init_db()
    conn = main.get_db()
    try:
        account_id = main.insert_and_get_id(
            conn,
            "INSERT INTO accounts (name, type, broker) VALUES (?,?,?)",
            ("Recent", "day_trading", "schwab"),
        )

        rows = [
            ("early", 25.0, [
                {"date": "2026-09-25", "time": "09:30:00", "action": "BOT", "qty": 1, "price": 1.0},
                {"date": "2026-09-25", "time": "09:40:00", "action": "SOLD", "qty": 1, "price": 1.3},
            ]),
            ("late", 50.0, [
                {"date": "2026-09-25", "time": "14:00:00", "action": "BOT", "qty": 1, "price": 1.0},
                {"date": "2026-09-25", "time": "14:52:00", "action": "SOLD", "qty": 1, "price": 1.6},
            ]),
            ("middle", 40.0, [
                {"date": "2026-09-25", "time": "10:00:00", "action": "BOT", "qty": 1, "price": 1.0},
                {"date": "2026-09-25", "time": "11:48:00", "action": "SOLD", "qty": 1, "price": 1.5},
            ]),
            ("open-latest", None, [
                {"date": "2026-09-25", "time": "15:30:00", "action": "BOT", "qty": 1, "price": 1.0},
            ]),
        ]

        for group, pnl, executions in rows:
            conn.execute(
                """INSERT INTO trades
                   (account_id, trade_group, date, ticker, instrument_type, side,
                    gross_pnl, net_pnl, commissions, executions, source, imported_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    account_id, group, "2026-09-25", group.upper(), "OPTION", "LONG",
                    pnl, pnl, 0.0, __import__("json").dumps(executions), "imported",
                    "2026-09-26 20:08:14",
                ),
            )
        conn.commit()

        result = main.list_trades(
            account_id=account_id,
            instrument_type=None,
            date_from=None,
            date_to=None,
            ticker=None,
            open_only=False,
            closed_only=True,
            sort_by="closed_at_desc",
            limit=2,
            conn=conn,
        )

        assert [row["trade_group"] for row in result] == ["late", "middle"]
    finally:
        conn.close()


def test_time_of_day_kpis_group_by_first_entry(monkeypatch, tmp_path):
    main = fresh_main(monkeypatch, tmp_path)
    trades = [
        {
            "side": "LONG",
            "instrument_type": "OPTION",
            "net_pnl": 100.0,
            "executions": [
                {"date": "2026-09-25", "time": "08:33:00", "action": "BOT", "qty": 1, "price": 5.00},
                {"date": "2026-09-25", "time": "08:45:00", "action": "SOLD", "qty": 1, "price": 6.00},
            ],
        },
        {
            "side": "LONG",
            "instrument_type": "OPTION",
            "net_pnl": -50.0,
            "executions": [
                {"date": "2026-09-25", "time": "08:50:00", "action": "BOT", "qty": 1, "price": 5.00},
                {"date": "2026-09-25", "time": "08:55:00", "action": "SOLD", "qty": 1, "price": 4.50},
            ],
        },
        {
            "side": "LONG",
            "instrument_type": "OPTION",
            "net_pnl": 60.0,
            "executions": [
                {"date": "2026-09-25", "time": "09:01:00", "action": "BOT", "qty": 1, "price": 2.00},
                {"date": "2026-09-25", "time": "09:10:00", "action": "SOLD", "qty": 1, "price": 2.60},
            ],
        },
    ]

    rows = main._time_of_day_kpis(trades)

    # Legacy Schwab/TOS clocks are Central; analytics normalize them to ET.
    assert [r["start_minute"] for r in rows] == [570, 600]
    assert rows[0]["label"] == "9:30 AM–10:00 AM"
    assert rows[0]["count"] == 2
    assert rows[0]["net_pnl"] == 50.0
    assert rows[0]["win_rate"] == 50.0
    assert rows[0]["expectancy"] == 25.0
    assert rows[0]["avg_pl_pct"] == 5.0
    assert rows[1]["label"] == "10:00 AM–10:30 AM"
    assert rows[1]["count"] == 1
    assert rows[1]["net_pnl"] == 60.0


def test_context_edge_breakdowns_keep_dimensions_separate(monkeypatch, tmp_path):
    main = fresh_main(monkeypatch, tmp_path)
    main.init_db()
    conn = main.get_db()
    try:
        account_id = main.insert_and_get_id(
            conn,
            "INSERT INTO accounts (name, type, broker) VALUES (?,?,?)",
            ("Context Edge", "day_trading", "schwab"),
        )
        pnls = [100, 80, 60, 40, -20, 20, -40, -50, -60, -70]
        fallback_tags = []

        for i, pnl in enumerate(pnls):
            strong = i < 5
            group = f"edge-{i}"
            setup = "ORB" if strong else "CHASE"
            strategy = "Momentum" if strong else "Mean Reversion"
            source = "Scanner" if strong else "Social Media"
            emotion = "Focused" if strong else "Anxious"

            # Exercise structured-field fallback to explicit tags once per dimension.
            if i == 0:
                strategy = None
                fallback_tags.append((group, "strategy", "Momentum"))
            if i == 1:
                source = None
                fallback_tags.append((group, "source", "Scanner"))
            if i == 2:
                setup = None
                fallback_tags.append((group, "setup", "ORB"))
            if i == 3:
                emotion = None
                fallback_tags.append((group, "emotion", "Focused"))

            executions = __import__("json").dumps([
                {"action": "BOT", "qty": 1, "price": 100.0, "time": "09:30:00"},
                {"action": "SOLD", "qty": 1, "price": 101.0, "time": "09:35:00"},
            ])
            conn.execute(
                """INSERT INTO trades
                   (account_id, trade_group, date, ticker, instrument_type, side,
                    gross_pnl, net_pnl, commissions, executions, source, setup)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    account_id, group, "2026-09-25", "SPY", "STOCK", "LONG",
                    float(pnl), float(pnl), 0.0, executions, "imported", setup,
                ),
            )
            conn.execute(
                """INSERT INTO trade_analysis
                   (trade_group, ticker, date, strategy, idea_source, emotional_state, r_multiple)
                   VALUES (?,?,?,?,?,?,?)""",
                (
                    group, "SPY", "2026-09-25", strategy, source, emotion,
                    1.0 if pnl > 0 else -1.0,
                ),
            )

        for group, tag_type, tag_value in fallback_tags:
            conn.execute(
                """INSERT INTO trade_tags (trade_group, tag_type, tag_value, source)
                   VALUES (?,?,?,'manual')""",
                (group, tag_type, tag_value),
            )
        conn.commit()

        result = main.get_kpis(
            account_id=account_id,
            date_from="2026-09-01",
            date_to="2026-09-30",
            conn=conn,
        )
        dims = result["edge_dimensions"]

        assert dims["strategy"]["coverage_count"] == 10
        assert dims["source"]["coverage_count"] == 10
        assert dims["setup"]["coverage_count"] == 10
        assert dims["emotion"]["coverage_count"] == 10
        assert dims["strategy"]["min_sample"] == 5

        strategy = {r["label"]: r for r in dims["strategy"]["rows"]}
        setup = {r["label"]: r for r in dims["setup"]["rows"]}
        source = {r["label"]: r for r in dims["source"]["rows"]}
        emotion = {r["label"]: r for r in dims["emotion"]["rows"]}

        assert set(strategy) == {"Momentum", "Mean Reversion"}
        assert set(setup) == {"ORB", "CHASE"}
        assert set(source) == {"Scanner", "Social Media"}
        assert set(emotion) == {"Focused", "Anxious"}

        assert strategy["Momentum"]["win_rate"] == 80.0
        assert strategy["Mean Reversion"]["win_rate"] == 20.0
        assert strategy["Momentum"]["expectancy"] > 0
        assert strategy["Mean Reversion"]["expectancy"] < 0
        assert strategy["Momentum"]["confidence"] == "DEVELOPING"
        assert strategy["Momentum"]["metric_status"] == "VERIFIED"
        assert strategy["Momentum"]["context_status"] == "RECORDED"
        assert strategy["Momentum"]["evidence_status"] == "RECORDED"
        assert dims["strategy"]["best_win_rate"]["label"] == "Momentum"
        assert dims["strategy"]["strongest"]["label"] == "Momentum"
        assert dims["strategy"]["weakest"]["label"] == "Mean Reversion"

        # Setup is no longer silently substituted into Strategy.
        assert "ORB" not in strategy
        assert result["by_strategy"] == dims["strategy"]["rows"]
        assert result["by_source"] == dims["source"]["rows"]
        assert result["by_setup"] == dims["setup"]["rows"]
        assert result["by_emotion"] == dims["emotion"]["rows"]
    finally:
        conn.close()


def test_context_edge_confidence_excludes_low_samples_and_open_positions(monkeypatch, tmp_path):
    main = fresh_main(monkeypatch, tmp_path)
    main.init_db()
    conn = main.get_db()
    try:
        account_id = main.insert_and_get_id(
            conn,
            "INSERT INTO accounts (name, type, broker) VALUES (?,?,?)",
            ("Confidence Edge", "day_trading", "schwab"),
        )

        rows = [
            *[(f"lucky-{i}", "Lucky", 40.0 + i) for i in range(4)],
            ("repeat-0", "Repeatable", 30.0),
            ("repeat-1", "Repeatable", 25.0),
            ("repeat-2", "Repeatable", 20.0),
            ("repeat-3", "Repeatable", -10.0),
            ("repeat-4", "Repeatable", -5.0),
        ]
        for group, strategy, pnl in rows:
            executions = __import__("json").dumps([
                {"action": "BOT", "qty": 1, "price": 100.0, "time": "09:30:00"},
                {"action": "SOLD", "qty": 1, "price": 101.0, "time": "09:35:00"},
            ])
            conn.execute(
                """INSERT INTO trades
                   (account_id, trade_group, date, ticker, instrument_type, side,
                    gross_pnl, net_pnl, commissions, executions, source)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    account_id, group, "2026-09-25", "SPY", "STOCK", "LONG",
                    pnl, pnl, 0.0, executions, "imported",
                ),
            )
            conn.execute(
                """INSERT INTO trade_analysis (trade_group, ticker, date, strategy)
                   VALUES (?,?,?,?)""",
                (group, "SPY", "2026-09-25", strategy),
            )

        # Realized P&L can exist on a partially open position. Context analytics
        # must not treat that row as a completed sample.
        open_execs = __import__("json").dumps([
            {"action": "BOT", "qty": 2, "price": 100.0, "time": "10:00:00"},
            {"action": "SOLD", "qty": 1, "price": 105.0, "time": "10:05:00"},
        ])
        conn.execute(
            """INSERT INTO trades
               (account_id, trade_group, date, ticker, instrument_type, side,
                gross_pnl, net_pnl, commissions, executions, source)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (
                account_id, "open-lucky", "2026-09-25", "SPY", "STOCK", "LONG",
                500.0, 500.0, 0.0, open_execs, "imported",
            ),
        )
        conn.execute(
            """INSERT INTO trade_analysis (trade_group, ticker, date, strategy)
               VALUES (?,?,?,?)""",
            ("open-lucky", "SPY", "2026-09-25", "Lucky"),
        )
        conn.commit()

        result = main.get_kpis(
            account_id=account_id,
            date_from="2026-09-01",
            date_to="2026-09-30",
            conn=conn,
        )
        dim = result["edge_dimensions"]["strategy"]
        by_label = {row["label"]: row for row in dim["rows"]}

        assert dim["total_trades"] == 9
        assert dim["coverage_count"] == 9
        assert dim["min_sample"] == 5
        assert dim["reliable_min_sample"] == 15
        assert dim["metric_status"] == "VERIFIED"
        assert dim["context_status"] == "RECORDED"

        assert by_label["Lucky"]["count"] == 4
        assert by_label["Lucky"]["win_rate"] == 100.0
        assert by_label["Lucky"]["confidence"] == "LOW"
        assert by_label["Lucky"]["sample_qualified"] is False
        assert by_label["Lucky"]["evidence_status"] == "INSUFFICIENT DATA"

        assert by_label["Repeatable"]["count"] == 5
        assert by_label["Repeatable"]["confidence"] == "DEVELOPING"
        assert by_label["Repeatable"]["sample_qualified"] is True
        assert dim["best_win_rate"]["label"] == "Repeatable"
        assert dim["strongest"]["label"] == "Repeatable"
        assert dim["weakest"]["label"] == "Repeatable"

        assert main._edge_confidence(14) == "DEVELOPING"
        assert main._edge_confidence(15) == "RELIABLE"
    finally:
        conn.close()


def test_occ_option_symbol_uses_broker_contract_metadata(monkeypatch, tmp_path):
    main = fresh_main(monkeypatch, tmp_path)
    symbol = main._occ_option_symbol({
        "ticker": "TSM",
        "option_expiry": "2026-09-25",
        "option_strike": 452.5,
        "option_type": "CALL",
    })
    assert symbol == "TSM260925C00452500"


def test_excursion_kpis_ignore_legacy_underlying_option_efficiency(monkeypatch, tmp_path):
    main = fresh_main(monkeypatch, tmp_path)
    main.init_db()
    conn = main.get_db()
    try:
        account_id = main.insert_and_get_id(
            conn,
            "INSERT INTO accounts (name, type, broker) VALUES (?,?,?)",
            ("Excursion Test", "day_trading", "schwab"),
        )
        conn.execute(
            """INSERT INTO trades
               (account_id, trade_group, date, ticker, instrument_type, side,
                gross_pnl, net_pnl, commissions, executions, source,
                option_expiry, option_strike, option_type,
                mfe_pct, mae_pct, exit_efficiency)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                account_id, "legacy-option", "2026-09-25", "TSM", "OPTION", "LONG",
                65.94, 65.94, 0.0, "[]", "imported",
                "2026-09-25", 452.5, "CALL",
                0.0011, 0.1947, -12600.0,
            ),
        )
        conn.commit()

        result = main._excursion_kpis(
            conn,
            account_id=account_id,
            date_from="2026-09-19",
            date_to="2026-09-25",
        )

        assert result["exit_efficiency"] is None
        assert result["excursion_n"] == 0
        assert result["capture_n"] == 0
        assert result["capture_confidence"] == "LOW"
    finally:
        conn.close()


def test_excursion_kpis_expose_medians_to_detect_outlier_skew(monkeypatch, tmp_path):
    main = fresh_main(monkeypatch, tmp_path)
    main.init_db()
    conn = main.get_db()
    try:
        account_id = main.insert_and_get_id(
            conn,
            "INSERT INTO accounts (name, type, broker) VALUES (?,?,?)",
            ("Median Risk Test", "day_trading", "schwab"),
        )
        mfe_values = [8.0, 10.0, 12.0, 15.0, 409.42]
        mae_values = [20.0, 24.0, 26.0, 28.0, 30.0]
        for i, (mfe, mae) in enumerate(zip(mfe_values, mae_values)):
            conn.execute(
                """INSERT INTO trades
                   (account_id, trade_group, date, ticker, instrument_type, side,
                    gross_pnl, net_pnl, commissions, executions, source,
                    option_expiry, option_strike, option_type,
                    mfe_pct, mae_pct, exit_efficiency, excursion_basis)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    account_id, f"skew-{i}", f"2026-09-{21 + i:02d}", "QQQ", "OPTION", "LONG",
                    50.0, 50.0, 0.0, "[]", "imported",
                    "2026-09-25", 500.0, "CALL",
                    mfe, mae, 70.0, "option_premium_1m",
                ),
            )
        conn.execute(
            """UPDATE trades
               SET excursion_version=?, excursion_calculated_at=?
               WHERE account_id=?""",
            (main.EXCURSION_ENGINE_VERSION, "2026-09-27T12:00:00Z", account_id),
        )
        conn.commit()

        result = main._excursion_kpis(conn, account_id=account_id)

        assert result["avg_mfe"] > result["avg_mae"]
        assert result["median_mfe"] == 12.0
        assert result["median_mae"] == 26.0
        assert result["median_mfe"] < result["median_mae"]
    finally:
        conn.close()


def test_excursion_kpis_expose_winner_loser_actionable_separation(monkeypatch, tmp_path):
    main = fresh_main(monkeypatch, tmp_path)
    main.init_db()
    conn = main.get_db()
    try:
        account_id = main.insert_and_get_id(
            conn,
            "INSERT INTO accounts (name, type, broker) VALUES (?,?,?)",
            ("Actionable Excursion Test", "day_trading", "schwab"),
        )
        rows = [
            ("w1", 50.0, 20.0, 10.0),
            ("w2", 50.0, 25.0, 12.0),
            ("w3", 50.0, 30.0, 18.0),
            ("w4", 50.0, 35.0, 30.0),
            ("l1", -50.0, 1.0, 28.0),
            ("l2", -50.0, 2.0, 32.0),
            ("l3", -50.0, 4.0, 36.0),
            ("l4", -50.0, 12.0, 42.0),
        ]
        for i, (group, pnl, mfe, mae) in enumerate(rows):
            conn.execute(
                """INSERT INTO trades
                   (account_id, trade_group, date, ticker, instrument_type, side,
                    gross_pnl, net_pnl, commissions, executions, source,
                    option_expiry, option_strike, option_type,
                    mfe_pct, mae_pct, exit_efficiency, excursion_basis)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    account_id, group, f"2026-09-{20 + i:02d}", "QQQ", "OPTION", "LONG",
                    pnl, pnl, 0.0, "[]", "imported",
                    "2026-10-02", 500.0, "CALL",
                    mfe, mae, 70.0 if pnl > 0 else None, "option_premium_1m",
                ),
            )
        conn.execute(
            """UPDATE trades
               SET excursion_version=?, excursion_calculated_at=?
               WHERE account_id=?""",
            (main.EXCURSION_ENGINE_VERSION, "2026-09-27T12:00:00Z", account_id),
        )
        conn.commit()

        result = main._excursion_kpis(conn, account_id=account_id)

        assert result["winner_median_mae"] == 15.0
        assert result["loser_median_mae"] == 34.0
        assert result["winner_median_mfe"] == 27.5
        assert result["loser_median_mfe"] == 3.0
        assert result["winner_mae_le_20_pct"] == 75.0
        assert result["loser_mfe_le_5_pct"] == 75.0
        assert result["loser_mfe_le_10_pct"] == 75.0
        assert result["loser_mae_ge_25_pct"] == 100.0
    finally:
        conn.close()


def test_excursion_confidence_requires_multiple_days(monkeypatch, tmp_path):
    main = fresh_main(monkeypatch, tmp_path)
    main.init_db()
    conn = main.get_db()
    try:
        account_id = main.insert_and_get_id(
            conn,
            "INSERT INTO accounts (name, type, broker) VALUES (?,?,?)",
            ("Coverage Test", "day_trading", "schwab"),
        )
        for i in range(20):
            conn.execute(
                """INSERT INTO trades
                   (account_id, trade_group, date, ticker, instrument_type, side,
                    gross_pnl, net_pnl, commissions, executions, source,
                    option_expiry, option_strike, option_type,
                    mfe_pct, mae_pct, exit_efficiency, excursion_basis)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    account_id, f"one-day-{i}", "2026-09-25", "QCOM", "OPTION", "LONG",
                    50.0, 50.0, 0.0, "[]", "imported",
                    "2026-09-25", 200.0, "CALL",
                    10.0, 3.0, 70.0, "option_premium_1m",
                ),
            )
        conn.execute(
            """UPDATE trades
               SET excursion_version=?, excursion_calculated_at=?
               WHERE account_id=?""",
            (main.EXCURSION_ENGINE_VERSION, "2026-09-27T12:00:00Z", account_id),
        )
        conn.commit()

        one_day = main._excursion_kpis(conn, account_id=account_id)
        assert one_day["excursion_n"] == 20
        assert one_day["management_coverage_pct"] == 100.0
        assert one_day["excursion_days"] == 1
        assert one_day["excursion_confidence"] == "LOW"
        assert one_day["capture_confidence"] == "LOW"

        for i, date in enumerate(("2026-09-23", "2026-09-24"), start=20):
            for j in range(8):
                conn.execute(
                    """INSERT INTO trades
                       (account_id, trade_group, date, ticker, instrument_type, side,
                        gross_pnl, net_pnl, commissions, executions, source,
                        option_expiry, option_strike, option_type,
                        mfe_pct, mae_pct, exit_efficiency, excursion_basis)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        account_id, f"multi-{i}-{j}", date, "QCOM", "OPTION", "LONG",
                        50.0, 50.0, 0.0, "[]", "imported",
                        "2026-09-25", 200.0, "CALL",
                        10.0, 3.0, 70.0, "option_premium_1m",
                    ),
                )
        conn.commit()

        multi_day = main._excursion_kpis(conn, account_id=account_id)
        assert multi_day["excursion_days"] == 3
        assert multi_day["excursion_confidence"] == "RELIABLE"
        assert multi_day["capture_confidence"] == "RELIABLE"
        assert multi_day["management_primary_source"] == "broker_csv"
    finally:
        conn.close()


def test_trade_list_surfaces_option_journal_and_derives_r_only_from_explicit_risk(monkeypatch, tmp_path):
    main = fresh_main(monkeypatch, tmp_path)
    main.init_db()
    conn = main.get_db()
    try:
        account_id = main.insert_and_get_id(
            conn,
            "INSERT INTO accounts (name, type, broker) VALUES (?,?,?)",
            ("Review Test", "day_trading", "schwab"),
        )
        executions = __import__("json").dumps([
            {"date": "2026-09-25", "time": "09:22:00", "action": "BOT", "qty": 5, "price": 1.08},
            {"date": "2026-09-25", "time": "09:41:00", "action": "SOLD", "qty": 5, "price": 1.50},
        ])
        conn.execute(
            """INSERT INTO trades
               (account_id, trade_group, date, ticker, instrument_type, side,
                gross_pnl, net_pnl, commissions, executions, source)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (
                account_id, "review-option", "2026-09-25", "QCOM", "OPTION", "LONG",
                205.89, 205.89, 0.0, executions, "imported",
            ),
        )
        conn.execute(
            """INSERT INTO trade_analysis
               (trade_group, ticker, date, strategy, risk_per_trade,
                entry_reason, exit_reason, mistakes, emotional_state)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (
                "review-option", "QCOM", "2026-09-25",
                "10m 8 EMA Retest + VWAP Reclaim", 100.0,
                "Confirmed entry.", "Trimmed into strength.", "Held first trim too long.", "Focused",
            ),
        )
        conn.commit()

        rows = main.list_trades(
            account_id=account_id,
            instrument_type=None,
            date_from=None,
            date_to=None,
            ticker=None,
            open_only=False,
            closed_only=False,
            sort_by=None,
            limit=None,
            conn=conn,
        )

        assert len(rows) == 1
        row = rows[0]
        assert row["instrument_type"] == "OPTION"
        assert row["strategy"] == "10m 8 EMA Retest + VWAP Reclaim"
        assert row["emotional_state"] == "Focused"
        assert row["mistakes"] == "Held first trim too long."
        assert row["risk_per_trade"] == 100.0
        assert row["realized_r"] == 2.0589
    finally:
        conn.close()


def test_kpis_expose_green_and_red_day_counts(monkeypatch, tmp_path):
    main = fresh_main(monkeypatch, tmp_path)
    main.init_db()
    conn = main.get_db()
    try:
        account_id = main.insert_and_get_id(
            conn,
            "INSERT INTO accounts (name, type, broker) VALUES (?,?,?)",
            ("Day Win Test", "day_trading", "schwab"),
        )
        rows = [
            ("g1", "2026-09-21", 100.0),
            ("g2", "2026-09-22", 50.0),
            ("r1", "2026-09-23", -25.0),
        ]
        for group, date, pnl in rows:
            conn.execute(
                """INSERT INTO trades
                   (account_id, trade_group, date, ticker, instrument_type, side,
                    gross_pnl, net_pnl, commissions, executions, source)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    account_id, group, date, "SPY", "OPTION", "LONG",
                    pnl, pnl, 0.0, "[]", "imported",
                ),
            )
        conn.commit()

        result = main.get_kpis(account_id=account_id, date_from=None, date_to=None, conn=conn)

        assert result["trading_days"] == 3
        assert result["positive_days"] == 2
        assert result["negative_days"] == 1
        assert result["day_win_rate"] == 66.7
    finally:
        conn.close()


def test_tag_library_seeds_overhead_resistance_mistake(monkeypatch, tmp_path):
    main = fresh_main(monkeypatch, tmp_path)
    main.init_db()
    conn = main.get_db()
    try:
        main.init_library_tables(conn)
        row = conn.execute(
            """SELECT description FROM library_items
               WHERE kind='tag' AND tag_type='mistake' AND name=?""",
            ("Entered Too Close to Resistance",),
        ).fetchone()
        assert row is not None
        assert "higher-priority resistance" in row["description"]
        assert "PDH retest" in row["description"]
        assert "PMH" in row["description"]
    finally:
        conn.close()


def test_manual_trade_tags_reject_duplicates_and_unknown_types(monkeypatch, tmp_path):
    main = fresh_main(monkeypatch, tmp_path)
    main.init_db()
    conn = main.get_db()
    try:
        account_id = main.insert_and_get_id(
            conn,
            "INSERT INTO accounts (name, type, broker) VALUES (?,?,?)",
            ("Tag Test", "day_trading", "schwab"),
        )
        conn.execute(
            """INSERT INTO trades
               (account_id, trade_group, date, ticker, instrument_type, side,
                gross_pnl, net_pnl, commissions, executions, source)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (
                account_id, "nvda-tag-test", "2026-09-25", "NVDA", "OPTION", "LONG",
                100.0, 100.0, 0.0, "[]", "imported",
            ),
        )
        conn.commit()

        created = main.add_trade_tag(
            "nvda-tag-test",
            main.TagCreate(tag_type="mistake", tag_value="Entered Too Close to Resistance"),
            conn,
        )
        assert created["tag_type"] == "mistake"
        assert created["tag_value"] == "Entered Too Close to Resistance"

        with pytest.raises(main.HTTPException) as duplicate:
            main.add_trade_tag(
                "nvda-tag-test",
                main.TagCreate(tag_type="mistake", tag_value="Entered Too Close to Resistance"),
                conn,
            )
        assert duplicate.value.status_code == 409

        with pytest.raises(main.HTTPException) as unknown:
            main.add_trade_tag(
                "nvda-tag-test",
                main.TagCreate(tag_type="random", tag_value="Something"),
                conn,
            )
        assert unknown.value.status_code == 400
    finally:
        conn.close()


def test_wmt_multifill_pl_percent_reconciles_to_schwab_fills(monkeypatch, tmp_path):
    main = fresh_main(monkeypatch, tmp_path)
    trade = {
        "side": "LONG",
        "instrument_type": "OPTION",
        "ticker": "WMT",
        "net_pnl": -172.10,
        "executions": [
            {"action": "BOT", "qty": 2, "price": 3.35},
            {"action": "BOT", "qty": 1, "price": 3.27},
            {"action": "SOLD", "qty": 3, "price": 2.76},
        ],
    }
    assert main._trade_pl_percent(trade) == -17.26


def test_manual_option_pnl_uses_contract_multiplier(monkeypatch, tmp_path):
    main = fresh_main(monkeypatch, tmp_path)
    gross, net = main.compute_manual_pnl(
        "LONG", 2.00, 2.50, 3, 3.00, "OPTION", "SPY"
    )
    assert gross == 150.0
    assert net == 147.0


def test_kpis_exclude_partially_open_positions_from_every_core_metric(monkeypatch, tmp_path):
    main = fresh_main(monkeypatch, tmp_path)
    main.init_db()
    conn = main.get_db()
    try:
        account_id = main.insert_and_get_id(
            conn,
            "INSERT INTO accounts (name, type, broker) VALUES (?,?,?)",
            ("Closed Only", "day_trading", "schwab"),
        )
        closed = __import__("json").dumps([
            {"action": "BOT", "qty": 1, "price": 1.0, "time": "08:30:00"},
            {"action": "SOLD", "qty": 1, "price": 2.0, "time": "08:35:00"},
        ])
        partial = __import__("json").dumps([
            {"action": "BOT", "qty": 2, "price": 1.0, "time": "09:00:00"},
            {"action": "SOLD", "qty": 1, "price": 2.0, "time": "09:05:00"},
        ])
        conn.execute(
            """INSERT INTO trades
               (account_id, trade_group, date, ticker, instrument_type, side,
                gross_pnl, net_pnl, commissions, executions, source)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (account_id, "closed", "2026-09-01", "SPY", "OPTION", "LONG",
             100.0, 100.0, 0.0, closed, "imported"),
        )
        conn.execute(
            """INSERT INTO trades
               (account_id, trade_group, date, ticker, instrument_type, side,
                gross_pnl, net_pnl, commissions, executions, source)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (account_id, "partial", "2026-09-01", "QQQ", "OPTION", "LONG",
             100.0, 100.0, 0.0, partial, "imported"),
        )
        conn.commit()

        result = main.get_kpis(account_id=account_id, date_from=None, date_to=None, conn=conn)
        assert result["total_trades"] == 1
        assert result["total_net_pnl"] == 100.0
        assert result["win_rate"] == 100.0
        assert result["expectancy"] == 100.0
        assert result["trading_days"] == 1
    finally:
        conn.close()


def test_yearly_day_win_rate_uses_net_day_result_not_any_winner(monkeypatch, tmp_path):
    main = fresh_main(monkeypatch, tmp_path)
    main.init_db()
    conn = main.get_db()
    try:
        account_id = main.insert_and_get_id(
            conn,
            "INSERT INTO accounts (name, type, broker) VALUES (?,?,?)",
            ("Yearly", "day_trading", "schwab"),
        )
        def add(group, date, pnl, ticker):
            execs = __import__("json").dumps([
                {"action": "BOT", "qty": 1, "price": 1.0, "time": "08:30:00"},
                {"action": "SOLD", "qty": 1, "price": 1.1, "time": "08:35:00"},
            ])
            conn.execute(
                """INSERT INTO trades
                   (account_id, trade_group, date, ticker, instrument_type, side,
                    gross_pnl, net_pnl, commissions, executions, source)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (account_id, group, date, ticker, "OPTION", "LONG", pnl, pnl, 0.0, execs, "imported"),
            )
        # Sep 1 contains a winner but is a red day overall: +100 - 200 = -100.
        add("a", "2026-09-01", 100.0, "SPY")
        add("b", "2026-09-01", -200.0, "QQQ")
        add("c", "2026-09-02", 50.0, "IWM")
        conn.commit()

        september = main.get_yearly_kpis(year=2026, account_id=account_id, conn=conn)[8]
        assert september["trading_days"] == 2
        assert september["day_win_rate"] == 50.0
    finally:
        conn.close()


def test_stale_excursion_requires_current_engine_version(monkeypatch, tmp_path):
    main = fresh_main(monkeypatch, tmp_path)
    trade = {
        "instrument_type": "OPTION",
        "side": "LONG",
        "net_pnl": 10.0,
        "mfe_pct": 20.0,
        "mae_pct": 5.0,
        "exit_efficiency": 50.0,
        "excursion_basis": "option_premium_1m",
        "excursion_calculated_at": "2026-09-27T12:00:00Z",
        "excursion_version": "old",
        "executions": [
            {"action": "BOT", "qty": 1, "price": 1.0},
            {"action": "SOLD", "qty": 1, "price": 1.1},
        ],
    }
    assert main._excursion_is_stale(trade) is True
    trade["excursion_version"] = main.EXCURSION_ENGINE_VERSION
    assert main._excursion_is_stale(trade) is False


def test_losing_trade_with_exit_capture_is_always_stale(monkeypatch, tmp_path):
    main = fresh_main(monkeypatch, tmp_path)
    trade = {
        "instrument_type": "OPTION",
        "side": "LONG",
        "net_pnl": -10.0,
        "mfe_pct": 2.0,
        "mae_pct": 10.0,
        "exit_efficiency": 76.9,
        "excursion_basis": "option_premium_1m",
        "excursion_calculated_at": "2026-09-27T12:00:00Z",
        "excursion_version": main.EXCURSION_ENGINE_VERSION,
        "executions": [
            {"action": "BOT", "qty": 1, "price": 1.0},
            {"action": "SOLD", "qty": 1, "price": 0.9},
        ],
    }
    assert main._excursion_is_stale(trade) is True
