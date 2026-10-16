"""Opt-in, bounded Sandbox connectivity check. Never touches Production or R2.

Reports only fixed stage/status names. Test credentials, tokens, account IDs,
transaction descriptions and provider error payloads must never reach logs.
"""
import logging
import os
import re
import time

import httpx

BASE = "https://sandbox.plaid.com"
PATHS = frozenset(("/sandbox/public_token/create", "/item/public_token/exchange",
                   "/accounts/get", "/transactions/sync", "/item/remove"))
logger = logging.getLogger(__name__)


class CheckFailed(Exception):
    pass


def verify(client_id, secret):
    """Create, inspect, and remove one synthetic Sandbox Item in memory only."""
    report = {"environment": "sandbox", "status": "failed", "stage": "credentials",
              "accounts": 0, "transactions": 0, "cleanup": "not_needed"}
    if not client_id or not secret:
        return report
    access = None
    try:
        with httpx.Client(timeout=12, follow_redirects=False, trust_env=False) as client:
            def call(path, **body):
                if path not in PATHS:
                    raise CheckFailed()
                response = client.post(BASE + path, json={
                    **body, "client_id": client_id, "secret": secret})
                if response.status_code != 200 or len(response.content) > 2 * 1024 * 1024:
                    raise CheckFailed()
                result = response.json()
                if not isinstance(result, dict):
                    raise CheckFailed()
                return result

            try:
                report["stage"] = "create_test_item"
                created = call("/sandbox/public_token/create", institution_id="ins_109508",
                               initial_products=["transactions"])
                public = created.get("public_token")
                if not isinstance(public, str) or not public.startswith("public-sandbox-"):
                    raise CheckFailed()
                report["stage"] = "exchange_test_token"
                exchanged = call("/item/public_token/exchange", public_token=public)
                candidate = exchanged.get("access_token")
                if not isinstance(candidate, str) or not candidate.startswith("access-sandbox-"):
                    raise CheckFailed()
                access = candidate
                report["cleanup"] = "pending"
                report["stage"] = "read_test_accounts"
                accounts = call("/accounts/get", access_token=access).get("accounts")
                if not isinstance(accounts, list) or not accounts or len(accounts) > 500:
                    raise CheckFailed()
                if any(not isinstance(a, dict) or not isinstance(a.get("account_id"), str)
                       or not a["account_id"] for a in accounts):
                    raise CheckFailed()
                report["accounts"] = len(accounts)
                report["stage"] = "sync_test_transactions"
                cursor, transaction_ids = "", set()
                complete = False
                for attempt in range(8):
                    result = call("/transactions/sync", access_token=access, cursor=cursor, count=500)
                    if (not isinstance(result.get("next_cursor"), str)
                            or type(result.get("has_more")) is not bool):
                        raise CheckFailed()
                    for field in ("added", "modified", "removed"):
                        rows = result.get(field)
                        if not isinstance(rows, list) or len(rows) > 500:
                            raise CheckFailed()
                        for row in rows:
                            if not isinstance(row, dict) or not isinstance(row.get("transaction_id"), str):
                                raise CheckFailed()
                            if field == "removed":
                                transaction_ids.discard(row["transaction_id"])
                            else:
                                transaction_ids.add(row["transaction_id"])
                    cursor = result["next_cursor"]
                    if not result["has_more"] and transaction_ids:
                        complete = True
                        break
                    if not result["has_more"] and attempt < 7:
                        time.sleep(2)  # Sandbox prepares history asynchronously.
                report["transactions"] = len(transaction_ids)
                report["status"] = "passed" if complete else "pending"
                report["stage"] = "complete" if complete else "waiting_for_test_transactions"
            except Exception:
                # Do not stringify exceptions: HTTP/provider messages may carry secrets.
                report["status"] = "failed"
            finally:
                if access:
                    try:
                        call("/item/remove", access_token=access)
                        report["cleanup"] = "passed"
                    except Exception:
                        report["cleanup"] = "failed"
                        report["status"] = "failed"
                        report["stage"] = "cleanup_test_item"
    except Exception:
        report["status"] = "failed"
    return report


def run_configured_check():
    """Disabled by default; invoked once per service startup when requested."""
    run = os.getenv("PLAID_SANDBOX_VERIFY_RUN", "")
    if not re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", run):
        return
    try:
        report = verify(os.getenv("PLAID_CLIENT_ID"), os.getenv("PLAID_SANDBOX_SECRET"))
    except Exception:
        report = {"status": "failed", "stage": "internal", "accounts": 0,
                  "transactions": 0, "cleanup": "unknown"}
    logger.warning("PLAID_SANDBOX_CHECK run=%s status=%s stage=%s accounts=%s transactions=%s cleanup=%s",
                   run, report["status"], report["stage"], report["accounts"],
                   report["transactions"], report["cleanup"])
