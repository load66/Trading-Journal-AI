"""Scoped existing-Item sync regressions using synthetic private state only."""
from copy import deepcopy
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import bonus_plaid_accounts as adapter
import pytest
from fastapi import HTTPException

NOW = 1800000000


def acct(raw_id):
    return {"account_id": raw_id, "type": "depository", "subtype": "checking",
            "balances": {}}


def test_single_item_sync_keeps_other_item_and_manual_reviews(monkeypatch):
    first = adapter._merge({}, "opaque-first", "Synthetic", {"accounts": [acct("a")]},
                           ({}, {}, set(), "cursor-first"), NOW - 7200)
    second = adapter._merge({}, "opaque-second", "Synthetic", {"accounts": [acct("b")]},
                            ({}, {}, set(), "cursor-second"), NOW - 7200)
    doc = {"version": 1, "items": {"opaque-first": first, "opaque-second": second},
           "monitored": [adapter._account_key("opaque-first", "a"),
                         adapter._account_key("opaque-second", "b")],
           "transactionReviews": {"owner-selection": {"one": {"category": "other"}}}}
    original = deepcopy(doc["items"]["opaque-second"])
    original_reviews = deepcopy(doc["transactionReviews"])
    ledger = {"items": {
        "opaque-first": {"owner": "synthetic", "access": "stub-first", "institution": "Synthetic"},
        "opaque-second": {"owner": "synthetic", "access": "stub-second", "institution": "Synthetic"},
    }}
    monkeypatch.setattr(adapter.quota, "_ledger", lambda: (ledger, "q-tag"))
    monkeypatch.setattr(adapter.time, "time", lambda: NOW)
    monkeypatch.setattr(adapter, "_read", lambda uid: (deepcopy(doc), "revision"))
    def save(uid, updated, etag):
        doc.clear()
        doc.update(deepcopy(updated))
        return "revision"
    monkeypatch.setattr(adapter, "_write", save)
    cursors = []
    def sync(token, cursor):
        assert token == "stub-first"
        cursors.append(cursor)
        return adapter.SyncDelta(({}, {}, set(), "new-cursor"))
    monkeypatch.setattr(adapter, "_sync_item", sync)
    def provider(endpoint, token, **kwargs):
        assert endpoint == "/accounts/get" and token == "stub-first"
        return {"accounts": [acct("a")]}
    monkeypatch.setattr(adapter, "_provider", provider)
    adapter.sync_item_now("synthetic", "opaque-first")
    assert cursors == ["cursor-first"]
    assert doc["items"]["opaque-first"]["cursor"] == "new-cursor"
    assert doc["items"]["opaque-second"] == original
    assert doc["transactionReviews"] == original_reviews
    assert not doc.get("leaseUntil")


def test_active_lease_never_issues_concurrent_provider_call(monkeypatch):
    doc = adapter._default()
    doc["lease"] = "synthetic-lease"
    doc["leaseUntil"] = NOW + 600
    doc["items"]["opaque-first"] = {"accounts": [{"key": "synthetic-account"}]}
    monkeypatch.setattr(adapter.time, "time", lambda: NOW)
    monkeypatch.setattr(adapter.quota, "_ledger", lambda: ({"items": {
        "opaque-first": {"owner": "synthetic", "access": "stub-first"}
    }}, "tag"))
    monkeypatch.setattr(adapter, "_read", lambda uid: (deepcopy(doc), "revision"))
    monkeypatch.setattr(adapter, "_sync_item", lambda *args: (_ for _ in ()).throw(
        AssertionError("Concurrent sync must never call Plaid")))
    result = adapter.sync_item_now("synthetic", "opaque-first")
    assert result["status"] in ("busy", "updated_just_now")
