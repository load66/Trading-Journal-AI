"""Provider-independent campaign evidence invariants applied to Plaid."""
import copy
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
import bonus_plaid_accounts as adapter
import bonus_plaid_trial as quota
from bonus_simplefin import public_ref
from test_bonus_plaid_accounts import account, fixture, NOW


@pytest.fixture
def setup(monkeypatch):
    key = adapter._account_key("item", "example")
    item = adapter._merge({}, "item", "Example", {"accounts": [account()]},
                          ({"tx1": fixture()}, {}, set(), "cursor"), NOW)
    state = {**adapter._default(), "items": {"item": item}, "monitored": [key], "lastSuccess": NOW}
    ledger = {"items": {"item": {"owner": "alice", "access": "never-return", "institution": "Example"}}}
    monkeypatch.setattr(adapter, "owner", lambda req: "alice" if req.headers.get("Authorization") == "Bearer test"
                        else (_ for _ in ()).throw(HTTPException(401, "Not signed in")))
    monkeypatch.setattr(quota, "_ready", lambda: True)
    monkeypatch.setattr(quota, "_ledger", lambda: (copy.deepcopy(ledger), "ledger"))
    monkeypatch.setattr(adapter, "_read", lambda uid: (copy.deepcopy(state), "etag"))
    def write(uid, doc, tag):
        assert uid == "alice" and tag == "etag"
        state.clear(); state.update(copy.deepcopy(doc))
        return "etag"
    monkeypatch.setattr(adapter, "_write", write)
    monkeypatch.setattr(adapter.time, "time", lambda: NOW)
    app = FastAPI(); app.include_router(adapter.router)
    client = TestClient(app)
    def put(path, payload):
        return client.put("/api/bonus-tracker/plaid/accounts/" + path, json=payload,
                          headers={"Authorization": "Bearer test"})
    mapping = {"entryId": "cycle-one", "bank": "Example", "opened": "2026-10-01", "productId": "checking"}
    review = {"account": key, "transaction": public_ref(key, "tx1"),
              **{k: mapping[k] for k in ("entryId", "bank", "opened")},
              "decision": "reviewed", "requirementId": "payroll"}
    return put, state, ledger, key, mapping, review


def test_exact_cycle_review_persists_and_product_change_clears_only_requirement(setup):
    put, state, ledger, key, mapping, review = setup
    assert put("selection", {"mappings": {key: mapping}}).status_code == 200
    result = put("evidence-review", review)
    assert result.status_code == 200
    assert result.json()["evidenceReviews"][key][review["transaction"]]["requirementId"] == "payroll"
    corrected = put("selection", {"mappings": {key: {**mapping, "productId": "savings"}}})
    assert corrected.status_code == 200
    receipt = corrected.json()["evidenceReviews"][key][review["transaction"]]
    assert receipt["status"] == "reviewed" and receipt["requirementId"] is None
    assert corrected.json()["transactions"][key][review["transaction"]]["amount"] == "500"
    assert corrected.json()["authoritativeMilestones"] is False
    replacement = put("selection", {"mappings": {key: {**mapping, "entryId": "cycle-two"}}})
    assert replacement.status_code == 200 and not replacement.json()["evidenceReviews"]
    assert put("evidence-review", review).status_code == 400


def test_corrections_and_removals_invalidate_review_and_category(setup):
    put, state, ledger, key, mapping, review = setup
    put("selection", {"mappings": {key: mapping}})
    assert put("evidence-review", review).status_code == 200
    assert put("transaction-review", {"account": key, "transaction": review["transaction"], "category": "income"}).status_code == 200
    row = state["items"]["item"]["transactions"]["tx1"]
    row["amount"] = "501"
    shown = adapter._public(state, ledger, "alice")
    assert not shown["evidenceReviews"]
    assert shown["transactions"][key][review["transaction"]]["categorySource"] == "automatic"
    del state["items"]["item"]["transactions"]["tx1"]
    assert put("evidence-review", review).status_code == 400


def test_monitoring_consent_foreign_accounts_scope_and_busy_writes(setup):
    put, state, ledger, key, mapping, review = setup
    assert put("selection", {"mappings": {"f" * 64: mapping}}).status_code == 400
    state["monitored"] = []
    assert put("selection", {"mappings": {key: mapping}}).status_code == 400
    state["monitored"] = [key]
    assert put("selection", {"mappings": {key: mapping}}).status_code == 200
    state["leaseUntil"] = NOW + 10
    assert put("evidence-review", review).status_code == 409
    state.pop("leaseUntil")
    hidden = adapter._public(state, ledger, "bob")
    assert hidden["accounts"] == [] and not hidden["mappings"] and not hidden["monitoredAccounts"]
    assert put("monitoring", {"accounts": []}).status_code == 200
    assert not state["mappings"] and not state["evidenceReviews"]


def test_daily_scheduler_never_links_or_upgrades(monkeypatch):
    calls = []
    monkeypatch.setattr(quota, "_ready", lambda: True)
    monkeypatch.setattr(quota, "_read", lambda: ({"items": {"a": {"owner": "one"}, "b": {"owner": "one"}, "c": {"owner": "two"}}}, "tag"))
    monkeypatch.setattr(adapter, "_read", lambda uid: ({"monitored": ["key"] if uid == "one" else [], "nextSyncAt": 0}, "tag"))
    monkeypatch.setattr(adapter, "sync_owner", calls.append)
    adapter.scheduled_sync_once()
    assert set(calls) == {"one", "two"}
    monkeypatch.setattr(quota, "_ready", lambda: False)
    adapter.scheduled_sync_once()
    assert set(calls) == {"one", "two"}


def test_successful_sync_permanently_discards_obsolete_receipts(setup, monkeypatch):
    put, state, ledger, key, mapping, review = setup
    put("selection", {"mappings": {key: mapping}})
    put("evidence-review", review)
    put("transaction-review", {"account": key, "transaction": review["transaction"], "category": "income"})
    monkeypatch.setattr(adapter, "_provider", lambda *args, **kwargs: {"accounts": [account()]})
    monkeypatch.setattr(adapter, "_sync_item", lambda *args: ({}, {}, {"tx1"}, "next"))
    adapter.sync_owner("alice")
    assert not state["evidenceReviews"].get(key) and not state["transactionReviews"].get(key)
    # Reappearance with the same ID and content needs a fresh review.
    state["items"]["item"]["transactions"]["tx1"] = adapter._transaction(fixture(), NOW)
    assert not adapter._public(state, ledger, "alice")["evidenceReviews"]
