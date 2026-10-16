"""Synthetic regression tests: Batch 1 finance data completeness.

No connections, credentials, or actual bank activity are used.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import bonus_simplefin as simple
import bonus_plaid_accounts as plaid

NOW = 1792134000
def simple_account(transactions):
    return {'id':'checking','conn_id':'test','name':'Synthetic checking',
            'currency':'USD','balance':'50','transactions': transactions}
def test_simplefin_zero_posted_pending_and_expiry():
    acc=simple_account([{'id':'pending','posted':0,'transacted_at':NOW-60,
                         'amount':'-7.50','description':'Pending purchase','pending':True}])
    key=simple.account_key(acc)
    state=simple.empty_state()
    state['accounts']=simple.normalize_accounts({'accounts':[acc]})
    state['monitoredAccounts']=[key]
    simple.merge(state,{'accounts':[acc]},NOW)
    assert state['transactions'][key]['pending']['pending'] is True
    assert state['transactions'][key]['pending']['displayAt']==NOW-60
    simple.merge(state,{'accounts':[simple_account([])]},NOW+60)
    assert 'pending' not in state['transactions'][key]

def test_plaid_pending_and_older_posting_are_preserved():
    acc={'account_id':'demo','name':'Synthetic checking','type':'depository','subtype':'checking','balances':{'current':20}}
    tx=lambda ident,pending,day: {'transaction_id':ident,'account_id':'demo',
           'amount':10,'date':day,'name':'Synthetic entry','pending':pending}
    changes=({'pending':tx('pending',True,'2026-10-16'),'old':tx('old',False,'2026-04-01')},{},set(),'c1')
    state=plaid._merge({},'item','Demo',{'accounts':[acc]},changes,NOW)
    assert state['transactions']['pending']['pending'] is True
    assert 'old' in state['transactions']

def test_plaid_pending_to_posted_reconciles_by_link():
    acc={'account_id':'demo','name':'Synthetic','balances':{'current':0}}
    pend={'transaction_id':'a','account_id':'demo','amount':10,'date':'2026-10-14','name':'Synthetic','pending':True}
    booked={**pend,'transaction_id':'b','pending':False,'pending_transaction_id':'a','date':'2026-10-16'}
    old=plaid._merge({},'item','Demo',{'accounts':[acc]},({'a':pend},{},set(),'c1'),NOW)
    new=plaid._merge(old,'item','Demo',{'accounts':[acc]},({'b':booked},{},set(),'c2'),NOW)
    assert set(new['transactions'])=={'b'}
