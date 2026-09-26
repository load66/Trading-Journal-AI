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


def test_kpis_strategy_breakdown_respects_date_range(monkeypatch, tmp_path):
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
            ("old", "2026-08-20", "OLD_SETUP", 50.0),
            ("new", "2026-09-20", "BREAKOUT", 100.0),
        ]
        for group, date, setup, pnl in rows:
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
        conn.commit()

        result = main.get_kpis(
            account_id=account_id,
            date_from="2026-09-01",
            date_to="2026-09-30",
            conn=conn,
        )

        assert result["total_trades"] == 1
        assert [row["strategy"] for row in result["by_strategy"]] == ["BREAKOUT"]
        assert result["by_strategy"][0]["count"] == 1
    finally:
        conn.close()
