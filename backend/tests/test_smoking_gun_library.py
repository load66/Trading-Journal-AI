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
