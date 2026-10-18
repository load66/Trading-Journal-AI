"""Synthetic immutable T&C archive regression tests; no real R2 or bank APIs."""
import hashlib
import json
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from botocore.exceptions import ClientError
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import bonus_terms_archive as archive
from auth import AuthError


class FakeStorage:
    bucket = "mock-r2"

    def __init__(self):
        self.client = self
        self.objects = {}
        self.counter = 0
        self.corrupt = False

    def put_object(self, Bucket, Key, Body, Metadata, **kwargs):
        assert kwargs["IfNoneMatch"] == "*"
        if Key in self.objects:
            raise ClientError({"Error": {"Code": "PreconditionFailed"},
                               "ResponseMetadata": {"HTTPStatusCode": 412}}, "PutObject")
        self.counter += 1
        self.objects[Key] = {
            "body": Body, "Metadata": Metadata,
            "LastModified": datetime(2026, 10, 18, tzinfo=timezone.utc) +
            timedelta(seconds=self.counter),
        }

    def head_object(self, Bucket, Key):
        if Key not in self.objects:
            raise ClientError({"Error": {"Code": "404"}}, "HeadObject")
        item = self.objects[Key]
        return {"ContentLength": len(item["body"]), "Metadata": item["Metadata"]}

    def read(self, key):
        if key not in self.objects:
            raise FileNotFoundError(key)
        return (b"bad" if self.corrupt else self.objects[key]["body"]), "application/json"

    def list_objects_v2(self, Bucket, Prefix, **kwargs):
        return {"Contents": [{"Key": key, "LastModified": row["LastModified"]}
                             for key, row in self.objects.items() if key.startswith(Prefix)]}


def sample(**updates):
    data = {
        "format": "BonusTrackerHistoricalTerms", "version": 1,
        "bank": "Mock Community Bank", "accountType": "business",
        "entryId": "mock-2026-01", "opened": "2026-10-15",
        "source": "verified-json-new-cycle", "capturedAt": "2026-10-18",
        "tcSourceRaw": "Exact 2026 introductory promotion. " * 10,
        "feeScheduleRaw": "Maintenance: $12 monthly. Early close $25 within 180 days.",
        "monthlyFeeAmountText": "$12",
        "earlyTerminationFeeText": "$25 closing fee if closed in 180 days",
        "promoSourceUrl": "https://example.test/promotion/2026",
        "feeScheduleSourceUrl": "https://example.test/fees/2026.pdf",
        "financialRules": {"version": 2, "accounts": [
            {"id": "primary", "role": "primary",
             "fee": {"amount": 12, "evidence": {"quote": "$12 monthly."}}}
        ], "closure": {"earlyCloseFee": 25, "minHoldDays": 180}},
        "eligibilityRules": [{"basis": "account-closed", "periodValue": 12, "periodUnit": "months"}],
    }
    data.update(updates)
    return json.dumps(data, ensure_ascii=False, separators=(",", ":"))


def packet(raw=None):
    raw = raw or sample()
    return {"snapshot": raw, "sha256": hashlib.sha256(raw.encode()).hexdigest()}


def test_append_only_versions_and_no_pruning_of_terms():
    storage = FakeStorage()
    p = packet()
    first = archive.save_terms(storage, "owner1", p)
    assert first["verified"]
    assert archive.read_terms(storage, "owner1", first["id"])["snapshot"] == p["snapshot"]
    assert archive.save_terms(storage, "owner1", p)["id"] == first["id"]
    assert len(storage.objects) == 1
    for i in range(10):
        archive.save_terms(storage, "owner1", packet(sample(tcSourceRaw=f"Enrolled original wording revision {i}")))
    catalog = archive.list_terms(storage, "owner1")
    assert catalog["total"] == 11
    assert catalog["immutable"]
    assert len(storage.objects) == 11
    assert archive.read_terms(storage, "owner1", first["id"])["snapshot"] == p["snapshot"]


def test_preserves_legacy_churn_conditions_and_source_separately():
    storage = FakeStorage()
    raw = sample(churn="1", churnability="repeatable", churnBasis="bonus",
                 churnRuleText="Previous bonus within twelve months is ineligible",
                 churnReason="Original 2026 enrolled terms",
                 churnDecisionSource="official-promotion-terms",
                 churnDecisionConfidence="High", eligibilityVerifiedAt="2026-10-15")
    saved = archive.save_terms(storage, "owner1", packet(raw))
    historic = json.loads(archive.read_terms(storage, "owner1", saved["id"])["snapshot"])
    assert historic["churn"] == "1"
    assert historic["churnBasis"] == "bonus"
    assert historic["churnRuleText"].startswith("Previous bonus")
    assert historic["churnDecisionSource"] == "official-promotion-terms"
    assert len(historic["eligibilityRules"]) == 1


def test_owner_isolation_integrity_and_no_overwrite():
    storage = FakeStorage()
    first = archive.save_terms(storage, "owner1", packet())
    with pytest.raises(Exception):
        archive.read_terms(storage, "owner2", first["id"])
    with pytest.raises(Exception):
        archive._key("owner1", "../other")
    assert archive.list_terms(storage, "owner2")["total"] == 0
    storage.corrupt = True
    with pytest.raises(ValueError, match="integrity"):
        archive.read_terms(storage, "owner1", first["id"])
    storage.corrupt = False
    assert archive.save_terms(storage, "owner1", packet())["id"] == first["id"]


def test_rejects_private_fields_bad_hash_and_empty_terms():
    storage = FakeStorage()
    with pytest.raises(ValueError):
        archive.save_terms(storage, "owner1", packet(sample(accountNumber="1234")))
    with pytest.raises(ValueError):
        archive.save_terms(storage, "owner1", packet(sample(
            financialRules={"accounts": [{"access_token": "not allowed"}]})))
    with pytest.raises(ValueError):
        archive.save_terms(storage, "owner1", packet(sample(tcSourceRaw="",
            feeScheduleRaw="", monthlyFeeAmountText="", earlyTerminationFeeText="",
            financialRules=None)))
    mismatch = packet()
    mismatch["sha256"] = "0" * 64
    with pytest.raises(ValueError):
        archive.save_terms(storage, "owner1", mismatch)
    assert storage.objects == {}


def test_authenticated_read_and_upload(monkeypatch):
    storage = FakeStorage()
    monkeypatch.setattr(archive, "store", lambda: storage)
    monkeypatch.setattr(archive, "owner", lambda request:
        "owner1" if request.headers.get("Authorization") == "Bearer mock" else
        (_ for _ in ()).throw(HTTPException(401, "Authentication required")))
    app = FastAPI()
    app.include_router(archive.router)
    client = TestClient(app)
    assert client.post("/api/bonus-tracker/terms", json=packet()).status_code == 401
    assert client.get("/api/bonus-tracker/terms").status_code == 401
    upload = client.post("/api/bonus-tracker/terms",
                         json=packet(), headers={"Authorization": "Bearer mock"})
    assert upload.status_code == 200
    info = upload.json()
    assert info["verified"]
    listed = client.get("/api/bonus-tracker/terms",
                        headers={"Authorization": "Bearer mock"})
    assert listed.status_code == 200
    assert listed.json()["total"] == 1
    assert listed.json()["terms"][0]["entryId"] == "mock-2026-01"
    fetched = client.get("/api/bonus-tracker/terms/" + info["id"],
                         headers={"Authorization": "Bearer mock"})
    assert json.loads(fetched.json()["snapshot"])["tcSourceRaw"].startswith("Exact 2026")
    assert client.get("/api/bonus-tracker/terms/not-a-hash",
                      headers={"Authorization": "Bearer mock"}).status_code == 400
    assert client.delete("/api/bonus-tracker/terms/" + info["id"],
                         headers={"Authorization": "Bearer mock"}).status_code == 405
