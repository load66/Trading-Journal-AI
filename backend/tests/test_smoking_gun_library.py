import json
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

import database  # noqa: E402
from config import Settings  # noqa: E402


def _settings(db_path: str):
    return Settings.from_env({
        "APP_ENV": "test",
        "AUTH_MODE": "disabled",
        "DATABASE_MODE": "sqlite",
        "DATABASE_PATH": db_path,
        "STORAGE_MODE": "local",
    })


def test_sqlite_init_creates_smoking_gun_reports_table(tmp_path):
    db_path = tmp_path / "journal.db"
    test_settings = _settings(str(db_path))
    database.init_db(test_settings)

    conn = database.get_db(test_settings)
    columns = {
        row["name"]
        for row in conn.execute("PRAGMA table_info(smoking_gun_reports)").fetchall()
    }
    expected = {
        "id", "account_id", "title", "date_from", "date_to", "generated_at",
        "report_version", "analytics_engine_version", "behavior_version",
        "analysis_provider", "analysis_model", "trade_count", "gross_pnl",
        "net_pnl", "primary_edge", "primary_leak", "data_fingerprint",
        "filters_json", "source_metrics_json", "diagnosis_json",
        "action_plan_json", "export_manifest_json", "status",
    }
    assert expected <= columns

    unique_indexes = []
    for idx in conn.execute("PRAGMA index_list(smoking_gun_reports)").fetchall():
        if idx["unique"]:
            cols = [
                row["name"]
                for row in conn.execute(
                    f"PRAGMA index_info({idx['name']})"
                ).fetchall()
            ]
            unique_indexes.append(tuple(cols))

    assert (
        "account_id", "date_from", "date_to", "data_fingerprint", "report_version"
    ) in unique_indexes
    conn.close()


def _source_trade(group, net_pnl=100.0, executions=None):
    if executions is None:
        executions = [
            {"date": "2026-09-01", "time": "09:30:00", "action": "BOT", "qty": 1, "price": 2.0},
            {"date": "2026-09-01", "time": "09:35:00", "action": "SOLD", "qty": 1, "price": 3.0},
        ]
    return {
        "id": 1 if group == "a" else 2,
        "account_id": 1,
        "trade_group": group,
        "date": "2026-09-01",
        "ticker": "SPY" if group == "a" else "QQQ",
        "instrument_type": "OPTION",
        "side": "LONG",
        "gross_pnl": net_pnl,
        "net_pnl": net_pnl,
        "commissions": 0.0,
        "executions": json.dumps(executions),
        "option_expiry": "2026-09-01",
        "option_strike": 600.0,
        "option_type": "CALL",
        "source": "imported",
    }


def test_source_fingerprint_is_order_independent_and_content_sensitive():
    from smoking_gun_library import build_source_fingerprint

    a = [_source_trade("a", 100), _source_trade("b", -50)]
    b = list(reversed(a))

    original = build_source_fingerprint(
        a, 1, "2026-09-01", "2026-09-30", {}
    )
    assert original == build_source_fingerprint(
        b, 1, "2026-09-01", "2026-09-30", {}
    )

    changed = [dict(a[0]), dict(a[1])]
    changed[0]["net_pnl"] = 101
    assert original != build_source_fingerprint(
        changed, 1, "2026-09-01", "2026-09-30", {}
    )


def test_source_fingerprint_canonicalizes_execution_json_key_order():
    from smoking_gun_library import build_source_fingerprint

    left_execs = [
        {"date": "2026-09-01", "time": "09:30:00", "action": "BOT", "qty": 1, "price": 2.0},
        {"date": "2026-09-01", "time": "09:35:00", "action": "SOLD", "qty": 1, "price": 3.0},
    ]
    right_execs = [
        {"price": 2.0, "qty": 1, "action": "BOT", "time": "09:30:00", "date": "2026-09-01"},
        {"qty": 1, "price": 3.0, "date": "2026-09-01", "action": "SOLD", "time": "09:35:00"},
    ]

    left = [_source_trade("a", executions=left_execs)]
    right = [_source_trade("a", executions=right_execs)]

    assert build_source_fingerprint(
        left, 1, "2026-09-01", "2026-09-30", {"tickers": ["SPY"]}
    ) == build_source_fingerprint(
        right, 1, "2026-09-01", "2026-09-30", {"tickers": ["SPY"]}
    )


def test_source_fingerprint_includes_population_filters():
    from smoking_gun_library import build_source_fingerprint

    rows = [_source_trade("a")]
    all_data = build_source_fingerprint(rows, 1, "2026-09-01", "2026-09-30", {})
    filtered = build_source_fingerprint(
        rows, 1, "2026-09-01", "2026-09-30", {"tickers": ["SPY"]}
    )

    assert all_data != filtered


def _initialized_conn(tmp_path):
    db_path = tmp_path / "journal-library.db"
    test_settings = _settings(str(db_path))
    database.init_db(test_settings)
    conn = database.get_db(test_settings)
    conn.execute(
        "INSERT INTO accounts (id, name, type) VALUES (?, ?, ?)",
        (1, "Primary", "day_trading"),
    )
    conn.commit()
    return conn


def _saved_payload(fingerprint="fp-1", title="September Smoking Gun"):
    return {
        "account_id": 1,
        "title": title,
        "date_from": "2026-09-01",
        "date_to": "2026-09-30",
        "report_version": "1",
        "analytics_engine_version": "2026.09.26.1",
        "behavior_version": "2026.09.26.1",
        "analysis_provider": "openai",
        "analysis_model": "gpt-test",
        "trade_count": 2,
        "gross_pnl": 75.0,
        "net_pnl": 50.0,
        "primary_edge": "Patience",
        "primary_leak": "Averaging down",
        "data_fingerprint": fingerprint,
        "filters": {"tickers": ["SPY", "QQQ"]},
        "source_metrics": {"scoreboard": {"net_pnl": 50.0}, "trade_ledger": []},
        "diagnosis": {"headline": "Protect the patient edge."},
        "action_plan": [{"rank": 1, "rule": "No averaging down"}],
        "export_manifest": {"format_version": 1},
        "status": "complete",
    }


def _insert_source_trade(conn, row):
    conn.execute(
        """INSERT INTO trades (
               id, account_id, trade_group, date, ticker, instrument_type, side,
               gross_pnl, net_pnl, commissions, executions, option_expiry,
               option_strike, option_type, source
           ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            row["id"], row["account_id"], row["trade_group"], row["date"],
            row["ticker"], row["instrument_type"], row["side"],
            row["gross_pnl"], row["net_pnl"], row["commissions"],
            row["executions"], row["option_expiry"], row["option_strike"],
            row["option_type"], row["source"],
        ),
    )
    conn.commit()


def test_saved_report_round_trip_decodes_json_payloads(tmp_path):
    from smoking_gun_library import create_saved_report, get_saved_report

    conn = _initialized_conn(tmp_path)
    created = create_saved_report(conn, _saved_payload())
    loaded = get_saved_report(conn, created["id"])

    assert loaded is not None
    assert loaded["source_metrics"] == {"scoreboard": {"net_pnl": 50.0}, "trade_ledger": []}
    assert loaded["diagnosis"] == {"headline": "Protect the patient edge."}
    assert loaded["action_plan"] == [{"rank": 1, "rule": "No averaging down"}]
    assert loaded["filters"] == {"tickers": ["SPY", "QQQ"]}
    assert loaded["duplicate"] is False
    conn.close()


def test_duplicate_report_returns_existing_row_without_second_insert(tmp_path):
    from smoking_gun_library import create_saved_report

    conn = _initialized_conn(tmp_path)
    first = create_saved_report(conn, _saved_payload())
    second = create_saved_report(conn, _saved_payload())

    assert second["id"] == first["id"]
    assert second["duplicate"] is True
    assert conn.execute(
        "SELECT COUNT(*) AS n FROM smoking_gun_reports"
    ).fetchone()["n"] == 1
    conn.close()


def test_report_list_is_metadata_only_and_newest_first(tmp_path):
    from smoking_gun_library import create_saved_report, list_saved_reports

    conn = _initialized_conn(tmp_path)
    first = create_saved_report(conn, _saved_payload("fp-1", "First"))
    second = create_saved_report(conn, _saved_payload("fp-2", "Second"))
    conn.execute(
        "UPDATE smoking_gun_reports SET generated_at=? WHERE id=?",
        ("2026-09-26 11:00:00", first["id"]),
    )
    conn.execute(
        "UPDATE smoking_gun_reports SET generated_at=? WHERE id=?",
        ("2026-09-26 12:00:00", second["id"]),
    )
    conn.commit()

    rows = list_saved_reports(conn, account_id=1)

    assert [row["title"] for row in rows] == ["Second", "First"]
    assert "source_metrics" not in rows[0]
    assert "source_metrics_json" not in rows[0]
    assert "diagnosis" not in rows[0]
    assert "action_plan" not in rows[0]
    conn.close()


def test_stale_detection_tracks_trade_mutation_and_missing_source(tmp_path):
    from smoking_gun_library import (
        build_source_fingerprint,
        create_saved_report,
        decorate_stale_status,
    )

    conn = _initialized_conn(tmp_path)
    trade_row = _source_trade("a", 100)
    _insert_source_trade(conn, trade_row)
    filters = {"tickers": ["SPY"]}
    fingerprint = build_source_fingerprint(
        [trade_row], 1, "2026-09-01", "2026-09-30", filters
    )
    payload = _saved_payload(fingerprint)
    payload["filters"] = filters
    created = create_saved_report(conn, payload)

    fresh = decorate_stale_status(conn, [created])[0]
    assert fresh["is_stale"] is False
    assert fresh["stale_reason"] is None

    conn.execute("UPDATE trades SET net_pnl=? WHERE id=?", (101.0, trade_row["id"]))
    conn.commit()
    stale = decorate_stale_status(conn, [created])[0]
    assert stale["is_stale"] is True
    assert stale["stale_reason"] == "source-data-changed"

    conn.execute("DELETE FROM trades WHERE id=?", (trade_row["id"],))
    conn.commit()
    missing = decorate_stale_status(conn, [created])[0]
    assert missing["is_stale"] is True
    assert missing["stale_reason"] == "source-data-missing"
    conn.close()


def test_stale_decoration_reuses_fingerprint_per_distinct_scope(tmp_path, monkeypatch):
    import smoking_gun_library as library

    conn = _initialized_conn(tmp_path)
    first = library.create_saved_report(conn, _saved_payload("fp-1", "First"))
    second_payload = _saved_payload("fp-2", "Second")
    second = library.create_saved_report(conn, second_payload)

    calls = []

    def fake_current(_conn, account_id, date_from, date_to, filters=None):
        calls.append((account_id, date_from, date_to, filters))
        return "current-fp"

    monkeypatch.setattr(library, "current_fingerprint_for_range", fake_current)
    decorated = library.decorate_stale_status(conn, [first, second])

    assert len(decorated) == 2
    assert len(calls) == 1
    conn.close()


def test_current_fingerprint_respects_ticker_and_instrument_filters(tmp_path):
    from smoking_gun_library import (
        build_source_fingerprint,
        current_fingerprint_for_range,
    )

    conn = _initialized_conn(tmp_path)
    spy = _source_trade("a", 100)
    qqq = _source_trade("b", -50)
    _insert_source_trade(conn, spy)
    _insert_source_trade(conn, qqq)

    actual = current_fingerprint_for_range(
        conn,
        1,
        "2026-09-01",
        "2026-09-30",
        {"tickers": ["SPY"], "instrument_types": ["OPTION"]},
    )
    expected = build_source_fingerprint(
        [spy],
        1,
        "2026-09-01",
        "2026-09-30",
        {"tickers": ["SPY"], "instrument_types": ["OPTION"]},
    )

    assert actual == expected
    conn.close()


def _fresh_main_for_api(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_MODE", "sqlite")
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "api-journal.db"))
    monkeypatch.setenv("STORAGE_MODE", "local")
    monkeypatch.setenv("UPLOAD_DIR", str(tmp_path / "uploads"))
    monkeypatch.setenv("AUTH_REQUIRED", "false")
    for name in (
        "auth", "config", "database", "smoking_gun_library",
        "smoking_gun_routes", "main",
    ):
        sys.modules.pop(name, None)
    import main
    return main


def _seed_api_source(main):
    import smoking_gun_library as library

    conn = main.get_db()
    conn.execute(
        "INSERT INTO accounts (id, name, type) VALUES (?, ?, ?)",
        (1, "Primary", "day_trading"),
    )
    source = _source_trade("a", 100)
    _insert_source_trade(conn, source)
    fingerprint = library.build_source_fingerprint(
        [source], 1, "2026-09-01", "2026-09-30", {"tickers": ["SPY"]}
    )
    conn.close()
    return source, fingerprint


def _api_payload(fingerprint):
    return {
        "account_id": 1,
        "title": "September Smoking Gun",
        "date_from": "2026-09-01",
        "date_to": "2026-09-30",
        "report_version": "1",
        "analytics_engine_version": "2026.09.26.1",
        "behavior_version": "2026.09.26.1",
        "analysis_provider": "openai",
        "analysis_model": "gpt-test",
        "data_fingerprint": fingerprint,
        "filters": {"tickers": ["SPY"]},
        "source_metrics": {
            "meta": {"trade_count": 1},
            "scoreboard": {"gross_pnl": 100.0, "net_pnl": 100.0},
            "trade_ledger": [],
        },
        "diagnosis": {"headline": "Patience is the edge."},
        "action_plan": [{"priority": 1, "rule": "No averaging down"}],
        "primary_edge": "Patience",
        "primary_leak": "Averaging down",
    }


def test_saved_report_api_create_list_detail_duplicate_and_delete(monkeypatch, tmp_path):
    main = _fresh_main_for_api(monkeypatch, tmp_path)
    from fastapi.testclient import TestClient

    with TestClient(main.app) as client:
        _, fingerprint = _seed_api_source(main)
        payload = _api_payload(fingerprint)

        created = client.post("/api/smoking-gun-reports", json=payload)
        assert created.status_code == 201
        report_id = created.json()["id"]
        assert created.json()["duplicate"] is False

        duplicate = client.post("/api/smoking-gun-reports", json=payload)
        assert duplicate.status_code == 200
        assert duplicate.json()["id"] == report_id
        assert duplicate.json()["duplicate"] is True

        listing = client.get("/api/smoking-gun-reports", params={"account_id": 1})
        assert listing.status_code == 200
        assert len(listing.json()) == 1
        assert "source_metrics" not in listing.json()[0]
        assert listing.json()[0]["is_stale"] is False

        detail = client.get(f"/api/smoking-gun-reports/{report_id}")
        assert detail.status_code == 200
        assert detail.json()["source_metrics"]["scoreboard"]["net_pnl"] == 100.0
        assert detail.json()["diagnosis"]["headline"] == "Patience is the edge."

        deleted = client.delete(f"/api/smoking-gun-reports/{report_id}")
        assert deleted.status_code == 204
        assert client.get(f"/api/smoking-gun-reports/{report_id}").status_code == 404


def test_saved_report_api_rejects_stale_fingerprint(monkeypatch, tmp_path):
    main = _fresh_main_for_api(monkeypatch, tmp_path)
    from fastapi.testclient import TestClient

    with TestClient(main.app) as client:
        _seed_api_source(main)
        payload = _api_payload("0" * 64)
        response = client.post("/api/smoking-gun-reports", json=payload)

    assert response.status_code == 409
    assert "fingerprint" in response.json()["detail"].lower()


def test_saved_report_api_rejects_empty_source_population(monkeypatch, tmp_path):
    main = _fresh_main_for_api(monkeypatch, tmp_path)
    from fastapi.testclient import TestClient
    import smoking_gun_library as library

    with TestClient(main.app) as client:
        conn = main.get_db()
        conn.execute(
            "INSERT INTO accounts (id, name, type) VALUES (?, ?, ?)",
            (1, "Primary", "day_trading"),
        )
        conn.commit()
        conn.close()
        empty_fp = library.build_source_fingerprint(
            [], 1, "2026-09-01", "2026-09-30", {}
        )
        payload = _api_payload(empty_fp)
        payload["filters"] = {}
        response = client.post("/api/smoking-gun-reports", json=payload)

    assert response.status_code == 400
