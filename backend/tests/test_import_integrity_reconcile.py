import json
import sqlite3
import sys
from pathlib import Path

from fastapi.testclient import TestClient

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from csv_parser import (
    detect_broker,
    execution_fingerprint,
    get_existing_fingerprints,
    parse_broker_csv,
)

QCOM_CSV = """index,Date, Type, Description, Ref Num, Misc Fees, Commissions, Amount, Balance
0,9/25/26 10:11 AM,TRD,SOLD -1 QCOM 100 (Weeklys) 25 SEP 26 202.5 CALL @2.11 CBOE,1008066079460,-0.01,-0.5,$211.00,$2220.13
1,9/25/26 10:07 AM,TRD,SOLD -1 QCOM 100 (Weeklys) 25 SEP 26 202.5 CALL @1.56 CBOE,1008066079241,-0.01,-0.5,$156.00,$2009.64
2,9/25/26 10:03 AM,TRD,SOLD -1 QCOM 100 (Weeklys) 25 SEP 26 202.5 CALL @1.24 CBOE,1008066079018,-0.03,-0.5,$124.00,$1854.15
3,9/25/26 10:03 AM,TRD,SOLD -1 QCOM 100 (Weeklys) 25 SEP 26 202.5 CALL @1.23 CBOE,1008066079018,-0.01,-0.5,$123.00,$1730.68
4,9/25/26 9:54 AM,TRD,SOLD -1 QCOM 100 (Weeklys) 25 SEP 26 202.5 CALL @.94 CBOE,1008066078454,-0.01,-0.5,$94.00,$1608.19
5,9/25/26 9:54 AM,TRD,SOLD -1 QCOM 100 (Weeklys) 25 SEP 26 202.5 CALL @.95 CBOE,1008066078448,-0.01,-0.5,$95.00,$1514.70
6,9/25/26 9:48 AM,TRD,SOLD -2 QCOM 100 (Weeklys) 25 SEP 26 202.5 CALL @.75 CBOE,1008066078125,-0.03,-1.0,$150.00,$1420.21
7,9/25/26 9:48 AM,TRD,SOLD -2 QCOM 100 (Weeklys) 25 SEP 26 202.5 CALL @.85 MIAX,1008066078118,-0.03,-1.0,$170.00,$1271.24
8,9/25/26 9:47 AM,TRD,BOT +5 QCOM 100 (Weeklys) 25 SEP 26 202.5 CALL @.67 CBOE,1008066078058,-0.06,-2.5,($335.00),$1102.27
9,9/25/26 9:47 AM,TRD,BOT +5 QCOM 100 (Weeklys) 25 SEP 26 202.5 CALL @.67 CBOE,1008066078058,-0.06,-2.5,($335.00),$1439.83
"""


def test_detects_schwab_transaction_history():
    assert detect_broker(QCOM_CSV) == "schwab_transactions"


def test_qcom_trim_sequence_and_runner_are_preserved():
    trades, skipped = parse_broker_csv(QCOM_CSV, "auto", account_id=1, conn=None)
    assert skipped == 0
    assert len(trades) == 1
    trade = trades[0]
    assert trade["ticker"] == "QCOM"
    assert trade["option_strike"] == 202.5
    assert trade["gross_pnl"] == 453.0
    assert trade["net_pnl"] == 442.74

    execs = json.loads(trade["executions"])
    assert len(execs) == 10
    assert sum(e["qty"] for e in execs if e["action"] == "BOT") == 10
    assert sum(e["qty"] for e in execs if e["action"] == "SOLD") == 10
    assert execs[-1]["price"] == 2.11
    assert execs[-1]["source_ref"] == "1008066079460"
    assert execs[-1]["timestamp_precision"] == "minute"


def test_execution_identity_matches_minute_and_second_exports_for_same_contract():
    base = {
        "iso_date": "2026-09-25",
        "ticker": "QCOM",
        "instrument_type": "OPTION",
        "option_expiry": "2026-09-25",
        "option_strike": 202.5,
        "option_type": "CALL",
        "action": "BOT",
        "qty": 5,
        "price": 0.67,
    }
    assert execution_fingerprint({**base, "time": "09:47:00"}) == execution_fingerprint(
        {**base, "time": "09:47:38"}
    )


def test_execution_identity_separates_different_option_contracts():
    base = {
        "iso_date": "2026-09-25",
        "time": "09:47:00",
        "ticker": "QCOM",
        "instrument_type": "OPTION",
        "option_expiry": "2026-09-25",
        "option_type": "CALL",
        "action": "BOT",
        "qty": 5,
        "price": 0.67,
    }
    assert execution_fingerprint({**base, "option_strike": 202.5}) != execution_fingerprint(
        {**base, "option_strike": 200.0}
    )


def test_existing_fingerprints_preserve_identical_split_fill_multiplicity():
    conn = sqlite3.connect(":memory:")
    conn.execute(
        """CREATE TABLE trades (
               account_id INTEGER, ticker TEXT, instrument_type TEXT,
               option_expiry TEXT, option_strike REAL, option_type TEXT,
               executions TEXT
           )"""
    )
    execs = [
        {"date": "2026-09-25", "time": "09:47:31", "action": "BOT", "qty": 5, "price": 0.67},
        {"date": "2026-09-25", "time": "09:47:44", "action": "BOT", "qty": 5, "price": 0.67},
    ]
    conn.execute(
        "INSERT INTO trades VALUES (?,?,?,?,?,?,?)",
        (1, "QCOM", "OPTION", "2026-09-25", 202.5, "CALL", json.dumps(execs)),
    )
    counts = get_existing_fingerprints(conn, 1)
    probe = {
        "iso_date": "2026-09-25",
        "time": "09:47:00",
        "ticker": "QCOM",
        "instrument_type": "OPTION",
        "option_expiry": "2026-09-25",
        "option_strike": 202.5,
        "option_type": "CALL",
        "action": "BOT",
        "qty": 5,
        "price": 0.67,
    }
    assert counts[execution_fingerprint(probe)] == 2


def _fresh_client(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "journal.db"))
    monkeypatch.setenv("UPLOAD_DIR", str(tmp_path / "uploads"))
    monkeypatch.setenv("AUTH_REQUIRED", "false")
    for name in ("auth", "database", "main"):
        sys.modules.pop(name, None)
    import main
    with TestClient(main.app) as client:
        yield client, main


def test_authoritative_reconcile_rebuilds_imported_trade_without_duplicates(monkeypatch, tmp_path):
    for client, main in _fresh_client(monkeypatch, tmp_path):
        conn = main.get_db()
        conn.execute("INSERT INTO accounts (id, name, type) VALUES (1, 'Day', 'day_trading')")
        old_execs = [
            {"date": "2026-09-25", "time": "09:47:31", "action": "BOT", "qty": 5, "price": 0.67, "commission": 2.56},
            {"date": "2026-09-25", "time": "09:47:44", "action": "BOT", "qty": 5, "price": 0.67, "commission": 2.56},
            {"date": "2026-09-25", "time": "10:11:22", "action": "SOLD", "qty": 10, "price": 1.123, "commission": 5.14},
        ]
        conn.execute(
            """INSERT INTO trades
               (account_id, trade_group, date, ticker, instrument_type, side,
                gross_pnl, net_pnl, commissions, executions,
                option_expiry, option_strike, option_type, source)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (1, "9/25/26_QCOM_OPTION_2026-09-25_202.5_CALL_1", "2026-09-25",
             "QCOM", "OPTION", "LONG", 453.0, 442.74, 10.26, json.dumps(old_execs),
             "2026-09-25", 202.5, "CALL", "imported"),
        )
        conn.commit()

        response = client.post(
            "/api/import-csv",
            data={"account_id": "1", "broker": "auto", "reconcile": "true"},
            files={"file": ("qcom.csv", QCOM_CSV.encode(), "text/csv")},
        )
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["reconcile"] is True
        assert payload["reconciled"] == 1

        rows = conn.execute(
            "SELECT ticker, net_pnl, executions FROM trades WHERE account_id=1 AND ticker='QCOM'"
        ).fetchall()
        assert len(rows) == 1
        assert round(rows[0][1], 2) == 442.74
        rebuilt = json.loads(rows[0][2])
        assert len(rebuilt) == 10
        assert rebuilt[-1]["price"] == 2.11
