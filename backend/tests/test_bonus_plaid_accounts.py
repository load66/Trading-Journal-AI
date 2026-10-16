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
    assert credit["kind"]=="other_deposit"
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
