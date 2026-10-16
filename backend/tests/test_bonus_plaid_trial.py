"""No live Plaid calls; synthetic Trial quota and encryption regression tests."""
import io
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pytest
from cryptography.fernet import Fernet
from botocore.exceptions import ClientError
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
import bonus_plaid_trial as plaid

class Memory:
    bucket = "synthetic"
    def __init__(self):
        self.items = {}
        self.rev = 0
        self.client = self
    def get_object(self, Bucket, Key):
        if Key not in self.items:
            raise ClientError({"Error": {"Code": "NoSuchKey"}}, "GetObject")
        body, tag = self.items[Key]
        return {"Body": io.BytesIO(body), "ETag": tag}
    def put_object(self, Bucket, Key, Body, **options):
        current = self.items.get(Key)
        if (options.get("IfNoneMatch") == "*" and current) or (
                options.get("IfMatch") and (not current or options["IfMatch"] != current[1])):
            raise ClientError({"Error": {"Code": "PreconditionFailed"}}, "PutObject")
        self.rev += 1
        self.items[Key] = (Body, str(self.rev))


@pytest.fixture
def setup(monkeypatch):
    memory = Memory()
    monkeypatch.setattr(plaid, "store", lambda: memory)
    monkeypatch.setenv("SIMPLEFIN_ENCRYPTION_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("PLAID_TRIAL_ENABLED", "true")
    monkeypatch.setenv("PLAID_TRIAL_PLAN_VERIFIED", "true")
    monkeypatch.setenv("PLAID_TRIAL_BASELINE_CONFIRMED", "true")
    monkeypatch.setenv("PLAID_TRIAL_PIPELINE_READY", "true")
    monkeypatch.setenv("PLAID_TRIAL_PRIOR_ITEMS", "0")
    monkeypatch.setenv("PLAID_CLIENT_ID", "fake-client")
    monkeypatch.setenv("PLAID_SECRET", "never-disclose")
    monkeypatch.setattr(plaid.time, "time", lambda: 1792134000)
    monkeypatch.setattr(plaid, "owner", lambda request:
                        "owner-a" if request.headers.get("Authorization") == "Bearer a"
                        else "owner-b" if request.headers.get("Authorization") == "Bearer b"
                        else (_ for _ in ()).throw(HTTPException(401, "Sign in required")))
    app = FastAPI()
    app.include_router(plaid.router)
    return TestClient(app), memory


def start(client, institution="Unsupported CU", owner="a", **changes):
    data = {"institution": institution, "triedSimplefin": True,
            "understandsLifetimeSlot": True, **changes}
    return client.post("/api/bonus-tracker/plaid/link-token",
                       headers={"Authorization": "Bearer " + owner}, json=data)


def exchange(client, session, owner="a", token="public-token"):
    return client.post("/api/bonus-tracker/plaid/exchange",
                       headers={"Authorization": "Bearer " + owner},
                       json={"session": session, "publicToken": token})


def test_default_off_requires_verified_baseline(setup, monkeypatch):
    client, memory = setup
    monkeypatch.setenv("PLAID_TRIAL_BASELINE_CONFIRMED", "false")
    status = client.get("/api/bonus-tracker/plaid", headers={"Authorization": "Bearer a"})
    assert status.status_code == 200 and status.json()["configured"] is False
    assert status.json()["used"] is None
    assert start(client).status_code == 503
    assert not memory.items
    monkeypatch.setenv("PLAID_TRIAL_BASELINE_CONFIRMED", "true")
    monkeypatch.delenv("PLAID_TRIAL_PRIOR_ITEMS")
    assert start(client).status_code == 503


def test_authorization_and_explicit_acknowledgement(setup, monkeypatch):
    client, memory = setup
    assert client.get("/api/bonus-tracker/plaid").status_code == 401
    assert start(client, owner="unknown").status_code == 401
    assert start(client, triedSimplefin=False).status_code == 400
    assert start(client, understandsLifetimeSlot=False).status_code == 400
    assert not memory.items


def test_exact_global_lifetime_cap_and_no_slot_recycling(setup, monkeypatch):
    client, memory = setup
    created = []
    def provider(endpoint, body):
        if endpoint.endswith("create"):
            return {"link_token": "synthetic-link"}
        created.append(body["public_token"])
        return {"item_id": "item-" + str(len(created)), "access_token": "secret-token-" + str(len(created))}
    monkeypatch.setattr(plaid, "_provider", provider)
    for n in range(10):
        response = start(client, institution="Bank " + str(n), owner="a" if n % 2 else "b")
        assert response.status_code == 200
        owner = "a" if n % 2 else "b"
        assert exchange(client, response.json()["session"], owner=owner,
                        token="public-token-" + str(n)).status_code == 200
    status = client.get("/api/bonus-tracker/plaid", headers={"Authorization": "Bearer a"}).json()
    assert status["used"] == 10 and status["remaining"] == 0 and status["limit"] == 10
    assert status["paidUpgradeAllowed"] is False
    assert start(client, institution="Bank 11").status_code == 409
    assert len(created) == 10
    encrypted = memory.items[plaid.LEDGER_KEY][0]
    assert b"secret-token" not in encrypted and b"item-" not in encrypted


def test_reservations_prevent_concurrent_overbooking(setup, monkeypatch):
    client, _ = setup
    monkeypatch.setenv("PLAID_TRIAL_PRIOR_ITEMS", "9")
    calls = []
    monkeypatch.setattr(plaid, "_provider", lambda endpoint, body: (
        calls.append(endpoint) or {"link_token": "synthetic"}))
    first = start(client)
    assert first.status_code == 200
    assert start(client, institution="Another bank").status_code == 409
    assert len(calls) == 1
    assert exchange(client, "f" * 32).status_code == 409


def test_cross_user_scope_duplicate_warning_and_no_replay(setup, monkeypatch):
    client, _ = setup
    monkeypatch.setattr(plaid, "_provider", lambda endpoint, body: (
        {"link_token": "synthetic"} if endpoint.endswith("create")
        else {"item_id": "new-item", "access_token": "sensitive"}))
    response = start(client)
    session = response.json()["session"]
    assert exchange(client, session, owner="b").status_code == 409
    assert exchange(client, session).status_code == 200
    assert exchange(client, session).status_code == 409
    assert start(client).status_code == 409
    allowed = start(client, allowDuplicate=True)
    assert allowed.status_code == 200
    a = client.get("/api/bonus-tracker/plaid", headers={"Authorization": "Bearer a"}).json()
    b = client.get("/api/bonus-tracker/plaid", headers={"Authorization": "Bearer b"}).json()
    assert len(a["connected"]) == 1 and b["connected"] == []
    assert "new-item" not in json.dumps(a) and "sensitive" not in json.dumps(a)


def test_provider_uncertainty_consumes_reservation_without_auto_retry(setup, monkeypatch):
    client, _ = setup
    def provider(endpoint, body):
        if endpoint.endswith("create"):
            return {"link_token": "synthetic"}
        raise HTTPException(502, "timeout")
    monkeypatch.setattr(plaid, "_provider", provider)
    session = start(client).json()["session"]
    assert exchange(client, session).status_code == 502
    assert exchange(client, session).status_code == 409
    result = client.get("/api/bonus-tracker/plaid", headers={"Authorization": "Bearer a"}).json()
    assert result["used"] == 0 and result["reserved"] == 1
    assert result["remaining"] == 9


def test_success_counter_survives_configuration_changes(setup, monkeypatch):
    client, _ = setup
    monkeypatch.setenv("PLAID_TRIAL_PRIOR_ITEMS", "6")
    monkeypatch.setattr(plaid, "_provider", lambda endpoint, body: (
        {"link_token": "synthetic"} if endpoint.endswith("create")
        else {"item_id": "unique", "access_token": "opaque"}))
    session = start(client).json()["session"]
    assert exchange(client, session).status_code == 200
    monkeypatch.setenv("PLAID_TRIAL_PRIOR_ITEMS", "0")
    result = client.get("/api/bonus-tracker/plaid", headers={"Authorization": "Bearer a"}).json()
    assert result["used"] == 7, "Existing lifetime ledger must not be reset by environment changes"


def test_no_upgrade_endpoints_can_be_invoked():
    assert plaid.BASE == "https://production.plaid.com"
    assert plaid.CAP == 10
    assert "Production-access application" in (plaid.__doc__ or "")
    with pytest.raises(HTTPException):
        plaid._provider("/production/access/apply", {})


def test_expired_link_never_recycles_possible_item_capacity(setup, monkeypatch):
    client, _ = setup
    monkeypatch.setenv("PLAID_TRIAL_PRIOR_ITEMS", "9")
    calls = []
    monkeypatch.setattr(plaid, "_provider", lambda endpoint, body: (
        calls.append(endpoint) or {"link_token": "synthetic"}))
    session = start(client).json()["session"]
    monkeypatch.setattr(plaid.time, "time", lambda: 1792134000 + plaid.TTL + 10)
    assert start(client, institution="Another bank").status_code == 409
    assert exchange(client, session).status_code == 409
    assert len(calls) == 1
    result = client.get("/api/bonus-tracker/plaid", headers={"Authorization": "Bearer a"}).json()
    assert result["used"] == 9 and result["reserved"] == 1 and result["remaining"] == 0


def test_exchange_rechecks_lifetime_counter_before_provider(setup, monkeypatch):
    client, _ = setup
    calls = []
    monkeypatch.setattr(plaid, "_provider", lambda endpoint, body: (
        calls.append(endpoint) or {"link_token": "synthetic"}))
    session = start(client).json()["session"]
    doc, tag = plaid._read()
    doc["used"] = 10
    plaid._write(doc, tag)
    assert exchange(client, session).status_code == 409
    assert calls == ["/link/token/create"]


def test_disconnect_one_bank_revokes_credential_without_recycling_trial_slot(setup, monkeypatch):
    import bonus_plaid_accounts as activity
    client, memory = setup
    monkeypatch.setattr(activity, "store", lambda: memory)
    calls = []
    def provider(endpoint, payload):
        calls.append(endpoint)
        if endpoint == "/link/token/create":
            return {"link_token": "synthetic-link"}
        if endpoint == "/item/public_token/exchange":
            return {"item_id": "item-one", "access_token": "synthetic-access"}
        assert endpoint == "/item/remove" and payload["access_token"] == "synthetic-access"
        return {"removed": True}
    monkeypatch.setattr(plaid, "_provider", provider)

    session = start(client).json()["session"]
    assert exchange(client, session).status_code == 200
    before = client.get("/api/bonus-tracker/plaid", headers={"Authorization": "Bearer a"}).json()
    handle = before["connected"][0]["handle"]
    assert len(handle) == 64 and before["used"] == 1
    forbidden = client.delete("/api/bonus-tracker/plaid/items/" + handle,
                              headers={"Authorization": "Bearer b"})
    assert forbidden.status_code == 404
    removed = client.delete("/api/bonus-tracker/plaid/items/" + handle,
                            headers={"Authorization": "Bearer a"})
    assert removed.status_code == 200 and removed.json()["disconnected"] is True
    after = client.get("/api/bonus-tracker/plaid", headers={"Authorization": "Bearer a"}).json()
    assert after["used"] == 1 and after["remaining"] == 9 and after["connected"] == []
    ledger, _ = plaid._read()
    assert "access" not in ledger["items"]["item-one"]
    assert ledger["items"]["item-one"]["disconnectedAt"] > 0
    assert calls.count("/item/remove") == 1
    assert client.delete("/api/bonus-tracker/plaid/items/" + handle,
                         headers={"Authorization": "Bearer a"}).status_code == 404
