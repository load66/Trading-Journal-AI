"""Synthetic-only Plaid webhook ingress tests; never contacts provider."""
import hashlib
import json
import sys
from copy import deepcopy
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import jwt
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi import FastAPI
from fastapi.testclient import TestClient

import bonus_plaid_accounts as plaid

NOW = 1800000000
ITEM = "synthetic-item-id"


def signed(body):
    key = ec.generate_private_key(ec.SECP256R1())
    jwk = json.loads(jwt.algorithms.ECAlgorithm.to_jwk(key.public_key()))
    jwk.update({"kid":"synthetic-kid", "alg":"ES256","use":"sig"})
    token = jwt.encode({"iat":NOW, "request_body_sha256":hashlib.sha256(body).hexdigest()},
                       key, algorithm="ES256", headers={"kid":"synthetic-kid"})
    return token, jwk


def test_unsigned_and_oversize_webhook_are_rejected(monkeypatch):
    app = FastAPI()
    app.include_router(plaid.router)
    client = TestClient(app)
    assert client.post("/api/bonus-tracker/plaid/webhook",
                       content=b'{"webhook_type":"TRANSACTIONS"}').status_code == 401
    assert client.post("/api/bonus-tracker/plaid/webhook",
                       content=b"x"*(64*1024+1),
                       headers={"Plaid-Verification":"fake"}).status_code == 413


def test_verified_webhook_coalesces_bursts_without_new_items(monkeypatch):
    storage = {"items": {ITEM: {
        "owner":"synthetic-owner", "access":"synthetic-access",
    }}}
    stored_calls = []
    def read_ledger():
        return deepcopy(storage), "etag"
    def write_ledger(doc, etag):
        storage.clear()
        storage.update(deepcopy(doc))
        stored_calls.append(1)
        return "etag"
    monkeypatch.setattr(plaid.quota, "_ledger", read_ledger)
    monkeypatch.setattr(plaid.quota, "_write", write_ledger)
    monkeypatch.setattr(plaid.quota, "_ready", lambda: True)
    monkeypatch.setattr(plaid.time, "time", lambda: NOW)
    if hasattr(plaid, "_WEBHOOK_KEY_CACHE"):
        plaid._WEBHOOK_KEY_CACHE.clear()
    events = []
    monkeypatch.setattr(plaid, "_drain_pending_webhook",
                        lambda owner,item: events.append((owner,item)), raising=False)
    app = FastAPI()
    app.include_router(plaid.router)
    client = TestClient(app)
    body = json.dumps({"webhook_type":"TRANSACTIONS",
                       "webhook_code":"SYNC_UPDATES_AVAILABLE",
                       "item_id":ITEM, "environment":"production"}).encode()
    token, key = signed(body)
    monkeypatch.setattr(plaid, "_webhook_key_get", lambda kid: key)
    for _ in range(4):
        response = client.post("/api/bonus-tracker/plaid/webhook", content=body,
                               headers={"Plaid-Verification":token})
        assert response.status_code == 200
    assert events == [("synthetic-owner", ITEM)]
    assert storage["items"][ITEM]["webhookPending"] is True
    assert len(storage["items"]) == 1
    assert all(x.get("access") == "synthetic-access" for x in storage["items"].values())


def test_unknown_and_unrelated_codes_never_enqueue(monkeypatch):
    storage = {"items": {ITEM: {"owner":"synthetic-owner", "access":"synthetic-access"}}}
    monkeypatch.setattr(plaid.quota, "_ledger", lambda: (deepcopy(storage),"etag"))
    monkeypatch.setattr(plaid.quota, "_write", lambda *args: (_ for _ in ()).throw(
        AssertionError("Invalid event must not mutate ledger")))
    monkeypatch.setattr(plaid.time, "time", lambda: NOW)
    if hasattr(plaid, "_WEBHOOK_KEY_CACHE"):
        plaid._WEBHOOK_KEY_CACHE.clear()
    app = FastAPI()
    app.include_router(plaid.router)
    client = TestClient(app)
    for kind,code,ident in [
       ("TRANSACTIONS","RECURRING_TRANSACTIONS_UPDATE",ITEM),
       ("IDENTITY","DEFAULT_UPDATE",ITEM),
       ("TRANSACTIONS","SYNC_UPDATES_AVAILABLE","unknown"),
    ]:
        body=json.dumps({"webhook_type":kind,"webhook_code":code,
                         "item_id":ident,"environment":"production"}).encode()
        token,key=signed(body)
        monkeypatch.setattr(plaid,"_webhook_key_get",lambda kid,k=key:k)
        plaid._WEBHOOK_KEY_CACHE.clear()
        assert client.post("/api/bonus-tracker/plaid/webhook",content=body,
                           headers={"Plaid-Verification":token}).status_code == 200
