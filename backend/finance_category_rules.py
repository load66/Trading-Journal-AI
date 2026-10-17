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
    r'payment thank you|payment to card|credit card autopay|x money transfer|crd)\b',re.I
)
ISSUER_PAYMENT = re.compile(
    r'\b(?:capital\s*one|chase|jpmorgan|american\s*express|amex|discover|'
    r'citi(?:bank)?|citicards|robinhood|fidelity|schwab|vanguard|'
    r'e\s*\*?\s*trade|barclays|synchrony|wells\s*fargo|'
    r'bank\s*of\s*america|us\s*bank)\b', re.I)
PAYMENT_ACTION = re.compile(
    r'\b(?:pmt|pymt|payment|autopay|auto\s*pay|epay|e\s*pay|'
    r'crcardpmt|credit\s*card\s*pmt|ccpmt|cardmember\s*payment)\b', re.I)
BROKER_CONTRIBUTION = re.compile(
    r'\b(?:robinhood|fidelity|schwab|vanguard|e\s*\*?\s*trade|brokerage|'
    r'investment|401k|roth\s*ira|traditional\s*ira|ira)\b'
    r'.*\b(?:deposit|contribut(?:ion|e)|transfer|funding|add\s*cash)\b|'
    r'\b(?:contribut(?:ion|e)|transfer|funding|deposit)\b.*'
    r'\b(?:robinhood|fidelity|schwab|vanguard|e\s*\*?\s*trade|brokerage|'
    r'investment|401k|roth\s*ira|traditional\s*ira|ira)\b', re.I)
# Financial destinations are evidence of money movement, not a merchant purchase.
FINANCIAL_DESTINATION = re.compile(
    r'\b(?:bank|fcu|credit\s+union|savings?|checking|brokerage|invest(?:ing|ment)|'
    r'ira|citibank|citi\s*bank|u\.?\s*s\.?\s*bank|capital\s+one|chase|'
    r'wells\s+fargo|bofa|bank\s+of\s+america|amex|american\s+express|'
    r'discover|synchrony|lafayette\s+fcu|navy\s+federal|paypal|pay\s+pal|'
    r'venmo|cash\s*app|zelle|x\s*money|one\s*pay|onepay|robinhood|'
    r'fidelity|schwab|vanguard)\b',re.I)
DIRECTIONAL_ACCOUNT = re.compile(r'^\s*(?:to|from)\s+(.+)r'\b(?:zelle|venmo|cash app|cashapp|p2p)\b.*\b(?:self|my own|myself)\b',re.I)
MERCHANTS = (
    ('groceries', r'\b(?:walmart|aldi|kroger|whole foods|safeway|trader joe|'
                  r'costco|sam.?s club|grocery|supermarket|publix|hy vee)\b'),
    ('dining', r'\b(?:starbucks|mcdonald|chipotle|doordash|ubereats|grubhub|'
               r'restaurant|coffee shop|panera|dunkin|taco bell)\b'),
    ('transportation', r'\b(?:shell oil|exxon|chevron|quiktrip|casey.?s|'
                       r'gas station|fuel|parking|toll|lyft|uber trip)\b'),
    ('subscription', r'\b(?:netflix|spotify|youtube premium|hulu|apple subscription|apple\s*(?:com\s*/\s*bill|icloud|i\s*cloud)|
                     r'disney plus|amazon prime membership)\b'),
    ('utilities', r'\b(?:t[ -]?mobile|verizon|at\s*&\s*t|att wireless|liberty utilities|electric company|water utility|
                  r'internet provider|utility bill|cellular bill|at&t wireless)\b'),
    ('shopping', r'\b(?:apple|amazon|walmart|target|best buy|ebay|etsy)\b'),
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
    financial = movement(text, number)
    if financial:
        return financial
    if number > 0 and REFUND.search(text):
        return 'refund','high',['possible_refund']
    if number > 0 and PAYROLL.search(text):
        return 'income','high',['possible_direct_deposit']
    if number > 0 and INTEREST.search(text):
        return 'income','high',[]
    if number < 0 and re.search(r'\bfee\b|service charge|maintenance charge|overdraft', text, re.I):
        return 'fees','high',['possible_fee']
    # Financial-account movements take precedence over recurring merchants.
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
    return ('unreviewed','low',['possible_transfer']) if suspicious_unknown(text,number) else ('unreviewed','low',[])


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
    for row in records:
        if not isinstance(row, dict) or row.get('pending'):
            continue
        try:
            amount = Decimal(str(row.get('amount')))
        except (InvalidOperation, ValueError):
            continue
        if not amount.is_finite() or not (-2 < amount < 0):
            continue
        key = (str(row.get('accountKey')), re.sub(r'\s+', ' ', str(row.get('description') or '').strip().lower()))
        credits = [r for r in records if isinstance(r, dict) and
                   (str(r.get('accountKey')), str(r.get('id'))) in matched and
                   (str(r.get('accountKey')), re.sub(r'\s+', ' ', str(r.get('description') or '').strip().lower())) == key and
                   isinstance(r.get('posted'), (int, float)) and isinstance(row.get('posted'), (int, float)) and
                   abs(r['posted'] - row['posted']) <= 3 * 86400]
        if len(credits) == 2 and sum(Decimal(str(r['amount'])) for r in credits) == -amount:
            matched.add((str(row['accountKey']), str(row['id'])))
    return matched
,re.I)
INVESTMENT_DESTINATION = re.compile(
    r'\b(?:brokerage|invest(?:ing|ment)|ira|robinhood|fidelity|schwab|vanguard)\b',re.I)
BARE_CARD_ISSUER = re.compile(
    r'^\s*(?:capital\s+one|u\.?\s*s\.?\s*bank|citibank|citi\s*bank|'
    r'amex|american\s+express|discover)\s*r'\b(?:zelle|venmo|cash app|cashapp|p2p)\b.*\b(?:self|my own|myself)\b',re.I)
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
    if number < 0 and re.search(r'\bfee\b|service charge|maintenance charge|overdraft', text, re.I):
        return 'fees','high',['possible_fee']
    # Financial-account movements take precedence over recurring merchants.
    if (ISSUER_PAYMENT.search(text) and PAYMENT_ACTION.search(text)) or BROKER_CONTRIBUTION.search(text):
        return 'transfer','high',['possible_transfer']
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
    for row in records:
        if not isinstance(row, dict) or row.get('pending'):
            continue
        try:
            amount = Decimal(str(row.get('amount')))
        except (InvalidOperation, ValueError):
            continue
        if not amount.is_finite() or not (-2 < amount < 0):
            continue
        key = (str(row.get('accountKey')), re.sub(r'\s+', ' ', str(row.get('description') or '').strip().lower()))
        credits = [r for r in records if isinstance(r, dict) and
                   (str(r.get('accountKey')), str(r.get('id'))) in matched and
                   (str(r.get('accountKey')), re.sub(r'\s+', ' ', str(r.get('description') or '').strip().lower())) == key and
                   isinstance(r.get('posted'), (int, float)) and isinstance(row.get('posted'), (int, float)) and
                   abs(r['posted'] - row['posted']) <= 3 * 86400]
        if len(credits) == 2 and sum(Decimal(str(r['amount'])) for r in credits) == -amount:
            matched.add((str(row['accountKey']), str(row['id'])))
    return matched
,re.I)
UNKNOWN_FINANCIAL = re.compile(
    r'\b(?:bank|fcu|credit\s+union|savings?|checking|brokerage|invest(?:ing|ment)|ira|'
    r'citibank|capital\s+one|u\.?\s*s\.?\s*bank|amex|discover|robinhood|'
    r'fidelity|schwab|vanguard|paypal|onepay)\b',re.I)
def movement(description, amount):
    """Conservative financial-flow signal. Call before spending and AI rules."""
    try:
        number = Decimal(str(amount))
        if not number.is_finite():
            return None
    except (ValueError, InvalidOperation):
        return None
    text = str(description or '').strip()
    match = DIRECTIONAL_ACCOUNT.match(text)
    if match and FINANCIAL_DESTINATION.search(match.group(1)):
        if match.group(0).lower().startswith('to ') and INVESTMENT_DESTINATION.search(match.group(1)):
            return ('transfer','high',['investment_transfer','possible_transfer'])
        return ('transfer','high',['possible_transfer'])
    if number < 0 and BARE_CARD_ISSUER.fullmatch(text):
        return ('transfer','high',['card_payment','possible_transfer'])
    if number < 0 and ISSUER_PAYMENT.search(text) and PAYMENT_ACTION.search(text):
        return ('transfer','high',['card_payment','possible_transfer'])
    if BROKER_CONTRIBUTION.search(text):
        return ('transfer','high',['investment_transfer','possible_transfer'])
    return None

def suspicious_unknown(description, amount):
    """Only uncertain large account-like movements; never flag ordinary purchases."""
    try:
        number = Decimal(str(amount))
        if not number.is_finite() or number >= 0:
            return False
    except (ValueError, InvalidOperation):
        return False
    text = str(description or '').strip()
    financial = bool(DIRECTIONAL_ACCOUNT.match(text) or UNKNOWN_FINANCIAL.search(text))
    return financial and (abs(number) >= 1000 or
                          abs(number) == abs(number).to_integral_value())

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
    if number < 0 and re.search(r'\bfee\b|service charge|maintenance charge|overdraft', text, re.I):
        return 'fees','high',['possible_fee']
    # Financial-account movements take precedence over recurring merchants.
    if (ISSUER_PAYMENT.search(text) and PAYMENT_ACTION.search(text)) or BROKER_CONTRIBUTION.search(text):
        return 'transfer','high',['possible_transfer']
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
    for row in records:
        if not isinstance(row, dict) or row.get('pending'):
            continue
        try:
            amount = Decimal(str(row.get('amount')))
        except (InvalidOperation, ValueError):
            continue
        if not amount.is_finite() or not (-2 < amount < 0):
            continue
        key = (str(row.get('accountKey')), re.sub(r'\s+', ' ', str(row.get('description') or '').strip().lower()))
        credits = [r for r in records if isinstance(r, dict) and
                   (str(r.get('accountKey')), str(r.get('id'))) in matched and
                   (str(r.get('accountKey')), re.sub(r'\s+', ' ', str(r.get('description') or '').strip().lower())) == key and
                   isinstance(r.get('posted'), (int, float)) and isinstance(row.get('posted'), (int, float)) and
                   abs(r['posted'] - row['posted']) <= 3 * 86400]
        if len(credits) == 2 and sum(Decimal(str(r['amount'])) for r in credits) == -amount:
            matched.add((str(row['accountKey']), str(row['id'])))
    return matched
