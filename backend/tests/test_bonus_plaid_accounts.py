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
        assert endpoint == "/accounts/get" and token == "private-new"
        return {"accounts": [{**account(), "account_id": "new-checking",
                              "name": "One Pay checking"}]}
    monkeypatch.setattr(adapter, "_provider", fetch)

    response = adapter.sync_owner("alice")
    assert len(response["accounts"]) == 2
    assert any(a["bank"] == "One Pay" for a in response["accounts"])
    assert calls == [("/accounts/get", "private-new")]
    assert response["lastSuccess"] == NOW - 120
    assert response["nextSyncAt"] == NOW + 7200
    assert storage["items"]["previous-item"]["accounts"] == prior["accounts"]
    assert not storage["items"]["new-item"]["transactions"]
    assert adapter._account_key("new-item", "new-checking") in storage["monitored"]
    assert "private-new" not in str(response)
    assert adapter.sync_owner("alice")["accounts"] == response["accounts"]
    assert calls == [("/accounts/get", "private-new")], "Never repeat a successful discovery"


def test_failed_new_item_discovery_preserves_existing_data_and_provider_budget(monkeypatch):
    from copy import deepcopy
    prior = adapter._merge({}, "previous-item", "Existing Bank",
                           {"accounts": [account()]}, None, NOW - 90)
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


# BATCH 1 fail-first regressions (synthetic Plaid responses, zero live provider calls).
def test_batch1_plaid_pending_retained_then_replaced_without_duplicates():
    pending = {**fixture(amount=28.35, ident='authorization-1'), 'pending': True}
    prior = adapter._merge({}, 'item-p', 'Synthetic', {'accounts': [account()]},
        ({'authorization-1': pending}, {}, set(), 'cursor-pending'), NOW)
    assert 'authorization-1' in prior['transactions'], (
        'A pending Plaid authorization must be retained in history')
    posted = {**fixture(amount=28.35, ident='posted-1'),
              'pending_transaction_id': 'authorization-1'}
    final = adapter._merge(prior, 'item-p', 'Synthetic', {'accounts': [account()]},
        ({'posted-1': posted}, {}, set(), 'cursor-posted'), NOW+3600)
    assert set(final['transactions']) == {'posted-1'}, (
        'Posted transaction replaces its pending ID, without a duplicate')


def test_batch1_plaid_history_survives_90_day_retention_cutoff():
    older = {**fixture(ident='historical-120d'), 'date': '2026-06-01'}
    original = adapter._merge({}, 'item-h', 'Synthetic', {'accounts': [account()]},
        ({'historical-120d': older}, {}, set(), 'cursor-old'), NOW - 105*adapter.DAY)
    assert 'historical-120d' in original['transactions']
    updated = adapter._merge(original, 'item-h', 'Synthetic', {'accounts': [account()]},
        ({'current': fixture(ident='current')}, {}, set(), 'cursor-now'), NOW)
    assert 'historical-120d' in updated['transactions'], (
        'Plaid cursor updates must not erase historical observations after 90 days')


def _batch1_plaid_in_memory(monkeypatch):
    from copy import deepcopy
    import bonus_plaid_trial as quota
    account_key = adapter._account_key('item-a', 'example')
    activity = adapter._default()
    activity['items'] = {'item-a': adapter._merge(
        {}, 'item-a', 'Synthetic', {'accounts': [account()]}, None, NOW)}
    activity['monitored'] = []
    ledger = {'items': {'item-a': {'owner': 'alice', 'institution': 'Synthetic',
                                  'access': 'synthetic-token'}}}
    monkeypatch.setattr(quota, '_ledger', lambda: (ledger, 'ledger-tag'))
    monkeypatch.setattr(adapter, '_read', lambda uid: (deepcopy(activity), 'activity-tag'))
    def store(uid, document, etag):
        activity.clear()
        activity.update(deepcopy(document))
        return 'activity-tag'
    monkeypatch.setattr(adapter, '_write', store)
    monkeypatch.setattr(adapter.time, 'time', lambda: NOW)
    monkeypatch.setattr(adapter, '_provider', lambda endpoint, access, **kwargs:
                        {'accounts': [account()]})
    monkeypatch.setattr(adapter, '_sync_item', lambda access, cursor:
                        ({'synthetic-tx': fixture(ident='synthetic-tx')}, {},
                         set(), 'synthetic-cursor'))
    return activity, account_key


def test_batch1_plaid_opt_out_survives_sync_and_purges_activity(monkeypatch):
    activity, key = _batch1_plaid_in_memory(monkeypatch)
    result = adapter.sync_owner('alice')
    assert key not in result['monitoredAccounts'], 'Explicitly off must stay off'
    assert not activity['items']['item-a']['transactions'], (
        'Transactions for disabled accounts must not be retained')


def test_batch1_plaid_sync_schedules_safe_four_hour_refresh(monkeypatch):
    activity, key = _batch1_plaid_in_memory(monkeypatch)
    activity['monitored'] = [key]
    result = adapter.sync_owner('alice')
    assert result['lastSuccess'] == NOW
    assert NOW < result['nextSyncAt'] <= NOW + 4 * 3600, (
        'Existing Plaid Trial Items may use a conservative four-hour check cadence')
