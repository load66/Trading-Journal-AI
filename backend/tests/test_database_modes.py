import sqlite3
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

import database  # noqa: E402
import library as library_module  # noqa: E402
from config import ConfigError, Settings  # noqa: E402


def settings(**overrides):
    env = {
        'APP_ENV': 'test',
        'AUTH_MODE': 'disabled',
        'DATABASE_MODE': 'sqlite',
        'DATABASE_PATH': ':memory:',
        'STORAGE_MODE': 'local',
    }
    env.update(overrides)
    return Settings.from_env(env)


def test_sqlite_mode_keeps_named_rows(tmp_path):
    db_path = tmp_path / 'journal.db'
    conn = database.get_db(settings(DATABASE_PATH=str(db_path)))
    conn.execute('CREATE TABLE t (id INTEGER PRIMARY KEY, name TEXT)')
    conn.execute('INSERT INTO t(name) VALUES (?)', ('alpha',))
    row = conn.execute('SELECT id, name FROM t').fetchone()
    assert row['name'] == 'alpha'
    assert row[1] == 'alpha'
    assert dict(row) == {'id': 1, 'name': 'alpha'}
    conn.close()


def test_postgres_mode_requires_database_url():
    with pytest.raises(ConfigError, match='DATABASE_URL'):
        settings(DATABASE_MODE='postgres', DATABASE_URL='')


def test_turso_mode_is_no_longer_supported():
    with pytest.raises(ConfigError, match='DATABASE_MODE'):
        settings(DATABASE_MODE='turso', TURSO_DATABASE_URL='https://db.turso.io', TURSO_AUTH_TOKEN='secret')


def test_remote_rows_support_name_index_and_dict_conversion():
    row = database.DBAPIRow(('id', 'name'), (7, 'QQQ'))
    assert row[0] == 7
    assert row['name'] == 'QQQ'
    assert dict(row) == {'id': 7, 'name': 'QQQ'}


def _legacy_db():
    conn = sqlite3.connect(':memory:')
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE trades ("
        "id INTEGER PRIMARY KEY, setup TEXT, setup_grade TEXT, setup_notes TEXT, "
        "setup_features TEXT, setup_source TEXT DEFAULT 'auto', mfe_pct REAL, "
        "mae_pct REAL, exit_efficiency REAL)"
    )
    conn.execute(
        'CREATE TABLE trade_analysis ('
        'id INTEGER PRIMARY KEY, target_price REAL, trade_rating INTEGER, idea_source TEXT)'
    )
    return conn


def test_existing_legacy_column_is_adopted_into_schema_migrations():
    conn = _legacy_db()
    database.apply_migrations(conn)
    applied = {
        row[0]
        for row in conn.execute('SELECT migration_id FROM schema_migrations')
    }
    expected = {m.migration_id for m in database.MIGRATIONS}
    expected.add(database.ACCOUNTS_MIXED_TYPE_MIGRATION_ID)
    assert applied == expected


def test_existing_sqlite_accounts_expand_type_constraint_without_losing_references():
    conn = sqlite3.connect(':memory:')
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA foreign_keys=ON')
    conn.execute("""CREATE TABLE accounts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        type TEXT NOT NULL CHECK(type IN ('day_trading','swing_trading','investment')),
        color TEXT NOT NULL DEFAULT '#6366f1',
        broker TEXT,
        created_at TEXT NOT NULL DEFAULT (datetime('now'))
    )""")
    conn.execute("""CREATE TABLE trades (
        id INTEGER PRIMARY KEY,
        account_id INTEGER NOT NULL REFERENCES accounts(id),
        setup TEXT, setup_grade TEXT, setup_notes TEXT, setup_features TEXT,
        setup_source TEXT DEFAULT 'auto', mfe_pct REAL, mae_pct REAL,
        exit_efficiency REAL, excursion_basis TEXT, excursion_calculated_at TEXT,
        excursion_version TEXT
    )""")
    conn.execute("""CREATE TABLE trade_analysis (
        id INTEGER PRIMARY KEY, target_price REAL, trade_rating INTEGER,
        idea_source TEXT, chart_screenshot_path TEXT
    )""")
    conn.execute("INSERT INTO accounts (id, name, type) VALUES (1, 'Primary', 'day_trading')")
    conn.execute("INSERT INTO trades (id, account_id) VALUES (10, 1)")
    conn.commit()

    database.apply_migrations(conn)
    conn.execute("UPDATE accounts SET type='mixed_trading' WHERE id=1")
    conn.commit()

    account = conn.execute('SELECT id, name, type FROM accounts').fetchone()
    trade = conn.execute('SELECT id, account_id FROM trades').fetchone()
    assert dict(account) == {'id': 1, 'name': 'Primary', 'type': 'mixed_trading'}
    assert dict(trade) == {'id': 10, 'account_id': 1}
    assert conn.execute('PRAGMA foreign_key_check').fetchall() == []
    assert conn.execute('PRAGMA foreign_keys').fetchone()[0] == 1
    conn.close()


def test_migration_error_propagates(monkeypatch):
    conn = sqlite3.connect(':memory:')
    conn.row_factory = sqlite3.Row
    conn.execute('CREATE TABLE trades (id INTEGER PRIMARY KEY)')
    monkeypatch.setattr(database, 'MIGRATIONS', (
        database.Migration(
            'broken',
            'trades',
            'new_col',
            'ALTER TABLE definitely_missing ADD COLUMN new_col TEXT',
        ),
    ))
    with pytest.raises(sqlite3.OperationalError):
        database.apply_migrations(conn)


def test_init_db_is_idempotent(tmp_path):
    db_path = tmp_path / 'journal.db'
    test_settings = settings(DATABASE_PATH=str(db_path))
    database.init_db(test_settings)
    database.init_db(test_settings)
    conn = database.get_db(test_settings)
    migrations = conn.execute(
        'SELECT COUNT(*) AS n FROM schema_migrations'
    ).fetchone()['n']
    assert migrations == len(database.MIGRATIONS) + 1
    conn.close()


class _FakeCursor:
    def __init__(self, rows=(), description=()):
        self._rows = list(rows)
        self.description = description
        self.rowcount = len(self._rows)

    def execute(self, sql, params=()):
        self.last_sql = sql
        self.last_params = params
        return self

    def fetchone(self):
        return self._rows.pop(0) if self._rows else None

    def fetchall(self):
        rows = list(self._rows)
        self._rows.clear()
        return rows

    def __iter__(self):
        return iter(self._rows)


class _FakePostgresConnection:
    def __init__(self):
        self.calls = []
        self.commits = 0
        self.rollbacks = 0
        self.closed = False

    def execute(self, sql, params=()):
        self.calls.append((sql, params))
        if 'RETURNING id' in sql:
            return _FakeCursor([(42,)], [('id',)])
        if sql.startswith('SELECT'):
            return _FakeCursor([(7, 'QQQ')], [('id',), ('name',)])
        return _FakeCursor()

    def cursor(self):
        parent = self

        class Cursor(_FakeCursor):
            def execute(self, sql, params=()):
                parent.calls.append((sql, params))
                return super().execute(sql, params)

        return Cursor()

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def close(self):
        self.closed = True


def test_postgres_mode_uses_psycopg_and_private_schema(monkeypatch):
    fake = _FakePostgresConnection()
    seen = {}

    class FakePsycopg:
        @staticmethod
        def connect(dsn, **kwargs):
            seen['dsn'] = dsn
            seen['kwargs'] = kwargs
            return fake

    monkeypatch.setitem(sys.modules, 'psycopg', FakePsycopg)
    conn = database.get_db(settings(
        DATABASE_MODE='postgres',
        DATABASE_URL='postgresql://example/session-pooler',
    ))

    assert seen['dsn'] == 'postgresql://example/session-pooler'
    assert seen['kwargs'] == {'autocommit': False}
    assert fake.calls[0][0] == 'SET search_path TO journal, public'
    conn.close()
    assert fake.closed is True


def test_postgres_adapter_translates_qmark_parameters_and_wraps_rows():
    fake = _FakePostgresConnection()
    conn = database.PostgresConnectionAdapter(fake)

    row = conn.execute('SELECT id, name FROM trades WHERE id=? AND ticker=?', (7, 'QQQ')).fetchone()

    assert fake.calls[-1] == (
        'SELECT id, name FROM trades WHERE id=%s AND ticker=%s',
        (7, 'QQQ'),
    )
    assert row[0] == 7
    assert row['name'] == 'QQQ'
    assert dict(row) == {'id': 7, 'name': 'QQQ'}


def test_qmark_translation_preserves_question_marks_inside_quoted_literals():
    sql = "SELECT '?' AS marker, \"still ?\" AS quoted_identifier, id FROM trades WHERE id=?"
    assert database.translate_qmark_sql(sql) == (
        "SELECT '?' AS marker, \"still ?\" AS quoted_identifier, id FROM trades WHERE id=%s"
    )


def test_postgres_insert_and_get_id_uses_returning_not_lastval():
    fake = _FakePostgresConnection()
    conn = database.PostgresConnectionAdapter(fake)

    inserted_id = database.insert_and_get_id(
        conn,
        'INSERT INTO accounts (name) VALUES (?)',
        ('Primary',),
    )

    assert inserted_id == 42
    assert fake.calls[-1] == (
        'INSERT INTO accounts (name) VALUES (%s) RETURNING id',
        ('Primary',),
    )
    assert not any(sql == 'SELECT lastval()' for sql, _ in fake.calls)


class _PostgresSchemaConnection:
    dialect = 'postgres'

    def __init__(self, schema_ready=True, library_ready=True):
        self.schema_ready = schema_ready
        self.library_ready = library_ready
        self.calls = []
        self.closed = False

    def execute(self, sql, params=()):
        self.calls.append((sql, params))
        if 'schema_migrations' in sql:
            rows = [(1,)] if self.schema_ready else []
            return _FakeCursor(rows, [('present',)])
        if 'information_schema.tables' in sql:
            table = params[-1] if params else None
            rows = [(1,)] if self.library_ready and table in {'library_items', 'library_aliases'} else []
            return _FakeCursor(rows, [('present',)])
        return _FakeCursor()

    def close(self):
        self.closed = True


def test_postgres_schema_verification_rejects_missing_application_version():
    conn = _PostgresSchemaConnection(schema_ready=False)

    with pytest.raises(RuntimeError, match='Postgres schema is not initialized'):
        database.verify_postgres_schema(conn)


def test_postgres_init_only_verifies_schema_and_never_runs_sqlite_ddl(monkeypatch):
    conn = _PostgresSchemaConnection(schema_ready=True)
    monkeypatch.setattr(database, 'get_db', lambda _settings=None: conn)

    database.init_db(settings(
        DATABASE_MODE='postgres',
        DATABASE_URL='postgresql://example/session-pooler',
    ))

    executed = '\n'.join(sql for sql, _ in conn.calls)
    assert 'schema_migrations' in executed
    assert 'CREATE TABLE' not in executed
    assert 'AUTOINCREMENT' not in executed
    assert 'PRAGMA' not in executed
    assert "datetime('now')" not in executed
    assert conn.closed is True


def test_library_init_uses_migrated_tables_in_postgres_mode():
    conn = _PostgresSchemaConnection(schema_ready=True, library_ready=True)

    library_module.init_library_tables(conn)

    checked_tables = [params[-1] for sql, params in conn.calls if 'information_schema.tables' in sql]
    assert checked_tables == ['library_items', 'library_aliases']


def test_runtime_request_sql_has_no_sqlite_only_constructs():
    source = (BACKEND / 'main.py').read_text()

    assert '.lastrowid' not in source
    assert 'INSERT OR REPLACE' not in source
    assert "datetime('now')" not in source
    assert "strftime('%Y', date)" not in source
    assert 'except sqlite3.IntegrityError' not in source
    assert 'ROUND(SUM(t.net_pnl),2)' not in source
    assert 'ROUND(CAST(SUM(t.net_pnl) AS NUMERIC), 2)' in source


def test_year_filter_clause_is_portable_for_iso_text_dates():
    conn = sqlite3.connect(':memory:')
    conn.row_factory = sqlite3.Row
    conn.execute('CREATE TABLE trades (date TEXT NOT NULL)')
    conn.executemany(
        'INSERT INTO trades(date) VALUES (?)',
        [('2025-12-31',), ('2026-01-02',), ('2026-09-26',)],
    )

    rows = conn.execute(
        f"SELECT date FROM trades WHERE {database.year_filter_clause('date')}",
        ('2026',),
    ).fetchall()

    assert [row['date'] for row in rows] == ['2026-01-02', '2026-09-26']


def test_integrity_error_detection_supports_sqlite_and_psycopg(monkeypatch):
    assert database.is_integrity_error(sqlite3.IntegrityError('duplicate')) is True

    class FakePostgresIntegrityError(Exception):
        pass

    class FakePsycopg:
        IntegrityError = FakePostgresIntegrityError

    monkeypatch.setitem(sys.modules, 'psycopg', FakePsycopg)
    assert database.is_integrity_error(FakePostgresIntegrityError('duplicate')) is True
    assert database.is_integrity_error(ValueError('other')) is False
