import asyncio
import sys
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

    assert [r["start_minute"] for r in rows] == [510, 540]
    assert rows[0]["label"] == "8:30 AM–9:00 AM"
    assert rows[0]["count"] == 2
    assert rows[0]["net_pnl"] == 50.0
    assert rows[0]["win_rate"] == 50.0
    assert rows[0]["expectancy"] == 25.0
    assert rows[0]["avg_pl_pct"] == 5.0
    assert rows[1]["label"] == "9:00 AM–9:30 AM"
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
        assert dims["strategy"]["min_sample"] == 3

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
