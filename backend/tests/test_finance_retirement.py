"""Synthetic only. Never connect to a real R2 bucket or provider."""
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import finance_retirement as retired


UID = "a" * 64
OTHER = "b" * 64


class FakeClient:
    def __init__(self, keys):
        self.data = dict(keys)
        self.deleted = []

    def list_objects_v2(self, *, Bucket, Prefix, MaxKeys, ContinuationToken=None):
        keys = sorted(k for k in self.data if k.startswith(Prefix))
        return {"Contents": [{"Key": k, "ETag": self.data[k]} for k in keys], "IsTruncated": False}

    def head_object(self, *, Bucket, Key):
        if Key not in self.data:
            raise AssertionError("Cannot delete an unknown object")
        return {"ETag": self.data[Key]}

    def delete_object(self, *, Bucket, Key):
        self.deleted.append(Key)
        self.data.pop(Key)
        return {}


class FakeStore:
    bucket = "synthetic-fixture"

    def __init__(self, keys):
        self.client = FakeClient(keys)


def paths(uid=UID):
    return {
        f"bonus-tracker-simplefin/v1/{uid}/state.enc": "etag-1",
        f"bonus-tracker-simplefin/v1/{uid}/history/2026-10.enc": "etag-2",
        f"bonus-tracker-simplefin/v1/{uid}/pre-disconnect/snapshot.enc": "etag-3",
        f"bonus-tracker-plaid/v1/{uid}/history/2026-09.enc": "etag-4",
        f"bonus-tracker-plaid-trial/v1/{uid}/activity.enc": "etag-5",
        f"bonus-tracker-finances/v1/{uid}/preferences.enc": "etag-6",
    }


@pytest.fixture
def case(monkeypatch):
    safe = {
        **paths(),
        **paths(OTHER),
        "bonus-tracker-plaid-trial/v1/global.enc": "global-ledger",
        "bonus-tracker-recovery/v1/" + UID + "/backup.json": "backup",
        "bonus-tracker-venza/v1/" + UID + "/state.enc": "vehicle",
        "journal/screenshots/synthetic.png": "journal",
    }
    storage = FakeStore(safe)
    monkeypatch.setattr(retired, "store", lambda: storage)
    monkeypatch.setattr(retired, "owner", lambda request: UID if request.headers.get("Authorization") == "Bearer fixture" else (_ for _ in ()).throw(HTTPException(401, "Unauthorized")))
    app = FastAPI()
    app.include_router(retired.router)
    return TestClient(app), storage


def test_auth_and_dry_run_do_not_delete_or_return_keys(case):
    client, storage = case
    assert client.get("/api/bonus-tracker/finance-retirement/dry-run").status_code == 401
    response = client.get("/api/bonus-tracker/finance-retirement/dry-run", headers={"Authorization": "Bearer fixture"})
    assert response.status_code == 200
    report = response.json()
    assert report["total"] == 6 and report["blocked"] == 0 and report["dryRun"] is True
    assert set(report["counts"]) == set(retired.CATEGORIES)
    assert len(report["digest"]) == 64
    assert "state.enc" not in response.text
    assert storage.client.deleted == []


def test_owner_phrase_count_and_digest_required(case):
    client, storage = case
    hdr = {"Authorization": "Bearer fixture"}
    plan = client.get("/api/bonus-tracker/finance-retirement/dry-run", headers=hdr).json()
    path = "/api/bonus-tracker/finance-retirement/execute"
    for values in [
        dict(phrase="delete", count=plan["total"], digest=plan["digest"]),
        dict(phrase=retired.PHRASE, count=plan["total"] - 1, digest=plan["digest"]),
        dict(phrase=retired.PHRASE, count=plan["total"], digest="0" * 64),
    ]:
        assert client.post(path, headers=hdr, json=values).status_code in (400, 409)
        assert storage.client.deleted == []
    ok = client.post(path, headers=hdr, json=dict(phrase=retired.PHRASE, count=plan["total"], digest=plan["digest"]))
    assert ok.status_code == 200, ok.text
    assert ok.json() == {"deleted": 6, "remaining": 0, "blocked": 0, "verified": True}
    assert len(storage.client.deleted) == 6
    assert "bonus-tracker-plaid-trial/v1/global.enc" in storage.client.data
    assert any("bonus-tracker-recovery/" in k for k in storage.client.data)
    assert any("journal/screenshots/" in k for k in storage.client.data)
    assert len([k for k in storage.client.data if OTHER in k]) == 6


def test_unrecognized_key_blocks_cleanup(case):
    client, storage = case
    hdr = {"Authorization": "Bearer fixture"}
    storage.client.data[f"bonus-tracker-simplefin/v1/{UID}/unrecognized.enc"] = "unknown"
    plan = client.get("/api/bonus-tracker/finance-retirement/dry-run", headers=hdr).json()
    assert plan["blocked"] == 1
    result = client.post("/api/bonus-tracker/finance-retirement/execute", headers=hdr, json={
        "phrase": retired.PHRASE, "count": plan["total"], "digest": plan["digest"],
    })
    assert result.status_code == 409
    assert not storage.client.deleted


def test_revised_history_requires_new_dry_run(case):
    client, storage = case
    hdr = {"Authorization": "Bearer fixture"}
    plan = client.get("/api/bonus-tracker/finance-retirement/dry-run", headers=hdr).json()
    storage.client.data[f"bonus-tracker-plaid/v1/{UID}/history/2026-10.enc"] = "new-etag"
    result = client.post("/api/bonus-tracker/finance-retirement/execute", headers=hdr, json={
        "phrase": retired.PHRASE, "count": plan["total"], "digest": plan["digest"],
    })
    assert result.status_code == 409
    assert storage.client.deleted == []
