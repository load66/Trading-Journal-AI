import sys
from pathlib import Path

import pytest


BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))


def fresh_main(monkeypatch, tmp_path):
    monkeypatch.setenv('DATABASE_PATH', str(tmp_path / 'journal.db'))
    monkeypatch.setenv('UPLOAD_DIR', str(tmp_path / 'uploads'))
    monkeypatch.setenv('AUTH_REQUIRED', 'false')

    for name in ('auth', 'database', 'main'):
        sys.modules.pop(name, None)

    import main
    return main


def test_mixed_trading_account_can_be_created_and_reclassified(monkeypatch, tmp_path):
    main = fresh_main(monkeypatch, tmp_path)
    main.init_db()
    conn = main.get_db()
    try:
        created = main.create_account(
            main.AccountCreate(name='Brokerage', type='mixed_trading', broker='Schwab'),
            conn=conn,
        )
        assert created['type'] == 'mixed_trading'

        updated = main.update_account(
            created['id'],
            main.AccountUpdate(type='day_trading'),
            conn=conn,
        )
        assert updated['type'] == 'day_trading'
    finally:
        conn.close()


def test_unknown_account_type_is_rejected(monkeypatch, tmp_path):
    main = fresh_main(monkeypatch, tmp_path)
    main.init_db()
    conn = main.get_db()
    try:
        with pytest.raises(ValueError, match='mixed_trading'):
            main.create_account(
                main.AccountCreate(name='Unknown', type='scalping'),
                conn=conn,
            )
    finally:
        conn.close()
