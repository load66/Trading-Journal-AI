"""Bounded official promotion availability checks; never accesses financial records.

Publisher posts discover links. Only institution-scoped approved official hosts can
support conclusions. No missing clause, bank footprint, or ATM network implies eligibility.
"""
import asyncio
import hashlib
import json
import logging
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

from fastapi import APIRouter, HTTPException, Request
from auth import auth_required, authorize_header, AuthError
import bank_verification as evidence

router = APIRouter()
log = logging.getLogger('uvicorn.error')
PUBLISHERS = {'www.doctorofcredit.com', 'frequentmiler.com', 'www.hustlermoneyblog.com'}
# Institution identities, not eligibility rules or hardcoded promotion amounts.
REGISTRY = {
 'truist': (r'\btruist\b', {'www.truist.com'}, ['https://www.truist.com/checking/lp/promos/checking-promo4']),
 'busey': (r'\bbusey\b', {'www.busey.com'}, ['https://www.busey.com/personal/products-and-services/banking/your-choice-your-reward']),
 'upgrade': (r'\bupgrade\b', {'www.upgrade.com'}, []),
 'westerra': (r'\bwesterra\b', {'www.westerracu.com'}, []),
 'wellby': (r'\bwellby\b', {'www.wellbyfinancial.com'}, []),
 'community-state-ia': (r'\[(?:IA|Iowa)\].*\bcommunity state bank\b', {'www.bankcsb.com','bankcsb.com'}, ['https://www.bankcsb.com/bonus']),
 'stark': (r'\bstark (?:federal|fcu|credit)\b', {'www.starkcu.org'}, []),
 'unitus': (r'\bunitus\b', {'www.unitusccu.com', 'join.unitusccu.com'}, []),
 'cobalt': (r'\bcobalt\b', {'cobaltcu.com', 'www.cobaltcu.com'}, []),
 'first-hawaiian': (r'\bfirst hawaiian\b', {'www.fhb.com'}, ['https://www.fhb.com/en/personal/checking']),
 'ccu-florida': (r'\bcommunity credit union (?:of )?florida\b', {'www.ccuflorida.org'}, []),
 'safe': (r'\bsafe credit union\b', {'www.safecu.org'}, ['https://www.safecu.org/personal/manage-your-money/checking-promo-get-300']),
 'chase': (r'\bchase\b', {'www.chase.com', 'account.chase.com'}, []),
 'wells': (r'\bwells fargo\b', {'www.wellsfargo.com', 'accountoffers.wellsfargo.com'}, []),
 'us-bank': (r'\bu\.?s\.? bank\b', {'www.usbank.com'}, []),
 'citi': (r'\b(?:citi|citibank)\b', {'www.citi.com', 'online.citi.com'}, []),
}
STATES = dict(zip('AL AK AZ AR CA CO CT DE DC FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV NH NJ NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY'.split(),
 ['Alabama','Alaska','Arizona','Arkansas','California','Colorado','Connecticut','Delaware','District of Columbia','Florida','Georgia','Hawaii','Idaho','Illinois','Indiana','Iowa','Kansas','Kentucky','Louisiana','Maine','Maryland','Massachusetts','Michigan','Minnesota','Mississippi','Missouri','Montana','Nebraska','Nevada','New Hampshire','New Jersey','New Mexico','New York','North Carolina','North Dakota','Ohio','Oklahoma','Oregon','Pennsylvania','Rhode Island','South Carolina','South Dakota','Tennessee','Texas','Utah','Vermont','Virginia','Washington','West Virginia','Wisconsin','Wyoming']))
evidence.PUBLIC_HOSTS.update(PUBLISHERS | set().union(*(x[1] for x in REGISTRY.values())))
_lock = threading.Lock()
_task = None
_last_started = 0.0
_priority_urls = []
_catalog_urls = set()
_last_priority_at = 0.0
_result = {'schemaVersion': 1, 'status': 'pending', 'records': {}}
MAX_AGE = timedelta(hours=24)


def profile_for(title):
    hits = [key for key, (pattern, _, _) in REGISTRY.items() if re.search(pattern, title, re.I)]
    return hits[0] if len(hits) == 1 else None


def approved(url, hosts):
    return evidence.safe_url(url) and urlsplit(url).hostname in hosts and urlsplit(url).path not in ('', '/') and not urlsplit(url).fragment


class Links(HTMLParser):
    def __init__(self):
        super().__init__(); self.rows = []; self.href = ''; self.parts = []
    def handle_starttag(self, tag, attrs):
        if tag == 'a': self.href = dict(attrs).get('href', ''); self.parts = []
    def handle_data(self, data):
        if self.href: self.parts.append(data)
    def handle_endtag(self, tag):
        if tag == 'a' and self.href:
            self.rows.append((self.href, ' '.join(self.parts))); self.href = ''


def discover(offer, key):
    _, hosts, fallbacks = REGISTRY[key]
    if not approved(offer['url'], PUBLISHERS): raise ValueError('Unapproved discovery post')
    raw, content_type, final_url = evidence.fetch_bytes(offer['url'])
    if urlsplit(final_url).hostname not in PUBLISHERS or 'html' not in content_type: raise ValueError('Invalid discovery response')
    links = Links(); links.feed(raw.decode('utf-8', errors='replace'))
    candidates = [(urljoin(final_url, href), label) for href, label in links.rows]
    candidates = [(url, label) for url, label in candidates if approved(url, hosts)]
    candidates.sort(key=lambda x: (0 if re.search(r'offer|promotion|bonus', x[1]+' '+x[0], re.I) else 1))
    # No login/application submission and no arbitrary publisher redirects.
    urls = [url for url, _ in candidates if not re.search(r'/login|/signin|/auth|/apply(?:/|$)|fee[-_ ]?schedule|privacy', urlsplit(url).path, re.I)]
    return list(dict.fromkeys(fallbacks + urls))[:3]


def state_codes(text):
    full={code for code,name in STATES.items() if re.search(r'\b'+name+r'\b',text,re.I)}
    codes=set()
    for m in re.finditer(r'\b[A-Z]{2}\b',text):
        code=m.group(0)
        if code not in STATES:continue
        before=text[:m.start()];after=text[m.end():]
        # OR and IN are also English words; require list/residency syntax.
        listed=bool(re.search(r'[:,;(]\s*$',before) and re.match(r'\s*(?:[,;.)]|$|(?:or|and|only)\b)',after))
        anchored=bool(re.search(r'\b(?:in|of|or|and)\s*$',before) and re.match(r'\s*(?:[,;.)]|$|(?:or|and|only)\b)',after))
        if listed or anchored:codes.add(code)
    return sorted(full|codes)


def rewards(title):
    # Deposit/balance/APY requirements are not promotion reward identities.
    return [m.group(1).replace(',', '') for m in re.finditer(r'\$\s*([\d,]+)', title)
            if not re.match(r'\s*(?:in\s+)?(?:cash|assets|direct deposit|deposit|balance|fund)', title[m.end():], re.I)][:6]


def identity_matches(offer, text):
    title = offer['title']
    if re.search(r'coupon codes|roundup|round-up|\by?mmv\b|\binvoices\b', title, re.I): return False
    if bool(re.search(r'\bbusiness\b', title, re.I)) and not re.search(r'\bbusiness checking\b', text, re.I): return False
    product = 'savings' if re.search(r'\bsavings\b', title, re.I) and not re.search(r'\bchecking\b', title, re.I) else 'checking'
    if not re.search(r'\b'+product+r'\b', text, re.I): return False
    deadline=re.search(r'\bends?\s+(\d{1,2})/(\d{1,2})/(\d{2,4})',title,re.I)
    if deadline:
        month,day,year=map(int,deadline.groups());year=year+2000 if year<100 else year
        dates={(int(m),int(d),int(y)+2000 if int(y)<100 else int(y)) for m,d,y in re.findall(r'\b(\d{1,2})/(\d{1,2})/(\d{2,4})\b',text)}
        if (month,day,year) not in dates:return False
    amounts = rewards(title)
    compact=text.replace(',', '')
    return bool(amounts) and all(any(re.search(r'bonus|reward|earn|get|statement credit',compact[max(0,m.start()-180):m.end()+180],re.I) for m in re.finditer(r'\$\s*'+re.escape(x)+r'\b',compact)) for x in amounts)


def interpret(offer, document):
    text = document['text']
    if not identity_matches(offer, text): return None
    expiry=re.search(r'\b(?:offer (?:expires|ends)|accounts? opened by|open[^.!?]{0,60}account by)\s+(\d{1,2}/\d{1,2}/\d{2,4})',text,re.I)
    if expiry:
        month,day,year=map(int,expiry.group(1).split('/'));year=year+2000 if year<100 else year
        try:
            ended=datetime(year,month,day,tzinfo=timezone.utc).date()<datetime.now(timezone.utc).date()
            if ended:return {'status':'expired','reason':'The official promotion opening deadline has passed.','rules':[],'conditions':[],'expirationQuote':evidence.normalized(expiry.group(0))}
        except ValueError:pass
    # Only explicit eligibility/address clauses, not branches or nationwide ATMs.
    clauses = [evidence.normalized(m.group(0)) for m in re.finditer(
        r'(?:[^.!?]{0,90}(?:offer (?:is )?(?:available|valid|open)|physical address|must (?:live|reside)|eligible residents|residents of)[^.!?]{0,1000}[.!?])', text, re.I)]
    rules = []
    for quote in clauses:
        codes = state_codes(quote)
        if not codes or len(quote) > 1200: continue
        # Nationwide text combined with restrictions is not silently generalized.
        if re.search(r'except|excluding|not available (?:in|to)', quote, re.I):
            mode = 'exclude'
            tail = re.split(r'except|excluding|not available (?:in|to)', quote, flags=re.I)[-1]
            codes = state_codes(tail)
        elif re.search(r'physical address (?:in|within)|(?:only|limited) (?:to|in|for)|must (?:live|reside)|residents of', quote, re.I):
            mode = 'include'
        else: continue
        if codes: rules.append({'mode': mode, 'states': codes, 'quote': quote})
    includes=[set(r['states']) for r in rules if r['mode']=='include']
    excludes=set().union(*(set(r['states']) for r in rules if r['mode']=='exclude')) if rules else set()
    if includes and not (set.intersection(*includes)-excludes):return {'status':'needs-review','reason':'Official geographic clauses conflict.','rules':[],'conditions':[]}
    if len(rules)>8:return {'status':'needs-review','reason':'Official scope requires a more detailed restriction review.','rules':[],'conditions':[]}
    national = next((evidence.normalized(m.group(0)) for m in re.finditer(
        r'[^.!?]{0,60}(?:offer (?:is )?(?:available|valid) nationwide|offer (?:is )?(?:available|valid) (?:in|to residents of) all 50 states)[^.!?]{0,180}[.!?]', text, re.I)
        if not re.search(r'except|excluding|not available', m.group(0), re.I)), '')
    conditions = []
    for m in re.finditer(r'[^.!?]{0,80}(?:membership (?:is )?required|must be a member|eligible (?:zip codes|counties)|(?:live|work|worship|attend school) (?:in|within)|invitation only|targeted offer|in[- ]branch only|enter your (?:home )?zip code)[^.!?]{0,350}[.!?]', text, re.I):
        quote = evidence.normalized(m.group(0))
        if quote not in conditions: conditions.append(quote)
    if national and rules: return {'status':'needs-review', 'reason':'Official geographic clauses conflict.', 'rules':[], 'conditions':conditions[:4]}
    if not rules and not national:
        return {'status':'conditional' if conditions else 'needs-review', 'reason':'Additional location or membership details are required.' if conditions else 'No explicit promotional geographic scope was found.', 'rules':[], 'conditions':conditions[:4]}
    return {'status':'verified', 'rules':rules[:8], 'nationwide':bool(national), 'nationwideQuote':national, 'conditions':conditions[:4],
            'reason':'Official geographic clauses checked; personal eligibility is not confirmed.'}


def verify_offer(offer):
    stamp = evidence.now()
    result = {'offerTitle':offer['title'], 'offerUrl':offer['url'], 'offerFingerprint':evidence.fingerprint(offer),
              'status':'needs-review', 'checkState':'unsupported', 'attemptedAt':stamp, 'rules':[], 'conditions':[],
              'reason':'Institution identity requires official-source onboarding.'}
    key = profile_for(offer['title'])
    if not key: return result
    result['institution'] = key
    checked = False
    try:
        try: urls = discover(offer, key)
        except Exception: urls = list(REGISTRY[key][2])
        for url in urls:
            try:
                doc = evidence.fetch_document('promotion', url)
                if urlsplit(doc['url']).hostname not in REGISTRY[key][1]: continue
                checked = True
                parsed = interpret(offer, doc)
                if parsed is None: continue
                result.update(parsed, checkState='checked', checkedAt=doc['checkedAt'], expiresAt=(datetime.fromisoformat(doc['checkedAt'])+MAX_AGE).isoformat(),
                              sourceUrl=doc['url'], sourceHash=doc['hash'])
                if parsed['status'] in ('verified','conditional','expired'): return result
            except Exception: continue
        if result.get('checkedAt'): return result
        result.update(checkState='checked' if checked else 'failed', reason='Official pages did not establish this exact promotion.' if checked else 'Official promotion could not be reached; no eligibility is assumed.')
    except Exception:
        result.update(checkState='failed', reason='Official-source discovery failed; no previous evidence is marked current.')
    return result


def build_catalog():
    global _catalog_urls
    raw, _, _ = evidence.fetch_bytes(evidence.CATALOG_URL)
    feed = json.loads(raw)
    if feed.get('schemaVersion') not in (1,2) or not isinstance(feed.get('offers'),list): raise ValueError('Invalid catalog')
    offers = [o for o in feed['offers'][:200] if isinstance(o,dict) and isinstance(o.get('title'),str) and len(o['title'])<=300 and isinstance(o.get('url'),str) and approved(o['url'],PUBLISHERS)]
    with _lock:
        _catalog_urls={o['url'] for o in offers}
        priorities = list(_priority_urls)
        old = _result.get('records', {})
    offers.sort(key=lambda o: (priorities.index(o['url']) if o['url'] in priorities else len(priorities), str(old.get(o['url'],{}).get('attemptedAt','')), o['url']))
    # Fair bounded batches: exact unattempted offers follow the user's priority matches.
    chosen = offers[:40]
    with ThreadPoolExecutor(max_workers=6) as pool:
        checked = list(pool.map(verify_offer, chosen))
    records = {o['url']:r for o,r in zip(chosen,checked)}
    for offer in offers[40:]:
        prior = old.get(offer['url'])
        if prior and prior.get('offerFingerprint')==evidence.fingerprint(offer): records[offer['url']]=prior
        else: records[offer['url']]={'offerTitle':offer['title'],'offerUrl':offer['url'],'offerFingerprint':evidence.fingerprint(offer),'status':'needs-review','checkState':'queued','reason':'Official check is queued.','rules':[],'conditions':[]}
    return {'schemaVersion':1,'status':'complete','checkedAt':evidence.now(),'records':records,
            'coverage':{'total':len(offers),'checked':sum(r.get('checkState')=='checked' for r in records.values()),'verified':sum(r.get('status')=='verified' for r in records.values()),'failed':sum(r.get('checkState')=='failed' for r in records.values()),'unsupported':sum(r.get('checkState')=='unsupported' for r in records.values()),'queued':sum(r.get('checkState')=='queued' for r in records.values())}}


def _run():
    global _result
    try:
        result = build_catalog()
        log.info('bank_availability_check_complete total=%s checked=%s verified=%s',result['coverage']['total'],result['coverage']['checked'],result['coverage']['verified'])
    except Exception:
        result = {'schemaVersion':1,'status':'unavailable','checkedAt':evidence.now(),'records':{}}
        log.warning('bank_availability_check_failed; no current eligibility assumed')
    with _lock: _result = result


def start_if_due():
    global _task, _last_started
    with _lock:
        if _task is None or (_task.done() and time.monotonic()-_last_started>=3600):
            _last_started=time.monotonic()
            _task=asyncio.create_task(asyncio.to_thread(_run))


@router.get('/public/bank-bonus-availability')
async def public_availability():
    start_if_due()
    with _lock:
        result=json.loads(json.dumps(_result))
        if _task and not _task.done(): result['status']='pending'
    return result


@router.post('/api/bank-bonus-availability/priorities')
async def prioritize_request(request: Request):
    global _priority_urls, _last_priority_at, _last_started
    if not auth_required(): raise HTTPException(503,'Secure priority verification is not configured.')
    try: authorize_header(request.headers.get('Authorization'))
    except AuthError as exc: raise HTTPException(exc.status_code,exc.detail)
    raw=await request.body()
    if len(raw)>4096: raise HTTPException(413,'Priority request is too large.')
    try: body=json.loads(raw)
    except (ValueError,UnicodeError): raise HTTPException(400,'Invalid priority request.')
    urls=body.get('offers') if isinstance(body,dict) and set(body)=={'offers'} else None
    if not isinstance(urls,list) or len(urls)>8 or any(not isinstance(url,str) or not approved(url,PUBLISHERS) for url in urls): raise HTTPException(400,'Only public catalog offer identifiers are accepted.')
    with _lock:
        known=set(_catalog_urls)
        if any(url not in known for url in urls): raise HTTPException(409,'Offer is not in the current verified catalog yet.')
        changed=urls!=_priority_urls
        if changed:
            _priority_urls=list(dict.fromkeys(urls))
            if time.monotonic()-_last_priority_at>=300:
                _last_priority_at=time.monotonic();_last_started=0
    start_if_due()
    return {'accepted':True,'count':len(urls),'status':'queued' if changed else 'unchanged'}


async def scheduled_availability_loop():
    await asyncio.sleep(30)
    while True:
        if auth_required(): start_if_due()
        await asyncio.sleep(300)
