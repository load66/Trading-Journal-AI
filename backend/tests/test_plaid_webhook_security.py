"""Synthetic-only security regressions for signed Plaid webhook verification.

Never makes a network request or reads production data.
"""
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi import HTTPException

import bonus_plaid_accounts as adapter

FIXED_NOW = 1800000000


@pytest.fixture
def signed_event(monkeypatch):
    private_key = ec.generate_private_key(ec.SECP256R1())
    jwk = json.loads(jwt.algorithms.ECAlgorithm.to_jwk(private_key.public_key()))
    jwk.update({"kid": "synthetic-verification-key", "alg": "ES256", "use": "sig"})
    requests = []
    def get_key(kid):
        requests.append(kid)
        assert kid == "synthetic-verification-key"
        return jwk
    monkeypatch.setattr(adapter, "_webhook_key_get", get_key, raising=False)
    if hasattr(adapter, "_WEBHOOK_KEY_CACHE"):
        adapter._WEBHOOK_KEY_CACHE.clear()
    body = b'{"webhook_type":"ITEM","webhook_code":"LOGIN_REPAIRED"}'
    def sign(*, data=body, issued=FIXED_NOW, algorithm="ES256"):
        digest = hashlib.sha256(data).hexdigest()
        return jwt.encode(
            {"iat": issued, "request_body_sha256": digest},
            private_key if algorithm == "ES256" else "synthetic-key",
            algorithm=algorithm,
            headers={"kid": "synthetic-verification-key"},
        )
    return sign, body, requests


def test_valid_signed_webhook_and_key_cache(signed_event):
    sign, body, requests = signed_event
    assert adapter._verify_webhook(sign(), body, now=FIXED_NOW) is True
    assert adapter._verify_webhook(sign(), body, now=FIXED_NOW) is True
    assert requests == ["synthetic-verification-key"]


@pytest.mark.parametrize("alteration", ["modified_body", "stale", "future", "invalid_signature", "wrong_algorithm"])
def test_reject_tampered_stale_unsigned_or_incompatible_webhooks(signed_event, alteration):
    sign, body, _ = signed_event
    signature = sign()
    if alteration == "modified_body":
        body += b" "
    elif alteration == "stale":
        signature = sign(issued=FIXED_NOW - 301)
    elif alteration == "future":
        signature = sign(issued=FIXED_NOW + 301)
    elif alteration == "invalid_signature":
        signature = signature[:-4] + "xxxx"
    elif alteration == "wrong_algorithm":
        signature = sign(algorithm="HS256")
    with pytest.raises(HTTPException) as err:
        adapter._verify_webhook(signature, body, now=FIXED_NOW)
    assert err.value.status_code == 401


def test_forbidden_provider_endpoints_are_blocked_without_network(monkeypatch):
    def fail_network(*args, **kwargs):
        raise AssertionError("Network must not be used")
    monkeypatch.setattr(adapter.httpx, "post", fail_network)
    for endpoint in ("/transactions/refresh", "/link/token/create", "/item/remove", "/accounts/balance/get"):
        with pytest.raises(HTTPException):
            adapter._provider(endpoint, "synthetic")


def test_provider_never_calls_network_when_item_budget_exhausted(monkeypatch):
    monkeypatch.setattr(adapter.quota, "_ready", lambda: True)
    monkeypatch.setattr(adapter, "_catalog", lambda _: {
        "products": ["transactions"], "billed_products": [], "available_products": []
    })
    monkeypatch.setattr(adapter, "_reserve_transaction_call",
                        lambda _: (_ for _ in ()).throw(HTTPException(429, "Daily sync limit reached")))
    def reject_network(*args, **kwargs):
        raise AssertionError("Cap must be checked before any remote provider request")
    monkeypatch.setattr(adapter.httpx, "post", reject_network)
    with pytest.raises(HTTPException) as blocked:
        adapter._provider("/transactions/sync", "synthetic-access", cursor="", count=500)
    assert blocked.value.status_code == 429
