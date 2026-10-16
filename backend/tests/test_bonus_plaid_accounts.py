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


def test_authenticated_readonly_routes_and_monitoring(monkeypatch):
    from fastapi import FastAPI, HTTPException
    from fastapi.testclient import TestClient
    from copy import deepcopy
    from types import SimpleNamespace
    import bonus_plaid_trial as quota
    key=adapter._account_key("sample-item","example")
    storage={"version":1,"items":{},"monitored":[],"lastSuccess":None,"nextSyncAt":0}
    item={"sample-item":{"owner":"alice","institution":"Synthetic Bank","access":"fake-access"}}
    monkeypatch.setattr(quota,"_ready",lambda:True)
    monkeypatch.setattr(quota,"_ledger",lambda:({"items":item}, "tag"))
    monkeypatch.setattr(adapter,"owner",lambda request:
        "alice" if request.headers.get("Authorization")=="Bearer example"
        else (_ for _ in ()).throw(HTTPException(401,"Not signed in")))
    monkeypatch.setattr(adapter,"_read",lambda uid:(deepcopy(storage),"tag"))
    def save(uid, doc, tag):
        storage.clear()
        storage.update(deepcopy(doc))
        return "tag"
    monkeypatch.setattr(adapter,"_write",save)
    monkeypatch.setattr(adapter.time,"time",lambda:NOW)
    calls=[]
    def provider(endpoint, token, **kwargs):
        calls.append(endpoint)
        if endpoint=="/accounts/get":
            return {"accounts":[account(), {**account(),"account_id":"unselected"}]}
        return {"added":[fixture(), {**fixture(ident="private-other"),"account_id":"unselected"}],"modified":[],"removed":[],
                "next_cursor":"latest","has_more":False}
    monkeypatch.setattr(adapter,"_provider",provider)
    app=FastAPI()
    app.include_router(adapter.router)
    client=TestClient(app)
    h={"Authorization":"Bearer example"}
    assert client.get("/api/bonus-tracker/plaid/accounts").status_code==401
    assert client.put("/api/bonus-tracker/plaid/accounts/monitoring",
        json={"accounts":[key]},headers=h).status_code==400
    first=client.post("/api/bonus-tracker/plaid/accounts/sync",headers=h)
    assert first.status_code==200 and len(first.json()["accounts"])==2
    assert calls==["/accounts/get"]
    storage["items"]["sample-item"]["cursor"]="cursor-from-previous-unmonitored-period"
    assert client.put("/api/bonus-tracker/plaid/accounts/monitoring",
        json={"accounts":[key,key]},headers=h).status_code==400
    enabled=client.put("/api/bonus-tracker/plaid/accounts/monitoring",
        json={"accounts":[key]},headers=h)
    assert enabled.status_code==200
    assert storage["items"]["sample-item"]["cursor"]=="", "Newly monitored accounts must backfill rather than skip history"
    result=client.post("/api/bonus-tracker/plaid/accounts/sync",headers=h)
    assert result.status_code==200
    assert calls==["/accounts/get","/accounts/get","/transactions/sync"]
    assert len(result.json()["transactions"][key])==1
    assert result.json()["authoritativeMilestones"] is False
    assert set(storage["items"]["sample-item"]["transactions"]) == {"tx1"}, \
        "Unselected account activity must never be retained in private storage"
    assert storage["transactionNextSyncAt"] == NOW + adapter.DAY
    # Neither a no-op save nor deselect/reselect may buy another provider call.
    for selected in ([key], [], [key]):
        assert client.put("/api/bonus-tracker/plaid/accounts/monitoring",
            json={"accounts":selected},headers=h).status_code==200
        assert storage["nextSyncAt"] == NOW + adapter.DAY
        assert client.post("/api/bonus-tracker/plaid/accounts/sync",headers=h).status_code==200
        assert len(calls)==3

    again=client.post("/api/bonus-tracker/plaid/accounts/sync",headers=h)
    assert again.status_code==200 and len(calls)==3
    assert client.put("/api/bonus-tracker/plaid/accounts/monitoring",
        json={"accounts":[]},headers=h).status_code==200
    assert client.get("/api/bonus-tracker/plaid/accounts",headers=h).json()["transactions"]=={}
