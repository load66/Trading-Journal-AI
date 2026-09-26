import os
import sqlite3
import sys
import types
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

import database


class FakeCursor:
    def __init__(self, rows=None, columns=None, lastrowid=None):
        self.rows = list(rows or [])
        self.description = [
            (name, None, None, None, None, None, None)
            for name in (columns or [])
        ]
        self.lastrowid = lastrowid
        self.rowcount = len(self.rows)

    def fetchone(self):
        return self.rows.pop(0) if self.rows else None

    def fetchall(self):
        rows = self.rows[:]
        self.rows.clear()
        return rows

    def __iter__(self):
        return iter(self.rows)


class FakeConnection:
    def __init__(self):
        self.calls = []
        self.closed = False

    def execute(self, sql, params=()):
        self.calls.append((sql, params))
        if sql.startswith("SELECT"):
            return FakeCursor([(1, "Alice")], ["id", "name"])
        return FakeCursor(lastrowid=7)

    def executemany(self, sql, params):
        self.calls.append((sql, list(params)))
        return FakeCursor()

    def cursor(self):
        return FakeCursor()

    def commit(self):
        pass

    def rollback(self):
        pass

    def close(self):
        self.closed = True


def test_turso_mode_selects_remote_driver_and_preserves_row_access(monkeypatch):
    monkeypatch.setenv("DATABASE_MODE", "turso")
    monkeypatch.setenv("TURSO_DATABASE_URL", "https://db.example.test")
    monkeypatch.setenv("TURSO_AUTH_TOKEN", "token")

    raw = FakeConnection()
    module = types.SimpleNamespace(
        connect=lambda url, auth_token: raw
    )
    monkeypatch.setattr(database.importlib, "import_module", lambda name: module)

    conn = database.get_db()
    row = conn.execute("SELECT id, name FROM users").fetchone()

    assert raw.calls[0][0] == "PRAGMA foreign_keys=ON"
    assert row[0] == 1
    assert row["name"] == "Alice"
    assert row.keys() == ["id", "name"]
    assert dict(row) == {"id": 1, "name": "Alice"}


def test_sqlite_mode_remains_native_sqlite(tmp_path, monkeypatch):
    path = tmp_path / "journal.db"
    monkeypatch.setenv("DATABASE_MODE", "sqlite")
    monkeypatch.setenv("DATABASE_PATH", str(path))
    monkeypatch.setattr(database, "DB_PATH", str(path))

    conn = database.get_db()
    try:
        assert isinstance(conn, sqlite3.Connection)
        conn.execute("CREATE TABLE sample (id INTEGER PRIMARY KEY, name TEXT)")
        conn.execute("INSERT INTO sample (name) VALUES (?)", ("local",))
        conn.commit()
        row = conn.execute("SELECT * FROM sample").fetchone()
        assert row["name"] == "local"
    finally:
        conn.close()


def test_existing_database_migrations_are_adopted_and_idempotent(tmp_path, monkeypatch):
    path = tmp_path / "journal.db"
    monkeypatch.setenv("DATABASE_MODE", "sqlite")
    monkeypatch.setenv("DATABASE_PATH", str(path))
    monkeypatch.setattr(database, "DB_PATH", str(path))

    # First initialization represents an upgraded local database.
    database.init_db()
    # Running initialization again must not attempt duplicate ALTERs.
    database.init_db()

    conn = sqlite3.connect(path)
    try:
        migrations = conn.execute(
            "SELECT id FROM schema_migrations ORDER BY id"
        ).fetchall()
        assert len(migrations) == 11

        trade_columns = {
            row[1] for row in conn.execute("PRAGMA table_info(trades)")
        }
        assert {"setup", "mfe_pct", "mae_pct", "exit_efficiency"} <= trade_columns

        analysis_columns = {
            row[1] for row in conn.execute("PRAGMA table_info(trade_analysis)")
        }
        assert {"target_price", "trade_rating", "idea_source"} <= analysis_columns
    finally:
        conn.close()


def test_unexpected_migration_error_is_not_silenced(tmp_path, monkeypatch):
    path = tmp_path / "journal.db"
    monkeypatch.setenv("DATABASE_MODE", "sqlite")
    monkeypatch.setenv("DATABASE_PATH", str(path))
    monkeypatch.setattr(database, "DB_PATH", str(path))

    original = database._table_columns

    def explode(conn, table):
        if table == "trades":
            raise RuntimeError("migration probe failed")
        return original(conn, table)

    monkeypatch.setattr(database, "_table_columns", explode)

    with pytest.raises(RuntimeError, match="migration probe failed"):
        database.init_db()
