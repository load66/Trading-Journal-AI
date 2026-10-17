"""Synthetic owner-authorized fast-sync endpoint tests; no provider requests."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
import bonus_plaid_accounts as adapter

NOW = 1800000000


def test_sync_now_queues_stale_item_once_but_skips_recent_item(monkeypatch):
    first = "synthetic-first"
    second = "synthetic-second"
    doc = adapter._default()
    doc["items"] = {
        first: {"accounts": [{"id": "account-a", "key": adapter._account_key(first, "account-a"),
                              "name": "Synthetic", "bank": "Synthetic", "currency": "USD"}],
                "lastTransactionSyncAt": NOW - 600},
        second: {"accounts": [{"id": "account-b", "key": adapter._account_key(second, "account-b"),
                               "name": "Synthetic", "bank": "Synthetic", "currency": "USD"}],
                 "lastTransactionSyncAt": NOW - 60},
    }
    doc["monitored"] = [adapter._account_key(first, "account-a"),
                        adapter._account_key(second, "account-b")]
    ledger = {"items": {
        first: {"owner": "synthetic-owner", "access": "synthetic-first-access"},
        second: {"owner": "synthetic-owner", "access": "synthetic-second-access"},
    }}
    calls = []
    monkeypatch.setattr(adapter, "owner", lambda request:
                        "synthetic-owner" if request.headers.get("Authorization") == "Bearer fixture"
                        else (_ for _ in ()).throw(HTTPException(401, "Unauthorized")))
    monkeypatch.setattr(adapter.quota, "_ledger", lambda: (ledger, "tag"))
    monkeypatch.setattr(adapter, "_read", lambda uid: (doc, "revision"))
    monkeypatch.setattr(adapter.time, "time", lambda: NOW)
    monkeypatch.setattr(adapter, "sync_item_now", lambda uid, ident: calls.append((uid, ident)))
    app = FastAPI()
    app.include_router(adapter.router)
    client = TestClient(app)
    result = client.post("/api/bonus-tracker/plaid/accounts/sync-now",
                         json={}, headers={"Authorization": "Bearer fixture"})
    assert result.status_code == 200
    assert result.json()["queued"] == 1
    assert calls == [("synthetic-owner", first)]
    assert client.post("/api/bonus-tracker/plaid/accounts/sync-now", json={}).status_code == 401
