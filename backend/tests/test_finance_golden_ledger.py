"""Finance golden-ledger adapter smoke with both providers mocked, no live calls.

The same pinned synthetic fixture is present in both repos. Tests fail on any
unreviewed fixture change; frontend separately asserts hand-written totals.
"""
import json
import sys
from pathlib import Path
from hashlib import sha256

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import bonus_simplefin as simple
import bonus_plaid_accounts as plaid

FIXTURE_PATH = Path(__file__).resolve().parents[2] / "tests/fixtures/golden-ledger.json"


def _fixture():
    raw = FIXTURE_PATH.read_bytes()
    digest = 14695981039346656037
    for byte in raw:
        digest = ((digest ^ byte) * 1099511628211) & ((1 << 64) - 1)
    assert f"{digest:016x}" == "889078fa7fb897d3", "Golden fixture drift across repos"
    return json.loads(raw)


def test_golden_ledger_same_fixture_and_exact_source_baseline(monkeypatch):
    f = _fixture()
    now = f["asOf"]
    expected = {
        "simplefin": {
            "accounts": 3,
            "balances": ("3800.00", "-240000.00", "900.00"),
            "transactions": 18,
        },
        "plaid": {
            "accounts": 2,
            "balances": ("1200.00", "500.00"),
            "transactions": 3,
        },
    }
    assert len(f["simplefin"]["accounts"]) == expected["simplefin"]["accounts"]
    assert tuple(a["balance"] for a in f["simplefin"]["accounts"]) == expected["simplefin"]["balances"]
    assert sum(len(v) for v in f["simplefin"]["transactions"].values()) == expected["simplefin"]["transactions"]
    assert len(f["plaid"]["accounts"]) == expected["plaid"]["accounts"]
    assert tuple(a["balance"] for a in f["plaid"]["accounts"]) == expected["plaid"]["balances"]
    assert sum(len(v) for v in f["plaid"]["transactions"].values()) == expected["plaid"]["transactions"]

    # Exercise REAL SimpleFIN account normalization -> merge -> public_state.
    public_bank = f["simplefin"]
    raw = {
        "connections": [
            {"conn_id":"s-check","name":"Synthetic Bank"},
            {"conn_id":"s-mortgage","name":"Synthetic Servicer"},
            {"conn_id":"s-off","name":"Synthetic Bank"},
        ],
        "accounts": [],
    }
    for alias, account in zip(("s-check","s-mortgage","s-off"),public_bank["accounts"]):
        raw_id = {"s-check":"checking","s-mortgage":"mortgage","s-off":"off"}[alias]
        supplied = {
            "conn_id":alias,"id":raw_id,"name":account["name"],
            "currency":account["currency"],"balance":account["balance"],
            "balance-date":account["balanceDate"],"type":account["accountType"],
            "subtype":account["accountSubtype"],
            "transactions": [
                {"id":row["id"],"amount":row["amount"],"description":row["description"],
                 "posted":row["posted"],"pending":row["pending"]}
                for row in public_bank["transactions"].get(account["key"],{}).values()
            ],
        }
        raw["accounts"].append(supplied)
        assert simple.account_key(supplied) == account["key"]
    source_calls = []
    def mock_get_data(*args,**kwargs):
        source_calls.append("simplefin")
        return raw
    monkeypatch.setattr(simple,"get_data",mock_get_data)
    state = simple.empty_state()
    state["accounts"] = simple.normalize_accounts(raw)
    state["monitoredAccounts"] = [a["key"] for a in state["accounts"]]
    state["accountProfiles"] = public_bank["accountProfiles"]
    simple.merge(state,simple.get_data("synthetic-only"),now)
    shown = simple.public_state(state)
    assert source_calls == ["simplefin"]
    assert {a["key"] for a in shown["accounts"]} == {
        a["key"] for a in public_bank["accounts"]}
    assert shown["balanceHistory"]
    # Verified ingestion keeps cents, mortgage balance, pending rows and history;
    # the provider determines classification, so this does not fake manual review.
    observed = {row["amount"] for records in shown["transactions"].values()
                for row in records.values()}
    assert {"2000.00","-1500.00","-150.25","-22.89"}.issubset(observed)
    assert any(row["pending"] for records in shown["transactions"].values()
               for row in records.values())

    # Exercise REAL Plaid _account/_transaction/_merge/_view/_public. Mock
    # _provider and _sync_item; never activate an Item or transmit credentials.
    raw_accounts = {"accounts": [
        {"account_id":raw_id,"name":account["name"],
         "type":account["accountType"],"subtype":account["accountSubtype"],
         "balances":{"current":account["balance"],"available":None,"iso_currency_code":"USD"}}
        for raw_id,account in zip(("wallet","card"),f["plaid"]["accounts"])
    ]}
    injected = []
    def mock_provider(route,token,**kw):
        injected.append(route)
        assert route == "/accounts/get"
        return raw_accounts
    def mock_sync_item(token,cursor):
        injected.append("/transactions/sync")
        added = {}
        for raw_id,account in zip(("wallet","card"),f["plaid"]["accounts"]):
            for row in f["plaid"]["transactions"][account["key"]].values():
                added[row["id"]] = {
                    "transaction_id":row["id"],"account_id":raw_id,
                    "amount": -float(row["amount"]),  # Plaid debit-positive protocol
                    "name":row["description"],"date":"2026-10-08",
                    "pending":False,"iso_currency_code":"USD",
                }
        return plaid.SyncDelta((added,{},set(),"cursor"),"HISTORICAL_UPDATE_COMPLETE")
    monkeypatch.setattr(plaid,"_provider",mock_provider)
    monkeypatch.setattr(plaid,"_sync_item",mock_sync_item)
    item_id="p-item"
    provider_rows=plaid._provider("/accounts/get","mock-only")
    delta=plaid._sync_item("mock-only","")
    prepared=plaid._merge({},item_id,"Synthetic Bank",provider_rows,delta,now)
    ledger={"items":{item_id:{"owner":"alice","institution":"Synthetic Bank"}}}
    doc={**plaid._default(),"items":{item_id:prepared},
         "monitored":[a["key"] for a in f["plaid"]["accounts"]],"lastSuccess":now}
    public=plaid._public(doc,ledger,"alice")
    assert injected == ["/accounts/get","/transactions/sync"]
    assert {a["key"] for a in public["accounts"]} == {
        a["key"] for a in f["plaid"]["accounts"]}
    assert {a["balance"] for a in public["accounts"]} == {"1200","500"}
    assert {row["amount"] for rec in public["transactions"].values()
            for row in rec.values()} == {"1000","333","500"}
    assert set(public["balanceHistory"]) == {a["key"] for a in f["plaid"]["accounts"]}
    assert plaid._public(doc,ledger,"bob")["accounts"] == []
    assert plaid._public(doc,ledger,"bob")["balanceHistory"] == {}
