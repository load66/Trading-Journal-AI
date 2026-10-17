"""Only already-authorized /transactions/sync metadata is used. All examples synthetic."""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from finance_plaid_enrichment import enrich
from bonus_plaid_accounts import ROUTES

def test_transaction_sync_enrichment_and_host_restrictions():
    result=enrich({
      'merchant_name':'Synthetic Market',
      'logo_url':'https://plaid-merchant-logos.plaid.com/synthetic.png',
      'website':'https://example.com/',
      'payment_channel':'online',
      'authorized_date':'2026-10-15',
      'location':{'city':'Joplin','region':'MO','country':'US'},
      'counterparties':[{'name':'Synthetic Market','type':'merchant','entity_id':'synthetic',
                         'logo_url':'https://plaid-counterparty-logos.plaid.com/synthetic.png'}],
      'personal_finance_category':{'primary':'FOOD_AND_DRINK',
                                   'detailed':'FOOD_AND_DRINK_FAST_FOOD','confidence_level':'HIGH'}
    })
    assert result['plaidSuggestedCategory']=='dining'
    assert result['merchantName']=='Synthetic Market'
    assert result['merchantLogoUrl']=='https://plaid-merchant-logos.plaid.com/synthetic.png'
    assert result['counterparties'][0]['type']=='merchant'
    assert result['authorizedDate']=='2026-10-15'
    assert result['location']['city']=='Joplin'
    assert enrich({'logo_url':'https://tracker.example.org/secret.png'})['merchantLogoUrl'] is None

def test_cash_movement_and_paycheck_category_mapping():
    samples=[
      ({'primary':'INCOME','detailed':'INCOME_WAGES'},'income','paycheck'),
      ({'primary':'TRANSFER_IN','detailed':'TRANSFER_IN_ACCOUNT_TRANSFER'},'transfer','plaid_transfer'),
      ({'primary':'LOAN_PAYMENTS','detailed':'LOAN_PAYMENTS_CREDIT_CARD_PAYMENT'},'transfer','card_payment'),
      ({'primary':'BANK_FEES','detailed':'BANK_FEES_INSUFFICIENT_FUNDS'},'fees',None),
    ]
    for meta,category,signal in samples:
        result=enrich({'personal_finance_category':meta})
        assert result['plaidSuggestedCategory']==category
        if signal:assert signal in result['plaidCategorySignals']

def test_no_paid_endpoints_ever_appear_in_plaid_finance_allowlist():
    assert ROUTES==frozenset(('/item/get','/accounts/get','/transactions/sync'))
    assert not any('refresh' in x or 'link/token/create' in x or 'recurring/get' in x
                   for x in ROUTES)
