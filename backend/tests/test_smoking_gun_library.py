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
