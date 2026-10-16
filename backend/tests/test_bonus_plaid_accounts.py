"""Unit tests for the inactive Plaid read-only adapter using synthetic data."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pytest
from fastapi import HTTPException
import bonus_plaid_accounts as adapter

NOW = 1792134000

def fixture(amount=-500.00, name="Example Direct Deposit", ident="tx1"):
    return {"transaction_id":ident,"account_id":"example",
            "amount":amount,"name":name,"date":"2026-10-15",
            "pending":False,"iso_currency_code":"USD"}

def account():
    return {"account_id":"example","name":"Example checking",
            "type":"depository","subtype":"checking",
            "balances":{"current":500,"available":None,"iso_currency_code":"USD"}}

def test_plaid_credit_and_debit_signs():
    credit=adapter._transaction(fixture(),NOW)
    debit=adapter._transaction(fixture(amount=15.75,name="Example Fee"),NOW)
    assert credit["amount"]=="500"
    assert debit["amount"]=="-15.75"
    assert credit["kind"]=="deposit"  # deposits become candidates via conservative signals
    assert debit["kind"]=="withdrawal"

def test_invalid_money_and_date_fail_closed():
    for value in (float("nan"),None,"inf"):
        with pytest.raises(HTTPException):
            adapter._transaction(fixture(amount=value),NOW)
    with pytest.raises(HTTPException):
        adapter._transaction({**fixture(),"date":"2026-99-99"},NOW)

def test_opaque_account_identity_is_provider_scoped():
    assert adapter._account_key("item-one","account-one") != adapter._account_key("item-two","account-one")
    assert len(adapter._account_key("item-one","account-one"))==64

def test_transaction_revision_and_provider_removal():
    prior=adapter._merge({},"example-item","Example",{"accounts":[account()]},(
        {"tx1":fixture()}, {},set(),"cursor1"),NOW)
    updated=adapter._merge(prior,"example-item","Example",{"accounts":[account()]},(
        {},{"tx1":fixture(amount=-520)},set(),"cursor2"),NOW+1)
    assert updated["transactions"]["tx1"]["amount"]=="520"
    assert updated["transactions"]["tx1"]["changedAt"]==NOW+1
    assert updated["transactions"]["tx1"]["revisions"][0]["amount"]=="500"
    removed=adapter._merge(updated,"example-item","Example",{"accounts":[account()]},(
        {},{},{"tx1"},"cursor3"),NOW+2)
    assert not removed["transactions"]
    assert removed["cursor"]=="cursor3"

def test_readonly_provider_route_allowlist():
    assert adapter.ROUTES == frozenset(("/accounts/get","/transactions/sync"))
    assert adapter.MAX_PAGES<=25


def test_paginated_delta_cursor_and_removal(monkeypatch):
    seen=[]
    def provider(endpoint, access, **kwargs):
        assert endpoint=="/transactions/sync"
        seen.append(kwargs["cursor"])
        if kwargs["cursor"]=="":
            return {"added":[fixture(ident="a")],"modified":[],"removed":[],
                    "next_cursor":"next1","has_more":True}
        return {"added":[],"modified":[fixture(ident="b")],
                "removed":[{"transaction_id":"retired"}],
                "next_cursor":"final","has_more":False}
    monkeypatch.setattr(adapter,"_provider",provider)
    added,modified,removed,cursor=adapter._sync_item("synthetic","")
    assert seen==["","next1"]
    assert list(added)==["a"] and list(modified)==["b"]
    assert removed=={"retired"}
    assert cursor=="final"

def test_incomplete_pagination_never_claims_success(monkeypatch):
    monkeypatch.setattr(adapter,"MAX_PAGES",2)
    monkeypatch.setattr(adapter,"_provider",lambda *args,**kwargs:{
        "added":[],"modified":[],"removed":[],"next_cursor":"loop","has_more":True})
    with pytest.raises(HTTPException) as error:
        adapter._sync_item("synthetic","")
    assert error.value.status_code==502

def test_provider_redacts_ids_from_public_snapshot():
    key=adapter._account_key("item1","example")
    row=adapter._merge({},"item1","Example",{"accounts":[account()]},(
        {"tx1":fixture()}, {},set(),"cursor1"),NOW)
    saved={"version":1,"items":{"item1":row},"monitored":[key],"lastSuccess":NOW}
    ledger={"items":{"item1":{"owner":"alice","access":"private","institution":"Example"}}}
    shown=adapter._public(saved,ledger,"alice")
    assert len(shown["accounts"])==1 and len(shown["transactions"][key])==1
    serialized=__import__("json").dumps(shown)
    assert "private" not in serialized and "item1" not in serialized
    assert '"example"' not in serialized and '"tx1"' not in serialized
    assert next(iter(shown["transactions"][key].values()))["signals"]==["possible_direct_deposit"]
    assert shown["authoritativeMilestones"] is False
    assert adapter._public(saved,ledger,"bob")["accounts"]==[]


def test_new_connection_syncs_every_account_automatically_and_only_once_daily(monkeypatch):
    from fastapi import FastAPI, HTTPException
    from fastapi.testclient import TestClient
    from copy import deepcopy
    import bonus_plaid_trial as quota

    first_key = adapter._account_key("sample-item", "example")
    second_key = adapter._account_key("sample-item", "unselected")
    storage = adapter._default()
    items = {"sample-item": {"owner": "alice", "institution": "Synthetic Bank", "access": "fake-access"}}
    monkeypatch.setattr(quota, "_ready", lambda: True)
    monkeypatch.setattr(quota, "_ledger", lambda: ({"items": items}, "tag"))
    monkeypatch.setattr(adapter, "owner", lambda request:
                        "alice" if request.headers.get("Authorization") == "Bearer example"
                        else (_ for _ in ()).throw(HTTPException(401, "Not signed in")))
    monkeypatch.setattr(adapter, "_read", lambda uid: (deepcopy(storage), "tag"))
    def save(uid, doc, tag):
        storage.clear()
        storage.update(deepcopy(doc))
        return "tag"
    monkeypatch.setattr(adapter, "_write", save)
    monkeypatch.setattr(adapter.time, "time", lambda: NOW)
    calls = []
    def provider(endpoint, token, **kwargs):
        calls.append(endpoint)
        if endpoint == "/accounts/get":
            return {"accounts": [account(), {**account(), "account_id": "unselected"}]}
        return {"added": [fixture(), {**fixture(ident="private-other"), "account_id": "unselected"}],
                "modified": [], "removed": [], "next_cursor": "latest", "has_more": False}
    monkeypatch.setattr(adapter, "_provider", provider)
    app = FastAPI()
    app.include_router(adapter.router)
    client = TestClient(app)
    headers = {"Authorization": "Bearer example"}
    response = client.post("/api/bonus-tracker/plaid/accounts/sync", headers=headers)
    assert response.status_code == 200
    payload = response.json()
    assert len(payload["accounts"]) == 2
    assert set(payload["monitoredAccounts"]) == {first_key, second_key}
    assert len(payload["transactions"][first_key]) == 1
    assert len(payload["transactions"][second_key]) == 1
    assert all(len(a["connectionHandle"]) == 64 for a in payload["accounts"])
    assert calls == ["/accounts/get", "/transactions/sync"]
    assert storage["transactionNextSyncAt"] == NOW + adapter.DAY
    assert storage["nextSyncAt"] == NOW + adapter.DAY
    assert payload["authoritativeMilestones"] is False
    repeat = client.post("/api/bonus-tracker/plaid/accounts/sync", headers=headers)
    assert repeat.status_code == 200
    assert calls == ["/accounts/get", "/transactions/sync"]
    assert adapter._public(storage, {"items": items}, "other-owner")["accounts"] == []


def test_new_bank_discovers_accounts_without_repeating_daily_transaction_sync(monkeypatch):
    from copy import deepcopy
    old_key = adapter._account_key("previous-item", "example")
    prior = adapter._merge({}, "previous-item", "Existing Bank",
                           {"accounts": [account()]}, None, NOW - 120)
    prior["lastTransactionAttemptAt"] = NOW - 120
    prior["nextTransactionSyncAt"] = NOW + adapter.DAY
    storage = adapter._default()
    storage["items"]["previous-item"] = prior
    storage["monitored"] = [old_key]
    storage["lastSuccess"] = NOW - 120
    storage["nextSyncAt"] = NOW + 7200
    quota = {"items": {
        "previous-item": {"owner": "alice", "institution": "Existing Bank",
                          "access": "private-old"},
        "new-item": {"owner": "alice", "institution": "One Pay",
                     "access": "private-new"}
    }}
    monkeypatch.setattr(adapter.quota, "_ledger", lambda: (quota, "quota-tag"))
    monkeypatch.setattr(adapter, "_read", lambda uid: (deepcopy(storage), "data-tag"))
    def save(uid, candidate, etag):
        storage.clear()
        storage.update(deepcopy(candidate))
        return "data-tag"
    monkeypatch.setattr(adapter, "_write", save)
    monkeypatch.setattr(adapter.time, "time", lambda: NOW)
    calls = []
    def fetch(endpoint, token, **kwargs):
        calls.append((endpoint, token))
        assert token == "private-new", "Existing Items must not be re-synced"
        if endpoint == "/accounts/get":
            return {"accounts": [{**account(), "account_id": "new-checking",
                                  "name": "One Pay checking"}]}
        assert endpoint == "/transactions/sync" and kwargs.get("cursor") == ""
        return {"added": [{**fixture(ident="onepay-posted"), "account_id": "new-checking"}],
                "modified": [], "removed": [], "next_cursor": "new-cursor", "has_more": False}
    monkeypatch.setattr(adapter, "_provider", fetch)

    response = adapter.sync_owner("alice")
    assert len(response["accounts"]) == 2
    assert any(a["bank"] == "One Pay" for a in response["accounts"])
    assert calls == [("/accounts/get", "private-new"), ("/transactions/sync", "private-new")]
    assert response["lastSuccess"] == NOW
    assert response["nextSyncAt"] == NOW + 7200
    assert storage["items"]["previous-item"]["accounts"] == prior["accounts"]
    assert len(storage["items"]["new-item"]["transactions"]) == 1
    assert storage["items"]["new-item"]["lastTransactionSyncAt"] == NOW
    assert storage["items"]["new-item"]["nextTransactionSyncAt"] == NOW + adapter.DAY
    assert response["accounts"][1]["transactionSyncStatus"] == "ready"
    assert adapter._account_key("new-item", "new-checking") in storage["monitored"]
    assert "private-new" not in str(response)
    assert adapter.sync_owner("alice")["accounts"] == response["accounts"]
    assert calls == [("/accounts/get", "private-new"), ("/transactions/sync", "private-new")], "Never repeat the first transaction sync inside the daily budget"


def test_failed_new_item_discovery_preserves_existing_data_and_provider_budget(monkeypatch):
    from copy import deepcopy
    prior = adapter._merge({}, "previous-item", "Existing Bank",
                           {"accounts": [account()]}, None, NOW - 90)
    prior["lastTransactionAttemptAt"] = NOW - 90
    prior["nextTransactionSyncAt"] = NOW + adapter.DAY
    storage = adapter._default()
    storage["items"]["previous-item"] = prior
    storage["nextSyncAt"] = NOW + 7200
    quota = {"items": {
        "previous-item": {"owner": "alice", "institution": "Existing Bank",
                          "access": "private-old"},
        "new-item": {"owner": "alice", "institution": "One Pay",
                     "access": "private-new"}
    }}
    monkeypatch.setattr(adapter.quota, "_ledger", lambda: (quota, "quota-tag"))
    monkeypatch.setattr(adapter, "_read", lambda uid: (deepcopy(storage), "data-tag"))
    def save(uid, candidate, etag):
        storage.clear()
        storage.update(deepcopy(candidate))
        return "data-tag"
    monkeypatch.setattr(adapter, "_write", save)
    monkeypatch.setattr(adapter.time, "time", lambda: NOW)
    calls = []
    def failed_provider(endpoint, token, **kwargs):
        calls.append(endpoint)
        raise HTTPException(502, "Account discovery temporarily unavailable.")
    monkeypatch.setattr(adapter, "_provider", failed_provider)
    response = adapter.sync_owner("alice")
    assert response["accounts"][0]["bank"] == "Existing Bank"
    assert len(response["accounts"]) == 1
    assert storage["items"]["previous-item"] == prior
    assert "new-item" not in storage["items"]
    assert storage["nextSyncAt"] == NOW + 7200
    assert storage["nextNewItemDiscoveryAt"] == NOW + adapter.DAY
    assert adapter.sync_owner("alice")["accounts"] == response["accounts"]
    assert calls == ["/accounts/get"], "Never retry repeatedly within the daily budget"


def test_pending_activity_is_kept_and_replaced_by_posted_with_link():
    initial = adapter._merge(
        {}, "onepay-item", "OnePay", {"accounts": [account()]},
        ({"pending1": {**fixture(amount=24.85, ident="pending1"), "pending": True}},
         {}, set(), "first-cursor"), NOW)
    assert initial["transactions"]["pending1"]["pending"] is True
    assert initial["transactionSyncStatus"] == "pending_only"
    ledger = {"items": {"onepay-item": {"owner": "alice", "access": "private",
                                        "institution": "OnePay"}}}
    key = adapter._account_key("onepay-item", "example")
    public = adapter._public({"version": 1, "items": {"onepay-item": initial},
        "monitored": [key], "lastSuccess": NOW}, ledger, "alice")
    rows = list(public["transactions"][key].values())
    assert len(rows) == 1 and rows[0]["pending"] is True
    assert rows[0]["signals"] == [], "Pending activity must never assert a bonus or payroll milestone"
    assert public["accounts"][0]["transactionSyncStatus"] == "pending_only"
    assert "private" not in str(public)
    posted = adapter._merge(initial, "onepay-item", "OnePay", {"accounts": [account()]},
        ({"posted1": {**fixture(amount=24.85, ident="posted1"), "pending": False,
                     "pending_transaction_id": "pending1"}}, {}, set(), "next-cursor"), NOW + 10)
    assert list(posted["transactions"]) == ["posted1"]
    assert posted["transactionSyncStatus"] == "ready"


def test_discovered_bank_gets_initial_sync_during_other_bank_cooldown(monkeypatch):
    from copy import deepcopy
    storage = adapter._default()
    storage["nextSyncAt"] = NOW + 3600
    storage["nextNewItemDiscoveryAt"] = NOW + adapter.DAY
    old = adapter._merge({}, "new-item", "OnePay", {"accounts": [account()]}, None, NOW - 70)
    storage["items"]["new-item"] = old
    key = adapter._account_key("new-item", "example")
    storage["monitored"] = [key]
    quota = {"items": {"new-item": {"owner": "alice", "institution": "OnePay", "access": "token"}}}
    monkeypatch.setattr(adapter.quota, "_ledger", lambda: (quota, "tag"))
    monkeypatch.setattr(adapter, "_read", lambda uid: (deepcopy(storage), "tag"))
    def write(uid, doc, tag):
        storage.clear()
        storage.update(deepcopy(doc))
        return "tag"
    monkeypatch.setattr(adapter, "_write", write)
    monkeypatch.setattr(adapter.time, "time", lambda: NOW)
    calls = []
    def provider(endpoint, access, **kwargs):
        calls.append(endpoint)
        assert access == "token"
        if endpoint == "/accounts/get":
            return {"accounts": [account()]}
        assert endpoint == "/transactions/sync"
        return {"added": [], "modified": [], "removed": [],
                "next_cursor": "initial-cursor", "has_more": False}
    monkeypatch.setattr(adapter, "_provider", provider)
    response = adapter.sync_owner("alice")
    assert calls == ["/transactions/sync", "/accounts/get"] or calls == ["/accounts/get", "/transactions/sync"]
    assert storage["items"]["new-item"]["cursor"] == "initial-cursor"
    assert response["accounts"][0]["transactionSyncStatus"] == "awaiting_provider_data"
    assert response["accounts"][0]["transactionNextSyncAt"] == NOW + adapter.DAY
    assert storage["nextSyncAt"] == NOW + 3600, "Another bank's owner-wide quota must remain intact"
    adapter.sync_owner("alice")
    assert len(calls) == 2, "Refreshing must never duplicate the first Plaid transaction call"


def test_initial_provider_error_preserves_discovery_and_suspends_retry(monkeypatch):
    from copy import deepcopy
    storage = adapter._default()
    storage["nextSyncAt"] = NOW + 7200
    quota = {"items": {"new-item": {"owner": "alice", "institution": "OnePay", "access": "secret"}}}
    monkeypatch.setattr(adapter.quota, "_ledger", lambda: (quota, "tag"))
    monkeypatch.setattr(adapter, "_read", lambda uid: (deepcopy(storage), "tag"))
    def write(uid, doc, tag):
        storage.clear()
        storage.update(deepcopy(doc))
        return "tag"
    monkeypatch.setattr(adapter, "_write", write)
    monkeypatch.setattr(adapter.time, "time", lambda: NOW)
    calls = []
    def provider(endpoint, token, **kwargs):
        calls.append(endpoint)
        if endpoint == "/accounts/get":
            return {"accounts": [account()]}
        raise HTTPException(502, "First transaction history is not ready.")
    monkeypatch.setattr(adapter, "_provider", provider)
    result = adapter.sync_owner("alice")
    assert len(result["accounts"]) == 1
    assert result["accounts"][0]["transactionSyncStatus"] == "needs_attention"
    assert result["accounts"][0]["transactionNextSyncAt"] == NOW + adapter.DAY
    assert "secret" not in str(result)
    assert calls == ["/accounts/get", "/transactions/sync"]
    adapter.sync_owner("alice")
    assert calls == ["/accounts/get", "/transactions/sync"], "Do not retry a failed new-Item sync immediately"


def test_two_new_banks_added_same_day_get_independent_first_syncs(monkeypatch):
    from copy import deepcopy
    storage = adapter._default()
    storage["nextSyncAt"] = NOW + 7200
    ledger = {"items": {
        "onepay-item": {"owner": "alice", "institution": "OnePay", "access": "onepay"},
        "xmoney-item": {"owner": "alice", "institution": "X Money", "access": "xmoney"}
    }}
    monkeypatch.setattr(adapter.quota, "_ledger", lambda: (ledger, "tag"))
    monkeypatch.setattr(adapter, "_read", lambda uid: (deepcopy(storage), "tag"))
    def persist(uid, doc, tag):
        storage.clear()
        storage.update(deepcopy(doc))
        return "tag"
    monkeypatch.setattr(adapter, "_write", persist)
    monkeypatch.setattr(adapter.time, "time", lambda: NOW)
    calls = []
    def provider(endpoint, token, **kwargs):
        calls.append((endpoint, token))
        if endpoint == "/accounts/get":
            return {"accounts": [{**account(), "account_id": token + "-checking"}]}
        return {"added": [{**fixture(ident=token + "-deposit"),
                            "account_id": token + "-checking"}],
                "modified": [], "removed": [],
                "next_cursor": token + "-cursor", "has_more": False}
    monkeypatch.setattr(adapter, "_provider", provider)
    # First link in the provider window.
    del ledger["items"]["xmoney-item"]
    first = adapter.sync_owner("alice")
    assert len(first["accounts"]) == 1
    assert len(first["transactions"][adapter._account_key("onepay-item", "onepay-checking")]) == 1
    assert calls == [("/accounts/get", "onepay"), ("/transactions/sync", "onepay")]
    # Second independent bank links minutes later while owner-wide clock is locked.
    ledger["items"]["xmoney-item"] = {"owner": "alice", "institution": "X Money", "access": "xmoney"}
    second = adapter.sync_owner("alice")
    assert len(second["accounts"]) == 2
    assert len(second["transactions"][adapter._account_key("xmoney-item", "xmoney-checking")]) == 1
    assert calls == [("/accounts/get", "onepay"), ("/transactions/sync", "onepay"),
                     ("/accounts/get", "xmoney"), ("/transactions/sync", "xmoney")]
    assert storage["nextSyncAt"] == NOW + 7200
    assert not storage["newItemDiscoveryAttempts"]
    adapter.sync_owner("alice")
    assert len(calls) == 4, "Neither linked bank may re-consume its same-day first-sync budget"


def test_automatic_scheduler_wakes_for_uninitialized_item_during_cooldown(monkeypatch):
    from copy import deepcopy
    ledger = {"items": {
        "existing": {"owner": "alice", "access": "existing", "institution": "Old bank"},
        "new-onepay": {"owner": "alice", "access": "new", "institution": "OnePay"}
    }}
    doc = adapter._default()
    doc["nextSyncAt"] = NOW + 7200
    doc["items"]["existing"] = adapter._merge(
        {}, "existing", "Old bank", {"accounts": [account()]}, None, NOW - 60)
    doc["items"]["existing"]["cursor"] = "old-cursor"
    monkeypatch.setattr(adapter.quota, "_ready", lambda: True)
    monkeypatch.setattr(adapter.quota, "_read", lambda: (ledger, "tag"))
    monkeypatch.setattr(adapter.quota, "_ledger", lambda: (ledger, "tag"))
    monkeypatch.setattr(adapter, "_read", lambda uid: (deepcopy(doc), "tag"))
    monkeypatch.setattr(adapter.time, "time", lambda: NOW)
    called = []
    monkeypatch.setattr(adapter, "sync_owner", lambda uid: called.append(uid))
    adapter.scheduled_sync_once()
    assert called == ["alice"], "A newly linked bank must be discovered without a user refresh"
    called.clear()
    doc["newItemDiscoveryAttempts"]["new-onepay"] = NOW + 86400
    adapter.scheduled_sync_once()
    assert called == [], "An attempted bank must not repeatedly consume provider calls"
    doc["items"]["new-onepay"] = adapter._merge({}, "new-onepay", "OnePay",
        {"accounts": [account()]}, None, NOW)
    doc["newItemDiscoveryAttempts"].clear()
    adapter.scheduled_sync_once()
    assert called == ["alice"], "A discovered bank lacking first transactions must be initialized"


def test_one_bank_transaction_failure_does_not_rollback_other_bank(monkeypatch):
    from copy import deepcopy
    storage = adapter._default()
    storage["nextSyncAt"] = 0
    ledger = {"items": {
        "old-failing": {"owner": "alice", "institution": "Unavailable", "access": "bad"},
        "good-bank": {"owner": "alice", "institution": "Available", "access": "good"}
    }}
    monkeypatch.setattr(adapter.quota, "_ledger", lambda: (ledger, "tag"))
    monkeypatch.setattr(adapter, "_read", lambda uid: (deepcopy(storage), "tag"))
    def persist(uid, doc, tag):
        storage.clear()
        storage.update(deepcopy(doc))
        return "tag"
    monkeypatch.setattr(adapter, "_write", persist)
    monkeypatch.setattr(adapter.time, "time", lambda: NOW)
    def provider(endpoint, token, **kwargs):
        if endpoint == "/accounts/get":
            return {"accounts": [{**account(), "account_id": token + "-checking"}]}
        if token == "bad":
            raise HTTPException(502, "Institution unavailable")
        return {"added": [{**fixture(ident="good-deposit"), "account_id": "good-checking"}],
                "modified": [], "removed": [], "next_cursor": "good-cursor", "has_more": False}
    monkeypatch.setattr(adapter, "_provider", provider)
    result = adapter.sync_owner("alice")
    by_name = {x["bank"]: x for x in result["accounts"]}
    assert by_name["Unavailable"]["transactionSyncStatus"] == "needs_attention"
    assert by_name["Available"]["transactionSyncStatus"] == "ready"
    assert len(result["transactions"][adapter._account_key("good-bank", "good-checking")]) == 1
    assert storage["items"]["good-bank"]["cursor"] == "good-cursor"
    assert storage["items"]["old-failing"]["nextTransactionSyncAt"] == NOW + adapter.DAY
