from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import dataclass
import sqlite3
from typing import Any

from dotenv import load_dotenv

from config import Settings

load_dotenv()

APPLICATION_SCHEMA_VERSION = '20260926_001_supabase_postgres'


class DBAPIRow(Mapping[str, Any]):
    """sqlite3.Row-compatible mapping for remote DB-API tuple results."""

    def __init__(self, columns: tuple[str, ...], values):
        self._columns = columns
        self._values = tuple(values)
        self._by_name = {name: index for index, name in enumerate(columns)}

    def __getitem__(self, key):
        if isinstance(key, int):
            return self._values[key]
        return self._values[self._by_name[key]]

    def __iter__(self) -> Iterator[str]:
        return iter(self._columns)

    def __len__(self) -> int:
        return len(self._columns)

    def keys(self):
        return self._columns


class DBAPICursor:
    def __init__(self, inner):
        self._inner = inner

    @property
    def description(self):
        return getattr(self._inner, 'description', None)

    @property
    def lastrowid(self):
        return getattr(self._inner, 'lastrowid', None)

    @property
    def rowcount(self):
        return getattr(self._inner, 'rowcount', -1)

    def _columns(self) -> tuple[str, ...]:
        return tuple(col[0] for col in (self.description or ()))

    def _wrap(self, row):
        if row is None or isinstance(row, DBAPIRow):
            return row
        columns = self._columns()
        if not columns:
            return row
        if hasattr(row, 'keys'):
            return DBAPIRow(columns, tuple(row[col] for col in columns))
        return DBAPIRow(columns, row)

    def execute(self, sql, params=()):
        self._inner.execute(sql, params)
        return self

    def executemany(self, sql, params):
        self._inner.executemany(sql, params)
        return self

    def fetchone(self):
        return self._wrap(self._inner.fetchone())

    def fetchall(self):
        return [self._wrap(row) for row in self._inner.fetchall()]

    def __iter__(self):
        for row in self._inner:
            yield self._wrap(row)


def translate_qmark_sql(sql: str) -> str:
    """Translate DB-API qmark placeholders without touching quoted question marks."""
    out: list[str] = []
    quote: str | None = None
    index = 0

    while index < len(sql):
        ch = sql[index]

        if quote is not None:
            out.append(ch)
            if ch == quote:
                if index + 1 < len(sql) and sql[index + 1] == quote:
                    out.append(sql[index + 1])
                    index += 1
                else:
                    quote = None
            index += 1
            continue

        if ch in {"'", '"'}:
            quote = ch
            out.append(ch)
        elif ch == '?':
            out.append('%s')
        else:
            out.append(ch)
        index += 1

    return ''.join(out)


class PostgresCursorAdapter(DBAPICursor):
    def execute(self, sql, params=()):
        self._inner.execute(translate_qmark_sql(sql), params)
        return self

    def executemany(self, sql, params):
        self._inner.executemany(translate_qmark_sql(sql), params)
        return self


class PostgresConnectionAdapter:
    """DB-API compatibility layer for the existing sqlite-oriented route code."""

    dialect = 'postgres'

    def __init__(self, inner):
        self._inner = inner

    def execute(self, sql, params=()):
        return PostgresCursorAdapter(
            self._inner.execute(translate_qmark_sql(sql), params)
        )

    def cursor(self):
        return PostgresCursorAdapter(self._inner.cursor())

    def commit(self):
        return self._inner.commit()

    def rollback(self):
        return self._inner.rollback()

    def close(self):
        return self._inner.close()


@dataclass(frozen=True)
class Migration:
    migration_id: str
    table: str
    column: str
    ddl: str


MIGRATIONS = (
    Migration('001_trade_analysis_target_price', 'trade_analysis', 'target_price', 'ALTER TABLE trade_analysis ADD COLUMN target_price REAL'),
    Migration('002_trade_analysis_trade_rating', 'trade_analysis', 'trade_rating', 'ALTER TABLE trade_analysis ADD COLUMN trade_rating INTEGER'),
    Migration('003_trade_analysis_idea_source', 'trade_analysis', 'idea_source', 'ALTER TABLE trade_analysis ADD COLUMN idea_source TEXT'),
    Migration('004_trades_setup', 'trades', 'setup', 'ALTER TABLE trades ADD COLUMN setup TEXT'),
    Migration('005_trades_setup_grade', 'trades', 'setup_grade', 'ALTER TABLE trades ADD COLUMN setup_grade TEXT'),
    Migration('006_trades_setup_notes', 'trades', 'setup_notes', 'ALTER TABLE trades ADD COLUMN setup_notes TEXT'),
    Migration('007_trades_setup_features', 'trades', 'setup_features', 'ALTER TABLE trades ADD COLUMN setup_features TEXT'),
    Migration('008_trades_setup_source', 'trades', 'setup_source', "ALTER TABLE trades ADD COLUMN setup_source TEXT DEFAULT 'auto'"),
    Migration('009_trades_mfe_pct', 'trades', 'mfe_pct', 'ALTER TABLE trades ADD COLUMN mfe_pct REAL'),
    Migration('010_trades_mae_pct', 'trades', 'mae_pct', 'ALTER TABLE trades ADD COLUMN mae_pct REAL'),
    Migration('011_trades_exit_efficiency', 'trades', 'exit_efficiency', 'ALTER TABLE trades ADD COLUMN exit_efficiency REAL'),
)


SCHEMA_STATEMENTS = (
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
    'CREATE INDEX IF NOT EXISTS idx_trades_account_date ON trades(account_id, date)',
    'CREATE INDEX IF NOT EXISTS idx_trades_group ON trades(trade_group)',
    'CREATE INDEX IF NOT EXISTS idx_analysis_group ON trade_analysis(trade_group)',
    'CREATE INDEX IF NOT EXISTS idx_tags_group ON trade_tags(trade_group)',
)


def get_db(settings: Settings | None = None):
    settings = settings or Settings.from_env()
    if settings.database_mode == 'sqlite':
        conn = sqlite3.connect(settings.database_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute('PRAGMA journal_mode=WAL')
        conn.execute('PRAGMA foreign_keys=ON')
        return conn

    import psycopg

    inner = psycopg.connect(settings.database_url, autocommit=False)
    conn = PostgresConnectionAdapter(inner)
    conn.execute('SET search_path TO journal, public')
    return conn


def insert_and_get_id(conn, sql: str, params=()) -> int:
    """Execute an INSERT and return its generated integer primary key."""
    if isinstance(conn, PostgresConnectionAdapter):
        statement = sql.rstrip().rstrip(';')
        if ' returning ' not in statement.lower():
            statement += ' RETURNING id'
        row = conn.execute(statement, params).fetchone()
        if row is None:
            raise RuntimeError('Postgres INSERT did not return an id')
        return int(row[0])

    cursor = conn.execute(sql, params)
    if cursor.lastrowid is None:
        raise RuntimeError('SQLite INSERT did not produce a row id')
    return int(cursor.lastrowid)


def year_filter_clause(column: str = 'date') -> str:
    """Return a SQLite/Postgres portable year predicate for ISO text dates."""
    if not column.isidentifier():
        raise ValueError('column must be a simple SQL identifier')
    return f'substr({column}, 1, 4) = ?'


def is_integrity_error(exc: Exception) -> bool:
    """Recognize unique/constraint errors from either supported database driver."""
    if isinstance(exc, sqlite3.IntegrityError):
        return True
    try:
        import psycopg
    except ImportError:
        return False
    return isinstance(exc, psycopg.IntegrityError)


def _column_names(conn, table: str) -> set[str]:
    rows = conn.execute(f'PRAGMA table_info({table})').fetchall()
    columns = set()
    for row in rows:
        try:
            columns.add(row['name'])
        except (KeyError, TypeError, IndexError):
            columns.add(row[1])
    return columns


def apply_migrations(conn) -> None:
    conn.execute("""CREATE TABLE IF NOT EXISTS schema_migrations (
        migration_id TEXT PRIMARY KEY,
        applied_at TEXT NOT NULL DEFAULT (datetime('now'))
    )""")
    conn.commit()
    applied = {
        row[0]
        for row in conn.execute('SELECT migration_id FROM schema_migrations').fetchall()
    }
    for migration in MIGRATIONS:
        if migration.migration_id in applied:
            continue
        try:
            if migration.column not in _column_names(conn, migration.table):
                conn.execute(migration.ddl)
            conn.execute(
                'INSERT INTO schema_migrations (migration_id) VALUES (?)',
                (migration.migration_id,),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise


def verify_postgres_schema(conn) -> None:
    """Fail closed unless the versioned Supabase schema has been applied."""
    try:
        row = conn.execute(
            'SELECT 1 AS present FROM schema_migrations WHERE migration_id=?',
            (APPLICATION_SCHEMA_VERSION,),
        ).fetchone()
    except Exception as exc:
        raise RuntimeError(
            f'Postgres schema is not initialized to application version {APPLICATION_SCHEMA_VERSION}'
        ) from exc

    if row is None:
        raise RuntimeError(
            f'Postgres schema is not initialized to application version {APPLICATION_SCHEMA_VERSION}'
        )


def init_db(settings: Settings | None = None):
    settings = settings or Settings.from_env()
    conn = get_db(settings)
    try:
        if settings.database_mode == 'postgres':
            verify_postgres_schema(conn)
            return

        for statement in SCHEMA_STATEMENTS:
            conn.execute(statement)
        conn.commit()
        apply_migrations(conn)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def row_to_dict(row) -> dict:
    return dict(row)
