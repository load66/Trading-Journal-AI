"""Importing a statement on top of trades already stored for the same day.

    cd backend && python -m pytest tests -q

A second import used to number its new position cycles from _1 again and overwrite the
stored trade with that name, so a whole round trip disappeared.
"""
import json
import os
import sqlite3
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

HEAD = "Account Statement\n\nCash Balance\nDATE,TIME,TYPE,REF #,DESCRIPTION,Misc Fees,Commissions & Fees,AMOUNT,BALANCE\n"
ROWS = [
    '9/15/26,09:46:16,TRD,="1",BOT +400 TSLA @250.00,,,"-100,000.00","1"',
    '9/15/26,09:50:48,TRD,="2",SOLD -100 TSLA @250.50,-0.75,,"25,050.00","1"',
    '9/15/26,09:57:47,TRD,="3",SOLD -300 TSLA @249.00,-2.25,,"74,700.00","1"',
    '9/15/26,09:59:52,TRD,="4",BOT +300 CRWD @200.00,,,"-60,000.00","1"',
    '9/15/26,10:14:04,TRD,="5",SOLD -100 CRWD @201.00,-0.50,,"20,100.00","1"',
    '9/15/26,10:19:49,TRD,="6",SOLD -200 CRWD @199.50,-1.00,,"39,900.00","1"',
    '9/15/26,10:55:12,TRD,="7",BOT +200 TSLA @248.00,,,"-49,600.00","1"',
    '9/15/26,11:00:35,TRD,="8",SOLD -200 TSLA @249.00,-1.50,,"49,800.00","1"',
]


def statement(rows):
    return HEAD + "\n".join(rows) + "\n"


@pytest.fixture
def client(tmp_path, monkeypatch):
    db = tmp_path / "journal.db"
    monkeypatch.setenv("DATABASE_PATH", str(db))
    for name in ("database", "main"):
        sys.modules.pop(name, None)
    import database
    database.DB_PATH = str(db)
    import main
    monkeypatch.setattr(main, "_classify_dates", lambda conn, dates: 0, raising=False)
    from fastapi.testclient import TestClient
    with TestClient(main.app) as c:
        c.db = str(db)
        yield c


def post(client, content):
    r = client.post("/api/import-csv", data={"account_id": "1"},
                    files={"file": ("statement.csv", content.encode(), "text/csv")})
    assert r.status_code == 200, r.text
    return r.json()


def stored(client):
    conn = sqlite3.connect(client.db)
    rows = conn.execute("SELECT trade_group, net_pnl, executions FROM trades ORDER BY trade_group").fetchall()
    return {g: (pnl, len(json.loads(ex))) for g, pnl, ex in rows}


def test_second_import_adds_to_the_day_instead_of_overwriting(client):
    conn = sqlite3.connect(client.db)
    if not conn.execute("SELECT 1 FROM accounts WHERE id=1").fetchone():
        conn.execute("INSERT INTO accounts (id, name, type) VALUES (1, 'Day', 'day_trading')")
        conn.commit()

    # a statement taken mid-morning: the first TSLA trip and the CRWD entry only
    post(client, statement(ROWS[:4]))
    conn.execute("INSERT INTO trade_analysis (trade_group, ticker, date, strategy) VALUES "
                 "('9/15/26_TSLA_STOCK_1', 'TSLA', '2026-09-15', 'Morning note')")
    conn.commit()

    # the full day's statement afterwards
    post(client, statement(ROWS))
    trades = stored(client)
    assert set(trades) == {"9/15/26_TSLA_STOCK_1", "9/15/26_TSLA_STOCK_2", "9/15/26_CRWD_STOCK_1"}
    assert trades["9/15/26_TSLA_STOCK_1"] == (-253.0, 3)
    assert trades["9/15/26_TSLA_STOCK_2"][1] == 2
    assert trades["9/15/26_CRWD_STOCK_1"][1] == 3
    note = conn.execute("SELECT trade_group FROM trade_analysis WHERE strategy='Morning note'").fetchone()
    assert note[0] == "9/15/26_TSLA_STOCK_1"

    # the same statement again changes nothing
    before = stored(client)
    result = post(client, statement(ROWS))
    assert result["imported"] == 0
    assert stored(client) == before


GENERIC = "date,time,symbol,side,quantity,price,commission,timezone\n"
GROWS = [
    "2026-09-15,09:46:16,TSLA,BUY,400,250.00,0,America/Chicago",
    "2026-09-15,09:57:47,TSLA,SELL,400,249.00,3.00,America/Chicago",
    "2026-09-15,10:55:12,TSLA,BUY,200,248.00,0,America/Chicago",
    "2026-09-15,11:00:35,TSLA,SELL,200,249.00,1.50,America/Chicago",
]


def test_generic_template_reimport_keeps_iso_named_trades(client):
    conn = sqlite3.connect(client.db)
    if not conn.execute("SELECT 1 FROM accounts WHERE id=1").fetchone():
        conn.execute("INSERT INTO accounts (id, name, type) VALUES (1, 'Day', 'day_trading')")
        conn.commit()
    r = client.post("/api/import-csv", data={"account_id": "1", "broker": "generic"},
                    files={"file": ("fills.csv", (GENERIC + "\n".join(GROWS[:2]) + "\n").encode(), "text/csv")})
    assert r.status_code == 200, r.text
    r = client.post("/api/import-csv", data={"account_id": "1", "broker": "generic"},
                    files={"file": ("fills.csv", (GENERIC + "\n".join(GROWS) + "\n").encode(), "text/csv")})
    assert r.status_code == 200, r.text
    trades = stored(client)
    assert set(trades) == {"2026-09-15_TSLA_STOCK_1", "2026-09-15_TSLA_STOCK_2"}
    assert trades["2026-09-15_TSLA_STOCK_1"] == (-403.0, 2)


def test_reimport_preserves_legitimate_identical_split_fills(client):
    """Identical minute/price/qty fills are a multiset, not one duplicate row."""
    conn = sqlite3.connect(client.db)
    if not conn.execute("SELECT 1 FROM accounts WHERE id=1").fetchone():
        conn.execute("INSERT INTO accounts (id, name, type) VALUES (1, 'Day', 'day_trading')")
        conn.commit()

    rows = [
        "2026-09-25,10:11:00,QCOM,BUY,5,160.00,0,America/Chicago",
        "2026-09-25,10:11:00,QCOM,BUY,5,160.00,0,America/Chicago",
        "2026-09-25,10:15:00,QCOM,SELL,5,161.00,0,America/Chicago",
        "2026-09-25,10:15:00,QCOM,SELL,5,161.00,0,America/Chicago",
    ]
    payload = GENERIC + "\n".join(rows) + "\n"

    first = client.post(
        "/api/import-csv",
        data={"account_id": "1", "broker": "generic"},
        files={"file": ("fills.csv", payload.encode(), "text/csv")},
    )
    assert first.status_code == 200, first.text

    trades = stored(client)
    assert trades["2026-09-25_QCOM_STOCK_1"] == (10.0, 4)

    before = stored(client)
    second = client.post(
        "/api/import-csv",
        data={"account_id": "1", "broker": "generic"},
        files={"file": ("fills.csv", payload.encode(), "text/csv")},
    )
    assert second.status_code == 200, second.text
    assert second.json()["imported"] == 0
    assert second.json()["skipped"] == 4
    assert stored(client) == before


def test_incremental_reimport_adds_only_excess_identical_fill_occurrences(client):
    """A newer export may legitimately contain one more identical split fill."""
    conn = sqlite3.connect(client.db)
    if not conn.execute("SELECT 1 FROM accounts WHERE id=1").fetchone():
        conn.execute("INSERT INTO accounts (id, name, type) VALUES (1, 'Day', 'day_trading')")
        conn.commit()

    first_rows = [
        "2026-09-25,10:11:00,QCOM,BUY,5,160.00,0,America/Chicago",
        "2026-09-25,10:11:00,QCOM,BUY,5,160.00,0,America/Chicago",
        "2026-09-25,10:15:00,QCOM,SELL,5,161.00,0,America/Chicago",
        "2026-09-25,10:15:00,QCOM,SELL,5,161.00,0,America/Chicago",
    ]
    full_rows = first_rows + [
        "2026-09-25,10:11:00,QCOM,BUY,5,160.00,0,America/Chicago",
        "2026-09-25,10:15:00,QCOM,SELL,5,161.00,0,America/Chicago",
    ]

    for rows in (first_rows, full_rows):
        response = client.post(
            "/api/import-csv",
            data={"account_id": "1", "broker": "generic"},
            files={"file": ("fills.csv", (GENERIC + "\n".join(rows) + "\n").encode(), "text/csv")},
        )
        assert response.status_code == 200, response.text

    trades = stored(client)
    assert trades["2026-09-25_QCOM_STOCK_1"] == (15.0, 6)
