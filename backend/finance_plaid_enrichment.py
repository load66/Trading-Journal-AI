"""Use metadata already included in an authorized /transactions/sync response.

Never triggers a Plaid product call or enables additional products.
"""
from urllib.parse import urlsplit
from bonus_simplefin import clean

def _plain(value, maxlen=120):
    return clean(value,maxlen) if isinstance(value,str) else ''

def _safe_url(value):
    if not isinstance(value,str) or len(value)>350:
        return None
    try:
        p=urlsplit(value)
        if p.scheme=='https' and p.hostname and not p.username and not p.password and not p.fragment:
            return value
    except ValueError:
        pass
    return None

def _safe_logo(value):
    url = _safe_url(value)
    if not url:
        return None
    host = urlsplit(url).hostname
    return url if host in ("plaid-merchant-logos.plaid.com",
                          "plaid-counterparty-logos.plaid.com",
                          "plaid-category-icons.plaid.com") else None


def enrich(row):
    pfc=row.get('personal_finance_category') or {}
    if not isinstance(pfc,dict):
        pfc={}
    primary=_plain(pfc.get('primary'),70).upper()
    detailed=_plain(pfc.get('detailed'),100).upper()
    confidence=_plain(pfc.get('confidence_level'),20)
    labels=(primary+' '+detailed).strip()
    category=None
    signals=[]
    if detailed.startswith('LOAN_PAYMENTS_CREDIT_CARD_PAYMENT'):
        category='transfer';signals=['card_payment']
    elif primary.startswith('TRANSFER_') or detailed.startswith('TRANSFER_'):
        category='transfer';signals=['plaid_transfer']
    elif 'INCOME_WAGES' in labels or 'INCOME_SALARY' in labels:
        category='income';signals=['paycheck','possible_direct_deposit']
    elif primary=='INCOME':
        category='income'
    elif primary=='BANK_FEES' or detailed.startswith('BANK_FEES_'):
        category='fees'
    elif primary=='FOOD_AND_DRINK':
        category='dining'
    elif primary=='GENERAL_MERCHANDISE':
        category='shopping'
    elif primary=='TRANSPORTATION':
        category='transportation'
    elif primary=='RENT_AND_UTILITIES':
        category='utilities'
    elif primary=='MEDICAL':
        category='health'
    elif primary=='TRAVEL':
        category='travel'
    elif primary=='ENTERTAINMENT':
        category='entertainment'
    elif primary=='LOAN_PAYMENTS':
        category='loan'
    counterparties=[]
    for p in (row.get('counterparties') or [])[:8]:
        if isinstance(p,dict):
            counterparties.append({'name':_plain(p.get('name'),100),'type':_plain(p.get('type'),35),
                                   'entityId':_plain(p.get('entity_id'),100),
                                   'logoUrl':_safe_logo(p.get('logo_url'))})
    location=row.get('location') or {}
    if not isinstance(location,dict):
        location={}
    return {'plaidCategoryPrimary':primary or None,'plaidCategoryDetailed':detailed or None,
            'plaidConfidence':confidence or None,'plaidSuggestedCategory':category,
            'plaidCategorySignals':signals,'counterparties':counterparties,
            'merchantName':_plain(row.get('merchant_name'),100),
            'merchantLogoUrl':_safe_logo(row.get('logo_url')) or next((v['logoUrl'] for v in counterparties if v.get('logoUrl')),None),
            'merchantWebsite':_safe_url(row.get('website')),
            'paymentChannel':_plain(row.get('payment_channel'),32),
            'authorizedDate':_plain(row.get('authorized_date'),10),
            'location':{k:_plain(location.get(k),70) for k in ('city','region','country') if location.get(k)}}
