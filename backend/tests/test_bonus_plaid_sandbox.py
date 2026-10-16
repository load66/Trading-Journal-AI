"""Synthetic tests; never sends credential values to an external service."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pytest
import bonus_plaid_sandbox as check


def install(monkeypatch, failure=None, pending=False, pages=False):
    calls = []
    class Client:
        def __init__(self, **kwargs):
            assert kwargs == {"timeout": 12, "follow_redirects": False, "trust_env": False}
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def post(self, url, json):
            assert url.startswith("https://sandbox.plaid.com/")
            assert json["client_id"] == "private-client"
            assert json["secret"] == "private-sandbox-secret"
            path = url.removeprefix(check.BASE)
            assert path in check.PATHS
            calls.append((path, json))
            if path == failure:
                raise RuntimeError("private-sandbox-secret access-sandbox-test private-client")
            data = {
                "/sandbox/public_token/create": {"public_token": "public-sandbox-test"},
                "/item/public_token/exchange": {"access_token": "access-sandbox-test"},
                "/accounts/get": {"accounts": [{"account_id": "private-test-account"}]},
                "/item/remove": {"request_id": "private-request"},
                "/transactions/sync": {"next_cursor": "next", "has_more": pages,
                    "added": [] if pending else [{"transaction_id": "private-test-transaction"}],
                    "modified": [], "removed": []},
            }[path]
            return type("Response", (), {"status_code": 200, "content": b"{}",
                "json": lambda self: data})()
    monkeypatch.setattr(check.httpx, "Client", Client)
    monkeypatch.setattr(check.time, "sleep", lambda seconds: None)
    return calls


def test_complete_sandbox_check_cleans_up_and_never_uses_production(monkeypatch):
    monkeypatch.setenv("PLAID_SECRET", "never-read-production-secret")
    calls = install(monkeypatch)
    result = check.verify("private-client", "private-sandbox-secret")
    assert result == {"environment": "sandbox", "status": "passed", "stage": "complete",
                      "accounts": 1, "transactions": 1, "cleanup": "passed"}
    assert [p for p, _ in calls] == ["/sandbox/public_token/create", "/item/public_token/exchange",
                                   "/accounts/get", "/transactions/sync", "/item/remove"]
    assert "never-read-production-secret" not in json.dumps(calls)
    assert "private-" not in json.dumps(result)


@pytest.mark.parametrize("path", ["/accounts/get", "/transactions/sync"])
def test_failure_cleans_up_test_item_without_disclosing_exception(monkeypatch, path):
    calls = install(monkeypatch, failure=path)
    result = check.verify("private-client", "private-sandbox-secret")
    assert result["status"] == "failed" and result["cleanup"] == "passed"
    assert calls[-1][0] == "/item/remove"
    assert "private-" not in json.dumps(result)


def test_cleanup_failure_is_not_a_pass(monkeypatch):
    install(monkeypatch, failure="/item/remove")
    result = check.verify("private-client", "private-sandbox-secret")
    assert result["status"] == "failed" and result["cleanup"] == "failed"


@pytest.mark.parametrize("pending,pages", [(True, False), (False, True)])
def test_empty_or_incomplete_history_is_bounded_and_not_a_pass(monkeypatch, pending, pages):
    calls = install(monkeypatch, pending=pending, pages=pages)
    result = check.verify("private-client", "private-sandbox-secret")
    assert result["status"] == "pending"
    assert sum(p == "/transactions/sync" for p, _ in calls) == 8
    assert result["cleanup"] == "passed"


def test_missing_credentials_never_make_provider_calls(monkeypatch):
    calls = install(monkeypatch)
    assert check.verify("private-client", "")["stage"] == "credentials"
    assert not calls


def test_startup_disabled_by_default_and_logs_only_fixed_result(monkeypatch, caplog):
    calls = install(monkeypatch)
    monkeypatch.delenv("PLAID_SANDBOX_VERIFY_RUN", raising=False)
    check.run_configured_check()
    assert not calls
    monkeypatch.setenv("PLAID_SANDBOX_VERIFY_RUN", "20261016-v1")
    monkeypatch.setenv("PLAID_CLIENT_ID", "private-client")
    monkeypatch.setenv("PLAID_SANDBOX_SECRET", "private-sandbox-secret")
    check.run_configured_check()
    assert "status=passed" in caplog.text and "cleanup=passed" in caplog.text
    for private in ("private-client", "private-sandbox-secret", "access-sandbox-test",
                    "private-test-account", "private-test-transaction"):
        assert private not in caplog.text


def test_disallowed_run_label_never_executes(monkeypatch):
    calls = install(monkeypatch)
    monkeypatch.setenv("PLAID_SANDBOX_VERIFY_RUN", "https://untrusted.example/token")
    check.run_configured_check()
    assert not calls
