from __future__ import annotations

import importlib
import os
import sqlite3

from dotenv import load_dotenv
from runtime_config import load_runtime_config

load_dotenv()

# Compatibility hook: existing tests/importers may patch this directly.
DB_PATH = os.getenv("DATABASE_PATH", "trading_journal.db")


class CompatRow:
    """sqlite3.Row-like wrapper for remote DB-API result tuples."""

    def __init__(self, columns, values):
        self._columns = tuple(columns)
        if isinstance(values, dict):
            self._values = tuple(values.get(column) for column in self._columns)
        else:
            self._values = tuple(values)
        self._index = {name: i for i, name in enumerate(self._columns)}

    def keys(self):
        return list(self._columns)

    def __getitem__(self, key):
        if isinstance(key, str):
            return self._values[self._index[key]]
        return self._values[key]

    def __iter__(self):
        # Match sqlite3.Row: iteration yields values, while dict(row) uses keys().
        return iter(self._values)

    def __len__(self):
        return len(self._values)


def _description_columns(description) -> list[str]:
    columns = []
    for item in description or []:
        if isinstance(item, (tuple, list)):
            columns.append(str(item[0]))
        elif hasattr(item, "name"):
            columns.append(str(item.name))
        else:
            columns.append(str(item))
    return columns


def _translate_remote_error(exc: Exception):
    text = str(exc).lower()
    if any(word in text for word in ("constraint", "unique", "integrity")):
        raise sqlite3.IntegrityError(str(exc)) from exc
    raise exc


class RemoteCursor:
    """Wrap a remote DB-API cursor so rows match sqlite3.Row behavior."""

    def __init__(self, raw):
        self._raw = raw

    @property
    def description(self):
        return getattr(self._raw, "description", None)

    @property
    def rowcount(self):
        return getattr(self._raw, "rowcount", -1)

    @property
    def lastrowid(self):
        value = getattr(self._raw, "lastrowid", None)
        if value is None:
            value = getattr(self._raw, "last_insert_rowid", None)
        return value

    def execute(self, sql, params=()):
        try:
            self._raw.execute(sql, params)
        except Exception as exc:
            _translate_remote_error(exc)
        return self

    def executemany(self, sql, seq_of_params):
        try:
            self._raw.executemany(sql, seq_of_params)
        except Exception as exc:
            _translate_remote_error(exc)
        return self

    def _wrap(self, row):
        if row is None:
            return None
        columns = _description_columns(self.description)
        if not columns and hasattr(row, "keys"):
            columns = list(row.keys())
        return CompatRow(columns, row)

    def fetchone(self):
        return self._wrap(self._raw.fetchone())

    def fetchall(self):
        return [self._wrap(row) for row in self._raw.fetchall()]

    def __iter__(self):
        for row in self._raw:
            yield self._wrap(row)


class RemoteConnection:
    """Narrow compatibility layer over turso_serverless DB-API connections."""

    def __init__(self, raw):
        self._raw = raw

    def execute(self, sql, params=()):
        try:
            cursor = self._raw.execute(sql, params)
        except Exception as exc:
            _translate_remote_error(exc)
        return RemoteCursor(cursor)

    def executemany(self, sql, seq_of_params):
        try:
            cursor = self._raw.executemany(sql, seq_of_params)
        except Exception as exc:
            _translate_remote_error(exc)
        return RemoteCursor(cursor)

    def cursor(self):
        return RemoteCursor(self._raw.cursor())

    def executescript(self, script: str):
        # The app's scripts are schema-only and contain no semicolons in literals.
        for statement in script.split(";"):
            statement = statement.strip()
            if statement:
                self.execute(statement)
        return self

    def commit(self):
        return self._raw.commit()

    def rollback(self):
        return self._raw.rollback()

    def close(self):
        return self._raw.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        if exc_type is None:
            self.commit()
        else:
            self.rollback()
        self.close()


def get_db():
    cfg = load_runtime_config()
    if cfg.database_mode == "sqlite":
        path = os.getenv("DATABASE_PATH", DB_PATH)
        conn = sqlite3.connect(path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    driver = importlib.import_module("turso_serverless")
    raw = driver.connect(
        cfg.turso_database_url,
        auth_token=cfg.turso_auth_token,
    )
    conn = RemoteConnection(raw)
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


_BASE_SCHEMA = [
    """CREATE TABLE IF NOT EXISTS accounts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        type TEXT NOT NULL CHECK(type IN ('day_trading','swing_trading','investment')),
        color TEXT NOT NULL DEFAULT '#6366f1',
        broker TEXT,
        created_at TEXT NOT NULL DEFAULT (datetime('now'))
    )""",
    """CREATE TABLE IF NOT EXISTS trades (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        account_id INTEGER NOT NULL REFERENCES accounts(id),
        trade_group TEXT NOT NULL,
        date TEXT NOT NULL,
        ticker TEXT NOT NULL,
        instrument_type TEXT NOT NULL CHECK(instrument_type IN ('STOCK','OPTION','FUTURE')),
        side TEXT NOT NULL CHECK(side IN ('LONG','SHORT')),
        gross_pnl REAL,
        net_pnl REAL,
        commissions REAL DEFAULT 0,
        executions TEXT NOT NULL DEFAULT '[]',
        option_expiry TEXT,
        option_strike REAL,
        option_type TEXT CHECK(option_type IN ('CALL','PUT',NULL)),
        source TEXT NOT NULL DEFAULT 'imported',
        imported_at TEXT NOT NULL DEFAULT (datetime('now')),
        UNIQUE(trade_group, account_id)
    )""",
    """CREATE TABLE IF NOT EXISTS diary_entries (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        account_id INTEGER NOT NULL REFERENCES accounts(id),
        entry_date TEXT NOT NULL,
        image_path TEXT,
        raw_text TEXT,
        ai_analysis TEXT,
        created_at TEXT NOT NULL DEFAULT (datetime('now'))
    )""",
    """CREATE TABLE IF NOT EXISTS trade_analysis (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        trade_group TEXT NOT NULL UNIQUE,
        ticker TEXT NOT NULL,
        date TEXT NOT NULL,
        strategy TEXT,
        stop_loss REAL,
        risk_per_trade REAL,
        risk_reward REAL,
        r_multiple REAL,
        entry_reason TEXT,
        exit_reason TEXT,
        mistakes TEXT,
        emotional_state TEXT,
        notes TEXT,
        ai_feedback TEXT,
        match_confidence TEXT CHECK(match_confidence IN ('high','medium','low','ambiguous','unmatched','manual')),
        match_notes TEXT,
        diary_entry_id INTEGER REFERENCES diary_entries(id)
    )""",
    """CREATE TABLE IF NOT EXISTS trade_tags (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        trade_group TEXT NOT NULL,
        tag_type TEXT NOT NULL,
        tag_value TEXT NOT NULL,
        source TEXT NOT NULL CHECK(source IN ('ai','manual'))
    )""",
    """CREATE TABLE IF NOT EXISTS daily_summaries (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        account_id INTEGER REFERENCES accounts(id),
        summary_date TEXT NOT NULL,
        ai_content TEXT NOT NULL,
        generated_at TEXT NOT NULL DEFAULT (datetime('now')),
        UNIQUE(summary_date, account_id)
    )""",
    """CREATE TABLE IF NOT EXISTS settings (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        account_id INTEGER NOT NULL DEFAULT 0,
        key TEXT NOT NULL,
        value TEXT NOT NULL,
        UNIQUE(account_id, key)
    )""",
    """CREATE TABLE IF NOT EXISTS custom_setups (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL UNIQUE,
        side TEXT,
        notes TEXT,
        active INTEGER NOT NULL DEFAULT 1,
        created_at TEXT NOT NULL DEFAULT (datetime('now'))
    )""",
    "CREATE INDEX IF NOT EXISTS idx_trades_account_date ON trades(account_id, date)",
    "CREATE INDEX IF NOT EXISTS idx_trades_group ON trades(trade_group)",
    "CREATE INDEX IF NOT EXISTS idx_analysis_group ON trade_analysis(trade_group)",
    "CREATE INDEX IF NOT EXISTS idx_tags_group ON trade_tags(trade_group)",
    """CREATE TABLE IF NOT EXISTS schema_migrations (
        id TEXT PRIMARY KEY,
        applied_at TEXT NOT NULL DEFAULT (datetime('now'))
    )""",
]

_COLUMN_MIGRATIONS = [
    ("001_trade_analysis_target_price", "trade_analysis", "target_price",
     "ALTER TABLE trade_analysis ADD COLUMN target_price REAL"),
    ("002_trade_analysis_trade_rating", "trade_analysis", "trade_rating",
     "ALTER TABLE trade_analysis ADD COLUMN trade_rating INTEGER"),
    ("003_trade_analysis_idea_source", "trade_analysis", "idea_source",
     "ALTER TABLE trade_analysis ADD COLUMN idea_source TEXT"),
    ("004_trades_setup", "trades", "setup",
     "ALTER TABLE trades ADD COLUMN setup TEXT"),
    ("005_trades_setup_grade", "trades", "setup_grade",
     "ALTER TABLE trades ADD COLUMN setup_grade TEXT"),
    ("006_trades_setup_notes", "trades", "setup_notes",
     "ALTER TABLE trades ADD COLUMN setup_notes TEXT"),
    ("007_trades_setup_features", "trades", "setup_features",
     "ALTER TABLE trades ADD COLUMN setup_features TEXT"),
    ("008_trades_setup_source", "trades", "setup_source",
     "ALTER TABLE trades ADD COLUMN setup_source TEXT DEFAULT 'auto'"),
    ("009_trades_mfe_pct", "trades", "mfe_pct",
     "ALTER TABLE trades ADD COLUMN mfe_pct REAL"),
    ("010_trades_mae_pct", "trades", "mae_pct",
     "ALTER TABLE trades ADD COLUMN mae_pct REAL"),
    ("011_trades_exit_efficiency", "trades", "exit_efficiency",
     "ALTER TABLE trades ADD COLUMN exit_efficiency REAL"),
]


def _table_columns(conn, table: str) -> set[str]:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return {
        row["name"] if "name" in row.keys() else row[1]
        for row in rows
    }


def _apply_migrations(conn):
    for migration_id, table, column, ddl in _COLUMN_MIGRATIONS:
        applied = conn.execute(
            "SELECT 1 FROM schema_migrations WHERE id=?",
            (migration_id,),
        ).fetchone()
        if applied:
            continue
        if column not in _table_columns(conn, table):
            conn.execute(ddl)
        conn.execute(
            "INSERT INTO schema_migrations (id) VALUES (?)",
            (migration_id,),
        )
        conn.commit()


def init_db():
    conn = get_db()
    try:
        for statement in _BASE_SCHEMA:
            conn.execute(statement)
        conn.commit()
        _apply_migrations(conn)
    finally:
        conn.close()


def row_to_dict(row) -> dict:
    if row is None:
        return {}
    if hasattr(row, "keys"):
        return {key: row[key] for key in row.keys()}
    return dict(row)
