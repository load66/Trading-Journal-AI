"""Conservative, deterministic Finances categories. No side effects or provider calls.

Financial records are authoritative. Classification is an annotation and may be
superseded by a revision-bound user's correction. It never changes bonus milestones.
"""
import re
from collections import defaultdict
from decimal import Decimal, InvalidOperation

CATEGORIES = frozenset((
    'unreviewed','verification','income','housing','loan','utilities','groceries',
    'dining','transportation','shopping','insurance','health','entertainment','fees','taxes',
    'travel','subscription','transfer','loan_proceeds','refund','bonus','other'
))
VERIFICATION = re.compile(
    r'(?<![a-z])(?:acct\s*verify|acctverify|verification|verify|trial\s*dep|'
    r'micro\s*dep|test\s*dep|rtp\s*test|rtptststtl|rtp\s*tst\s*sttl|'
    r'prenote|validation)(?![a-z])', re.I
)
PAYROLL = re.compile(r'\b(?:payroll|salary|wages|direct\s*dep(?:osit)?|dir\s*dep|ppd|paycheck)\b', re.I)
REFUND = re.compile(r'\b(?:refund|return(?:ed)?\s+(?:purchase|item|payment)|reimbursement|merchant credit)\b', re.I)
INTEREST = re.compile(r'\b(?:interest (?:paid|earned|credit)|dividends?|apy interest)\b', re.I)
HOUSING = re.compile(
    r'\b(?:mortgage|mortg|mtge?|home\s*mtg|home\s+loan|rent|landlord|apartment\s+lease|'
    r'mr\.?\s*cooper|loancare|newrez|shellpoin(?:t)?|rocket\s+mortgage|pennymac|'
    r'freedom\s+mortgage|dovenmuehle|cenlar|nationstar|lakeview\s+loan|'
    r'carrington\s+mortgage|roundpoint|sps\s+servicing)\b', re.I)
LOAN = re.compile(r'\b(?:student loan|auto loan|vehicle loan|personal loan|loan payment)\b', re.I)
LOAN_PROCEEDS = re.compile(
    r'\b(?:loan\s+proceeds?|loan\s+disbursement|personal\s+loan|cash\s+advance|'
    r'loan\s+advance|loan|advance|disbursement|sofi\s+lending|one\s+finance|'
    r'upstart|avant|lendingclub|oportun|earnest)\b',re.I
)
OWNER_TRANSFER_ACTION = re.compile(r'\b(?:transfr|transfer|ach\s*transfer|ach\s*xfer|xfer|zelle\s+from|from)\b',re.I)
P2P_PREFIX = re.compile(r'^\s*(?:(?:zelle|venmo|cash\s*app|p2p)\s+(?:from\s+)?|from\s+)(.+?)\s*$',re.I)
P2P_PERSON = re.compile(r'^[A-Za-z][a-z]+(?:\s+[A-Za-z][a-z]+){1,2}$',re.I)
P2P_BUSINESS = re.compile(r'\b(?:llc|inc|ltd|corp|company|payroll|salary|wages|deposit|loan|lending|bank|fcu|savings|checking|account|brokerage|investment|retirement|personal)\b',re.I)

def owner_transfer_description(description, owner_names):
    """Only a private owner-supplied name together with an explicit transfer action."""
    text=re.sub(r'\s+',' ',str(description or '').strip())
    if not OWNER_TRANSFER_ACTION.search(text):
        return False
    for name in owner_names or ():
        normalized=re.sub(r'\s+',' ',str(name or '').strip()).casefold()
        if 3<=len(normalized)<=100 and re.fullmatch(r'[a-z]+(?: [a-z]+){1,3}',normalized):
            if re.search(r'(?<![a-z])'+re.escape(normalized)+r'(?![a-z])',text.casefold()):
                return True
    return False

def possible_person_to_person(description):
    text=re.sub(r'\s+',' ',str(description or '').strip())
    match=P2P_PREFIX.fullmatch(text)
    return bool(match and not P2P_BUSINESS.search(text) and
                not PAYROLL.search(text) and not LOAN_PROCEEDS.search(text) and
                P2P_PERSON.fullmatch(match.group(1)))

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

FINANCIAL_DESTINATION = re.compile(
    r'\b(?:bank|fcu|credit\s*union|savings|checking|brokerage|investing|investment|'
    r'ira|citibank|citi|u\.?\s*s\.?\s*bank|capital\s*one|chase|wells\s*fargo|'
    r'bofa|bank\s*of\s*america|amex|american\s*express|discover|synchrony|'
    r'lafayette|navy\s*federal|paypal|venmo|cash\s*app|zelle|x\s*money|'
    r'one\s*pay|onepay|robinhood|fidelity|schwab|vanguard)\b', re.I)
INVESTMENT_DESTINATION = re.compile(
    r'\b(?:brokerage|investing|investment|ira|robinhood|fidelity|schwab|vanguard)\b',re.I)
FINANCIAL_DIRECTION = re.compile(r'^\s*(?:to|from)\s+(.+?)\s*$',re.I)
ACCOUNT_ARROW = re.compile(r'^\s*(?:savings|checking|brokerage|investment|retirement|cash\s*account)\s*(?:\(\d{4}\)|[·*x]+\d{4})?\s*(?:→|->| to )\s*(?:savings|checking|brokerage|investment|retirement|cash\s*account)\s*(?:\(\d{4}\)|[·*x]+\d{4})?\s*$',re.I)
BARE_CARD_ISSUER = re.compile(
    r'^\s*(?:capital\s*one|u\.?\s*s\.?\s*bank|us\s*bank|citibank|citi|'
    r'amex|american\s*express|discover|chase|synchrony|wells\s*fargo|'
    r'bank\s*of\s*america|bofa)\s*$',re.I)
FINANCIAL_NAME = re.compile(
    r'\b(?:bank|fcu|credit\s*union|citibank|citi|capital\s*one|'
    r'u\.?\s*s\.?\s*bank|chase|amex|discover|paypal|venmo|cash\s*app|'
    r'robinhood|fidelity|schwab|vanguard|brokerage|onepay|x\s*money)\b',re.I)

def financial_movement(description, amount):
    """Classify explicit financial account movements before merchant spending."""
    try:
        value = Decimal(str(amount))
    except (InvalidOperation, ValueError):
        return None
    if not value.is_finite() or value == 0:
        return None
    label = str(description or '').strip()
    # A recognizable mortgage debit is not an issuer/card payment.
    if value < 0 and HOUSING.search(label):
        return None
    if ACCOUNT_ARROW.fullmatch(label):
        return 'transfer', ['matched_internal_transfer' if not INVESTMENT_DESTINATION.search(label) else 'investment_transfer']
    direction = FINANCIAL_DIRECTION.fullmatch(label)
    if direction and FINANCIAL_DESTINATION.search(direction.group(1)):
        code = 'investment_transfer' if INVESTMENT_DESTINATION.search(direction.group(1)) else 'transfer'
        return 'transfer', [code] if code == 'investment_transfer' else [code, 'ownership_unconfirmed']
    if value < 0 and BARE_CARD_ISSUER.fullmatch(label):
        return 'transfer', ['card_payment']
    if ISSUER_PAYMENT.search(label) and PAYMENT_ACTION.search(label):
        return 'transfer', ['card_payment']
    if BROKER_CONTRIBUTION.search(label):
        return 'transfer', ['investment_transfer']
    if re.search(r'\b(?:x\s*money\s*transfer|payment\s*thank\s*you)\b',label,re.I):
        return 'transfer', ['transfer']
    if value < 0 and (value == value.to_integral_value() or abs(value) >= 1000) and (
            direction or FINANCIAL_NAME.search(label)):
        return 'unreviewed', ['possible_transfer']
    return None

FINANCIAL_SOURCE_BARRIER = re.compile(r'\b(?:schwab|brokerage|robinhood|fidelity|vanguard|bank|credit\s*union|fcu|savings|checking|paypal|venmo|zelle|cash\s*app)\b',re.I)
SELF_P2P = re.compile(r'\b(?:zelle|venmo|cash app|cashapp|p2p)\b.*\b(?:self|my own|myself)\b',re.I)
MERCHANTS = (
    ('groceries', r'\b(?:walmart|aldi|kroger|whole foods|safeway|trader joe|'
                  r'costco|sam.?s club|grocery|supermarket|publix|hy vee)\b'),
    ('dining', r'\b(?:starbucks|mcdonald|chipotle|doordash|ubereats|grubhub|'
               r'restaurant|coffee shop|panera|dunkin|taco bell)\b'),
    ('transportation', r'\b(?:shell oil|exxon|chevron|quiktrip|casey.?s|'
                       r'gas station|fuel|parking|toll|lyft|uber trip)\b'),
    ('subscription', r'\b(?:netflix|spotify|youtube premium|hulu|apple\s*(?:subscription|bill|\.com/bill)|icloud|'
                     r'disney plus|amazon prime membership)\b'),
    ('utilities', r'\b(?:liberty utilities|electric company|water utility|'
                  r'internet provider|utility bill|cellular bill|at&t wireless|at&t|t[-\s]?mobile|verizon)\b'),
    ('shopping', r'\b(?:amazon|best buy|target|ebay|etsy|apple|walmart|costco)\b'),
    ('insurance', r'\b(?:progressive|prog\s+advanced|geico|state\s+farm|allstate|'
                  r'liberty\s+mutual|usaa\s+insurance|nationwide|farmers|aaa\s+insurance|'
                  r'ins\s+prem|insurance\s+prem(?:ium)?|auto\s+ins|home\s+ins)\b'),
    ('travel', r'\b(?:airline|delta air|united air|southwest air|hotel|airbnb|rental car)\b'),
    ('health', r'\b(?:pharmacy|walgreens|cvs|hospital|doctor|dental|clinic|medical)\b'),
    ('entertainment',r'\b(?:movie theater|cinema|amc theatre|concert|ticketmaster)\b'),
    ('fees',r'\b(?:overdraft fee|maintenance fee|service charge|atm fee|late fee)\b'),
    ('taxes',r'\b(?:irs payment|state tax|property tax|estimated tax)\b'),
)
_COMPILED_MERCHANTS = tuple((cat,re.compile(pattern,re.I)) for cat,pattern in MERCHANTS)


def category_for(description, amount, *, pending=False, kind=None, owner_names=()):
    """Return (category, confidence, signals); never conflate unknown with zero."""
    try:
        number = Decimal(str(amount))
    except (ValueError, InvalidOperation):
        return 'unreviewed','low',[]
    if not number.is_finite():
        return 'unreviewed','low',[]
    text = str(description or '')
    # A credit from a lender is borrowed money, never automatic earnings.
    # Explicit owner reviews and scoped rules are applied by the ledger later.
    if number > 0 and not pending and LOAN_PROCEEDS.search(text):
        return 'loan_proceeds','high',['possible_loan_proceeds']
    if not pending and owner_transfer_description(text,owner_names):
        return 'transfer','medium',['owner_transfer_unpaired']
    movement = financial_movement(text, number)
    if movement and not pending:
        category, signals = movement
        return category, 'high' if category == 'transfer' else 'low', signals
    if number == 0 or (abs(number) < 1 and VERIFICATION.search(text)):
        return 'verification','high',[]
    if pending:
        return 'unreviewed','low',[]
    if kind == 'possible_bonus' or (number > 0 and re.search(r'\b(?:promotional\s+bonus|promotion(?:al)?\s+(?:credit|reward)|bank\s+bonus|signup\s+bonus|sign[- ]?up\s+reward)\b', text, re.I)):
        return 'bonus','high',['possible_bonus']
    if kind == 'possible_reversal':
        return 'unreviewed','high',['possible_reversal']
    if number > 0 and REFUND.search(text):
        return 'refund','high',['possible_refund']
    # A generic inbound bank 'Payment' is not evidence of wages or income.
    # Keep it neutral until a distinct account supplies a unique opposite leg,
    # or the owner confirms a transfer. Manual reviews still take precedence.
    if number > 0 and re.fullmatch(r'\s*payment(?:\s+received)?\s*', text, re.I):
        return 'unreviewed','low',['possible_transfer']
    if number > 0 and possible_person_to_person(text):
        if number != number.to_integral_value():
            return 'transfer','medium',['possible_p2p']
        return 'unreviewed','low',['possible_transfer']
    if number > 0 and PAYROLL.search(text) and not FINANCIAL_SOURCE_BARRIER.search(text):
        return 'income','high',['possible_direct_deposit','paycheck']
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
    return 'unreviewed','low',[]



RENT_ADDRESS = re.compile(
    r'^\s*\d{1,6}\s+(?:(?:n|s|e|w)\s+)?'
    r'[A-Za-z]+(?:\s+[A-Za-z]+){0,4}\s+'
    r'(?:st|street|rd|road|ave|avenue|ln|lane|dr|drive|blvd|boulevard)\.?\s*$',re.I
)


def annotate_recurring_rent(records):
    """Suggest housing only after two comparable posted monthly address debits."""
    grouped=defaultdict(list)
    for row in records:
        if (not isinstance(row,dict) or row.get('pending') or
                row.get('categorySource') in ('user','user_rule') or
                row.get('category') not in ('unreviewed','housing') or
                not isinstance(row.get('posted'),(int,float))):
            continue
        address=re.sub(r'\s+',' ',str(row.get('description') or '').strip()).casefold()
        if not RENT_ADDRESS.fullmatch(address):
            continue
        try:
            amount=Decimal(str(row['amount']))
        except (ValueError,InvalidOperation,KeyError):
            continue
        if amount.is_finite() and amount<0:
            grouped[(str(row.get('accountKey') or ''),address)].append((row,amount))
    for matches in grouped.values():
        ordered=sorted(matches,key=lambda item:item[0]['posted'])
        qualifying=set()
        for i,(left,a) in enumerate(ordered):
            for right,b in ordered[i+1:]:
                days=(right['posted']-left['posted'])/86400
                if days>35:
                    break
                if 26<=days<=35 and a*Decimal('1.25')<=b<=a*Decimal('.75'):
                    qualifying.update((id(left),id(right)))
        for row,_ in matches:
            if id(row) in qualifying:
                row['category']='housing'
                row['confidence']='medium'
                row['signals']=sorted(set((row.get('signals') or [])+['possible_rent']))


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
