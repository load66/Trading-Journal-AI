import sqlite3
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

import database  # noqa: E402
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
    with pytest.raises(ConfigError, match='SUPABASE_DB_URL'):
        settings(DATABASE_MODE='postgres', SUPABASE_DB_URL='')


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
    assert applied == expected


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
    assert migrations == len(database.MIGRATIONS)
    conn.close()
