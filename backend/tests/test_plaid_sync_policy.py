"""Synthetic scheduling and rate-cap tests. No provider calls or account data."""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pytest
from plaid_sync_policy import (
    ITEM_POLL_SECONDS, MIN_REFRESH_GAP_SECONDS, MAX_DAILY_CALLS,
    rolling_calls, reserve_call, retry_deadline, due_for_refresh,
    DailyCallCapReached,
)

NOW = 1800000000


def test_three_hour_poll_and_five_minute_refresh_gap():
    assert ITEM_POLL_SECONDS == 10800
    assert MIN_REFRESH_GAP_SECONDS == 300
    assert due_for_refresh(NOW - 301, NOW) is True
    assert due_for_refresh(NOW - 299, NOW) is False
    assert due_for_refresh(None, NOW) is True


def test_rolling_call_cap_blocks_the_twenty_fifth_call():
    record = []
    for i in range(24):
        record = reserve_call(record, NOW + i)
    assert len(record) == MAX_DAILY_CALLS == 24
    with pytest.raises(DailyCallCapReached) as exceeded:
        reserve_call(record, NOW + 25)
    assert exceeded.value.retry_at == NOW + 86400
    assert len(rolling_calls(record, NOW + 86400)) == 23
    assert len(reserve_call(record, NOW + 86400)) == 24


def test_backoff_and_history_retry_are_not_shortened():
    for attempts, hours in ((1, 1), (2, 3), (3, 6), (4, 24), (5, 24)):
        assert retry_deadline(NOW, attempts) == NOW + hours * 3600


def test_invalid_budget_records_fail_closed():
    with pytest.raises(ValueError):
        reserve_call(["invalid"], NOW)
    with pytest.raises(ValueError):
        reserve_call([NOW + 100], NOW)

def test_cap_reservation_is_atomic_on_existing_item_ledger(monkeypatch):
    from copy import deepcopy
    from fastapi import HTTPException
    import bonus_plaid_accounts as adapter
    ledger = {"items": {"opaque-item": {
        "owner": "synthetic-owner", "access": "synthetic-access",
    }}}
    monkeypatch.setattr(adapter.quota, "_ready", lambda: True)
    monkeypatch.setattr(adapter.quota, "_ledger", lambda: (deepcopy(ledger), "tag"))
    def write(doc, tag):
        ledger.clear()
        ledger.update(deepcopy(doc))
        return "tag"
    monkeypatch.setattr(adapter.quota, "_write", write)
    monkeypatch.setattr(adapter.time, "time", lambda: NOW)
    for _ in range(MAX_DAILY_CALLS):
        adapter._reserve_transaction_call("synthetic-access")
    assert len(ledger["items"]["opaque-item"]["transactionSyncCalls"]) == MAX_DAILY_CALLS
    with pytest.raises(HTTPException) as err:
        adapter._reserve_transaction_call("synthetic-access")
    assert err.value.status_code == 429
    assert len(ledger["items"]["opaque-item"]["transactionSyncCalls"]) == MAX_DAILY_CALLS
    with pytest.raises(HTTPException) as unknown:
        adapter._reserve_transaction_call("unrecognized")
    assert unknown.value.status_code == 403
