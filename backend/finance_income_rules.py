"""Conservative paychecks and account-to-account movement annotations.

Read-only financial summaries. Never change provider evidence or bonus eligibility.
The rule needs two independently posted employer credits; a lone corporate deposit is
a suggestion, not verified payroll. No personally identifying account strings leave
the authenticated Finance response.
"""
import re
from collections import defaultdict
from decimal import Decimal, InvalidOperation

DAY = 86400
COMPANY = re.compile(r'\b(?:inc\.?|llc|ltd\.?|corp(?:oration)?|company|foods?|industries|manufacturing|logistics|healthcare|technologies|services|hospitality|holdings|systems|group)\b', re.I)
PAY_DESCRIPTOR = re.compile(r'\b(?:payroll|paycheck|dir\s*dep|direct\s*dep(?:osit)?|salary|wages|\bppd\b)\b', re.I)
FINANCIAL_SOURCE = re.compile(r'\b(?:bank|credit union|\bfcu\b|savings|checking|brokerage|investment|schwab|robinhood|fidelity|vanguard|paypal|venmo|zelle|cash app|wallet|citibank|chase|wells fargo|capital one|lafayette|onepay|x money)\b', re.I)
PERSON = re.compile(r'\b(?:zelle|venmo|cashapp|cash app|p2p|from person|personal payment|gift from)\b', re.I)
PREFIX = re.compile(r'^(?:(?:ach|ppd|direct\s*dep(?:osit)?|dir\s*dep|payroll|deposit|credit)\s*[-:*#\s]+)+',re.I)
TRAIL = re.compile(r'\s+(?:payroll|paycheck|ppd|dir\s*dep|direct\s*deposit|salary|wages)\s*$', re.I)


def employer_name(value):
    name = PREFIX.sub('', str(value or '').strip())
    name = TRAIL.sub('', name)
    name = re.sub(r'\s+', ' ', name).strip(' -:*,')
    if not name or len(name) > 90 or FINANCIAL_SOURCE.search(name) or PERSON.search(name):
        return None
    return name


def _credit(value):
    try:
        result=Decimal(str(value))
        return result if result.is_finite() and result > 0 else None
    except (ValueError, InvalidOperation, TypeError):
        return None


def annotate_paychecks(records):
    """Annotate presentation rows in place. Called after transfer matching.

    A positive deposit from a company with 2 similar amounts posted 6-9,
    11-17, or 13-19 days apart is income. A first company credit, or one
    containing a payroll ACH marker, can be reviewed. A genuine user override
    and any identified movement always take precedence.
    """
    by_employer=defaultdict(list)
    for row in records:
        value=_credit(row.get('amount'))
        description=str(row.get('description') or '')
        employer=employer_name(description)
        if (not employer or value is None or row.get('pending') or
                not isinstance(row.get('posted'), (int,float)) or
                row.get('category') in ('transfer','refund','verification','bonus') or
                any(s in row.get('signals',[]) for s in
                    ('matched_internal_transfer','investment_transfer','possible_transfer','card_payment'))):
            continue
        row['_paycheckEmployer']=employer
        by_employer[(str(row.get('accountKey') or ''), employer.casefold())].append((row,value))
    # Do not infer a payroll pattern from transactions on different bank accounts.
    for entries in by_employer.values():
        ordered=sorted(entries,key=lambda item:item[0]['posted'])
        qualifying=set()
        for i,(left,a) in enumerate(ordered):
            for right,b in ordered[i+1:]:
                days=(right['posted']-left['posted'])/DAY
                if days>19:
                    break
                if (6 <= days <= 9 or 11 <= days <= 19) and a*Decimal('.75')<=b<=a*Decimal('1.25'):
                    qualifying.update((id(left),id(right)))
        for row,_ in ordered:
            employer=row.pop('_paycheckEmployer')
            if row.get('categorySource') in ('user','user_rule'):
                if row.get('category')=='income':
                    row['signals']=sorted(set(row.get('signals',[])+['paycheck', 'possible_direct_deposit']))
                continue
            explicit=bool(PAY_DESCRIPTOR.search(row.get('description') or ''))
            is_company=bool(COMPANY.search(employer))
            if (id(row) in qualifying and is_company) or explicit:
                row.update(category='income',confidence='high',
                           merchant=employer,signals=sorted(set(row.get('signals',[])+['paycheck','possible_direct_deposit'])))
            elif is_company and row.get('category')=='unreviewed':
                row.update(merchant=employer,signals=sorted(set(row.get('signals',[])+['possible_paycheck'])))
