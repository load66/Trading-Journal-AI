"""Conservative, deterministic Finances categories. No side effects or provider calls.

Financial records are authoritative. Classification is an annotation and may be
superseded by a revision-bound user's correction. It never changes bonus milestones.
"""
import re
from collections import defaultdict
from decimal import Decimal, InvalidOperation

CATEGORIES = frozenset((
    'unreviewed','verification','income','housing','loan','utilities','groceries',
    'dining','transportation','shopping','health','entertainment','fees','taxes',
    'travel','subscription','transfer','refund','bonus','other'
))
VERIFICATION = re.compile(
    r'(?<![a-z])(?:acct\s*verify|acctverify|verification|verify|trial\s*dep|'
    r'micro\s*dep|test\s*dep|rtp\s*test|rtptststtl|rtp\s*tst\s*sttl|'
    r'prenote|validation)(?![a-z])', re.I
)
PAYROLL = re.compile(r'\b(?:payroll|salary|wages|direct\s*dep(?:osit)?|paycheck)\b', re.I)
REFUND = re.compile(r'\b(?:refund|return(?:ed)?\s+(?:purchase|item|payment)|reimbursement|merchant credit)\b', re.I)
INTEREST = re.compile(r'\b(?:interest (?:paid|earned|credit)|dividends?|apy interest)\b', re.I)
HOUSING = re.compile(r'\b(?:mortgage|home loan|rent|landlord|apartment lease)\b', re.I)
LOAN = re.compile(r'\b(?:student loan|auto loan|vehicle loan|personal loan|loan payment)\b', re.I)
TRANSFER = re.compile(
    r'\b(?:transfer|xfer|pmt|autopay|epayment|credit card payment|'
    r'payment thank you|payment to card|credit card autopay|x money transfer)\b',re.I
)
SELF_P2P = re.compile(r'\b(?:zelle|venmo|cash app|cashapp|p2p)\b.*\b(?:self|my own|myself)\b',re.I)
MERCHANTS = (
    ('groceries', r'\b(?:walmart|aldi|kroger|whole foods|safeway|trader joe|'
                  r'costco|sam.?s club|grocery|supermarket|publix|hy vee)\b'),
    ('dining', r'\b(?:starbucks|mcdonald|chipotle|doordash|ubereats|grubhub|'
               r'restaurant|coffee shop|panera|dunkin|taco bell)\b'),
    ('transportation', r'\b(?:shell oil|exxon|chevron|quiktrip|casey.?s|'
                       r'gas station|fuel|parking|toll|lyft|uber trip)\b'),
    ('subscription', r'\b(?:netflix|spotify|youtube premium|hulu|apple subscription|'
                     r'disney plus|amazon prime membership)\b'),
    ('utilities', r'\b(?:liberty utilities|electric company|water utility|'
                  r'internet provider|utility bill|cellular bill|at&t wireless)\b'),
    ('shopping', r'\b(?:amazon marketplace|amazon retail|best buy|target|ebay|etsy)\b'),
    ('travel', r'\b(?:airline|delta air|united air|southwest air|hotel|airbnb|rental car)\b'),
    ('health', r'\b(?:pharmacy|walgreens|cvs|hospital|doctor|dental|clinic|medical)\b'),
    ('entertainment',r'\b(?:movie theater|cinema|amc theatre|concert|ticketmaster)\b'),
    ('fees',r'\b(?:overdraft fee|maintenance fee|service charge|atm fee|late fee)\b'),
    ('taxes',r'\b(?:irs payment|state tax|property tax|estimated tax)\b'),
)
_COMPILED_MERCHANTS = tuple((cat,re.compile(pattern,re.I)) for cat,pattern in MERCHANTS)


def category_for(description, amount, *, pending=False, kind=None):
    """Return (category, confidence, signals); never conflate unknown with zero."""
    try:
        number = Decimal(str(amount))
    except (ValueError, InvalidOperation):
        return 'unreviewed','low',[]
    if not number.is_finite():
        return 'unreviewed','low',[]
    text = str(description or '')
    if number == 0 or (abs(number) < 1 and VERIFICATION.search(text)):
        return 'verification','high',[]
    if pending:
        return 'unreviewed','low',[]
    if kind == 'possible_bonus':
        return 'bonus','high',['possible_bonus']
    if kind == 'possible_reversal':
        return 'unreviewed','high',['possible_reversal']
    if number > 0 and REFUND.search(text):
        return 'refund','high',['possible_refund']
    if number > 0 and PAYROLL.search(text):
        return 'income','high',['possible_direct_deposit']
    if number > 0 and INTEREST.search(text):
        return 'income','high',[]
    if number < 0 and re.search(r'\\bfee\\b|service charge|maintenance charge|overdraft', text, re.I):
        return 'fees','high',['possible_fee']
    if number < 0 and HOUSING.search(text):
        return 'housing','high',[]
    if number < 0 and LOAN.search(text):
        return 'loan','high',[]
    if TRANSFER.search(text) or SELF_P2P.search(text):
        return 'transfer','medium',['possible_transfer']
    if number < 0:
        for category, pattern in _COMPILED_MERCHANTS:
            if pattern.search(text):
                return category,'medium',[]
    return 'unreviewed','low',[]


def micro_deposit_refs(records):
    """Recognize paired sub-$1 credits on the same account/source within 3 days.

    Input rows have account/key, opaque transaction ID, description, amount and
    posted timestamp. Identical descriptions alone never prove ownership.
    """
    grouped = defaultdict(list)
    for row in records:
        if not isinstance(row, dict) or row.get('pending'):
            continue
        try:
            amount = Decimal(str(row.get('amount')))
        except (InvalidOperation, ValueError):
            continue
        timestamp = row.get('posted')
        if not amount.is_finite() or not isinstance(timestamp, (int, float)):
            continue
        if not (Decimal('0') < amount < Decimal('1')):
            continue
        account = row.get('accountKey')
        ident = row.get('id')
        source = re.sub(r'\s+',' ',str(row.get('description') or '').strip().lower())
        if not account or not ident or not source:
            continue
        grouped[(str(account),source)].append((timestamp,str(ident)))
    matched = set()
    for (account,_source), group in grouped.items():
        group.sort()
        for i, (at,ident) in enumerate(group):
            if any(ident!=other and 0 <= at2-at <= 3*86400
                   for at2,other in group[i+1:]):
                matched.add((account,ident))
                for at2,other in group[i+1:]:
                    if at2-at <= 3*86400:
                        matched.add((account,other))
    return matched
