"""Opt-in merchant learning is additive, scoped and preserves confirmed corrections."""
import copy
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import bonus_simplefin as sf
from finance_ai_categories import cache_key

NOW=1792134000

def test_learned_rule_relabels_similar_transactions_without_rewriting_rows(monkeypatch):
    account={"conn_id":"c1","id":"a1","name":"Checking","balance":"200","currency":"USD",
             "balance-date":NOW,"transactions":[]}
    payload={"accounts":[account],"connections":[],"errlist":[],"errors":[]}
    state=sf.empty_state()
    state["access"]="https://test:fake@bridge.simplefin.org/simplefin"
    state["accounts"]=sf.normalize_accounts(payload)
    account_key=state["accounts"][0]["key"]
    state["monitoredAccounts"]=[account_key]
    account["transactions"]=[
        {"id":"a","amount":"-9.12","posted":NOW-100,"description":"Unfamiliar local shop 334466"},
        {"id":"b","amount":"-10.13","posted":NOW-110,"description":"Unfamiliar local shop 445577"}
    ]
    sf.merge(state,payload,NOW)
    original=copy.deepcopy(state["transactions"])
    storage=[state]
    monkeypatch.setattr(sf,"owner",lambda req:"fake-owner")
    monkeypatch.setattr(sf,"read_state",lambda owner:(copy.deepcopy(storage[0]),"tag"))
    def write_state(owner,current,tag):
        storage[0]=copy.deepcopy(current)
        return "tag"
    monkeypatch.setattr(sf,"write_state",write_state)
    sf.transaction_review(sf.TransactionReviewBody(
        account=account_key,transaction=sf.public_ref(account_key,"a"),
        category="shopping",applySimilar=True),None)
    assert storage[0]["transactions"]==original
    assert storage[0]["merchantRules"][cache_key("Unfamiliar local shop 445577")]["category"]=="shopping"
    result=sf.public_state(storage[0])["transactions"][account_key]
    assert result[sf.public_ref(account_key,"a")]["categorySource"]=="user"
    assert result[sf.public_ref(account_key,"b")]["categorySource"]=="user_rule"
    assert result[sf.public_ref(account_key,"b")]["category"]=="shopping"


def test_merchant_rule_never_overrides_transaction_specific_user_correction():
    row={"id":"a","posted":NOW-100,"amount":"-22","description":"Synthetic obscure merchant",
         "pending":False,"kind":"withdrawal"}
    key="a"*64
    state={"transactions":{key:{"a":row}},"merchantRules":{
        cache_key(row["description"]):{"category":"shopping"}
    },"transactionReviews":{key:{"a":{"category":"dining"}}}}
    output={}
    sf.enrich_transactions(state,output)
    found=output["transactions"][key][sf.public_ref(key,"a")]
    assert found["category"]=="dining"
    assert found["categorySource"]=="user"
