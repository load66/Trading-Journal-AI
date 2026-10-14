import asyncio
import hashlib
import json
import sys
from datetime import datetime,timezone
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import bank_availability as a

def offer(title='Truist $500 Checking Bonus'):
    return {'title':title,'url':'https://www.doctorofcredit.com/truist-bonus/'}
def doc(text):
    return {'text':text,'url':'https://www.truist.com/checking/lp/promos/checking-promo4','hash':hashlib.sha256(text.encode()).hexdigest(),'checkedAt':datetime.now(timezone.utc).isoformat()}
def test_explicit_residency_and_no_atm_inference():
    r=a.interpret(offer(),doc('Earn a $500 checking bonus. The client must have a physical address in: AL, AR, FL, GA, IN, KY, MD, MS, NC, NJ, OH, PA, SC, TN, TX, VA, WV or DC.'))
    assert r['status']=='verified' and 'MO' not in r['rules'][0]['states'] and 'VA' in r['rules'][0]['states']
    r=a.interpret(offer(),doc('Earn a $500 checking bonus. Use 50,000 ATMs nationwide.'))
    assert r['status']=='needs-review' and not r.get('nationwide')
def test_explicit_national_and_conditions():
    r=a.interpret(offer(),doc('Earn a $500 checking bonus. This offer is available nationwide.'))
    assert r['status']=='verified' and r['nationwide'] and r['nationwideQuote']
    r=a.interpret(offer(),doc('Earn a $500 checking bonus. Membership is required. You must live or work in selected counties.'))
    assert r['status']=='conditional' and r['conditions']
def test_scope_conflict_and_expiration():
    r=a.interpret(offer(),doc('Earn a $500 checking bonus. This offer is available nationwide. The client must have a physical address in MO only.'))
    assert r['status']=='needs-review'
    r=a.interpret(offer(),doc('Earn a $500 checking bonus. This offer ends 1/1/2020.'))
    assert r['status']=='expired' and r['expirationQuote']
def test_exact_reward_and_product_required():
    assert a.interpret(offer(),doc('Earn a $300 checking bonus. Offer is available nationwide.')) is None
    assert a.interpret(offer(),doc('Maintain $500 in checking. The monthly fee is $10.')) is None
    assert a.interpret(offer('Truist $500 Savings Bonus'),doc('Earn a $500 checking bonus.')) is None
    assert a.interpret(offer('Truist Business $500 Checking Bonus'),doc('Earn a $500 personal checking bonus.')) is None
    assert a.interpret(offer('Truist Coupon Codes $500 Checking Bonuses'),doc('Earn a $500 checking bonus.')) is None
@pytest.mark.parametrize('url',['https://127.0.0.1/x','https://www.truist.com.evil.test/x','http://www.truist.com/x','https://u:p@www.truist.com/x','https://www.truist.com:444/x','https://www.truist.com/'])
def test_host_boundaries(url):
    assert not a.approved(url,{'www.truist.com'})
def test_discovery_limits_and_scopes(monkeypatch):
    html='<a href="https://www.truist.com/checking/promo">Direct offer</a><a href="https://www.chase.com/bonus">Another bank</a><a href="https://127.0.0.1/private">Offer</a><a href="https://www.truist.com/login">Login</a>'
    monkeypatch.setattr(a.evidence,'fetch_bytes',lambda u:(html.encode(),'text/html',u))
    urls=a.discover(offer(),'truist')
    assert 'https://www.truist.com/checking/promo' in urls and len(urls)<=3
    assert not any('chase' in u or '/login' in u for u in urls)
def test_failed_new_check_does_not_reuse_old_evidence(monkeypatch):
    monkeypatch.setattr(a,'discover',lambda *x:(_ for _ in ()).throw(ValueError()))
    monkeypatch.setattr(a.evidence,'fetch_document',lambda *x:(_ for _ in ()).throw(ValueError()))
    r=a.verify_offer(offer())
    assert r['checkState']=='failed' and 'checkedAt' not in r and not r['rules']
def test_verify_receipt_has_original_quotes_hash_and_identity(monkeypatch):
    text='Earn a $500 checking bonus. This offer is available nationwide.'
    monkeypatch.setattr(a,'discover',lambda *x:['https://www.truist.com/checking/promo'])
    monkeypatch.setattr(a.evidence,'fetch_document',lambda *x:doc(text))
    r=a.verify_offer(offer())
    assert r['status']=='verified' and r['sourceHash']==hashlib.sha256(text.encode()).hexdigest()
    assert r['nationwideQuote'] in text and r['offerFingerprint']==a.evidence.fingerprint(offer()) and r['expiresAt']>r['checkedAt']
def test_priority_order_and_all_offer_coverage(monkeypatch):
    offers=[offer('Truist $'+str(500+i)+' Checking Bonus')|{'url':f'https://www.doctorofcredit.com/offer-{i}/'} for i in range(43)]
    monkeypatch.setattr(a.evidence,'fetch_bytes',lambda u:(json.dumps({'schemaVersion':2,'offers':offers}).encode(),'application/json',u))
    calls=[]
    def verify(o):
        calls.append(o['url']);return {'offerFingerprint':a.evidence.fingerprint(o),'status':'needs-review','checkState':'checked','attemptedAt':'2026-10-14'}
    monkeypatch.setattr(a,'verify_offer',verify);monkeypatch.setattr(a,'_priority_urls',[offers[-1]['url']]);monkeypatch.setattr(a,'_result',{'records':{}})
    r=a.build_catalog()
    assert calls[0]==offers[-1]['url'] and len(calls)==40 and len(r['records'])==43 and r['coverage']['queued']==3

def test_different_promotion_deadline_cannot_borrow_geography():
    assert a.interpret(offer('Truist $500 Checking Bonus – Ends 2/2/27'),doc('Earn a $500 checking bonus. Offer is available nationwide. Open by 3/1/27.')) is None
    assert a.interpret(offer('Truist $500 Checking Bonus – Ends 2/2/27'),doc('Earn a $500 checking bonus. Offer is available nationwide. Open by 2/2/27.'))['status']=='verified'

class PriorityRequest:
    headers={'Authorization':'Bearer test'}
    def __init__(self,body):self.raw=json.dumps(body).encode()
    async def body(self):return self.raw

def test_priority_route_rejects_private_fields_and_unknown_offers(monkeypatch):
    monkeypatch.setattr(a,'auth_required',lambda:True);monkeypatch.setattr(a,'authorize_header',lambda *_:{'sub':'test'})
    monkeypatch.setattr(a,'_result',{'records':{offer()['url']:{}}});monkeypatch.setattr(a,'_catalog_urls',{offer()['url']});monkeypatch.setattr(a,'_priority_urls',[])
    with pytest.raises(a.HTTPException) as error:asyncio.run(a.prioritize_request(PriorityRequest({'offers':[offer()['url']],'accountIds':['private']})))
    assert error.value.status_code==400 and not a._priority_urls
    with pytest.raises(a.HTTPException) as error:asyncio.run(a.prioritize_request(PriorityRequest({'offers':['https://www.doctorofcredit.com/not-current/']})))
    assert error.value.status_code==409 and not a._priority_urls

def test_priority_route_requires_existing_owner_auth(monkeypatch):
    monkeypatch.setattr(a,'auth_required',lambda:False)
    with pytest.raises(a.HTTPException) as error:asyncio.run(a.prioritize_request(PriorityRequest({'offers':[]})))
    assert error.value.status_code==503
    monkeypatch.setattr(a,'auth_required',lambda:True)
    monkeypatch.setattr(a,'authorize_header',lambda *_:(_ for _ in ()).throw(a.AuthError(401,'Authentication required')))
    with pytest.raises(a.HTTPException) as error:asyncio.run(a.prioritize_request(PriorityRequest({'offers':[]})))
    assert error.value.status_code==401

def test_priority_route_accepts_only_bounded_catalog_identifiers(monkeypatch):
    monkeypatch.setattr(a,'auth_required',lambda:True);monkeypatch.setattr(a,'authorize_header',lambda *_:{'sub':'test'})
    monkeypatch.setattr(a,'_result',{'records':{offer()['url']:{}}});monkeypatch.setattr(a,'_catalog_urls',{offer()['url']});monkeypatch.setattr(a,'_priority_urls',[])
    monkeypatch.setattr(a,'_last_priority_at',0);monkeypatch.setattr(a,'start_if_due',lambda:None)
    r=asyncio.run(a.prioritize_request(PriorityRequest({'offers':[offer()['url']]})))
    assert r['accepted'] and a._priority_urls==[offer()['url']]
    assert asyncio.run(a.prioritize_request(PriorityRequest({'offers':[offer()['url']]})))['status']=='unchanged'

def test_public_endpoint_shares_one_job_and_contains_no_user_data(monkeypatch):
    async def run():
        monkeypatch.setattr(a,'_task',None);monkeypatch.setattr(a,'_last_started',0)
        monkeypatch.setattr(a,'_result',{'schemaVersion':1,'status':'pending','records':{}})
        def work():
            with a._lock:a._result={'schemaVersion':1,'status':'complete','records':{}}
        monkeypatch.setattr(a,'_run',work)
        first=await a.public_availability();task=a._task;await a.public_availability();assert a._task is task
        await task;result=await a.public_availability();assert result['status']=='complete' and a._task is task
    asyncio.run(run())

def test_state_codes_do_not_confuse_uppercase_conjunctions():
    assert a.state_codes('Residents of Missouri OR Kansas only.')==['KS','MO']
    assert a.state_codes('The client must have a physical address in: MO, OR, IN or DC.')==['DC','IN','MO','OR']
    assert a.state_codes('The offer is available IN the United States.')==[]

def test_incompatible_geographic_clauses_never_exclude_every_user():
    r=a.interpret(offer(),doc('Earn a $500 checking bonus. This offer is valid only for residents of Missouri. This offer is valid only for residents of California.'))
    assert r['status']=='needs-review' and not r['rules']
