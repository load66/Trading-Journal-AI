import asyncio
import hashlib
import sys
from pathlib import Path
import pytest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import bank_verification as b

def docs():
    return [{'role':'promotion','url':'https://accountoffers.wellsfargo.com/checkingoffer/','hash':'p','checkedAt':'2026-10-13T11:00:00+00:00','text':'Everyday Checking. Receive a $325 bonus. Complete qualifying deposits within 90 calendar days.'},
            {'role':'fees','url':'https://www.wellsfargo.com/fees/','hash':'f','checkedAt':'2026-10-13T11:00:01+00:00','text':'Everyday Checking monthly service fee is $15. No early closure fee is stated here.'}]

def payload():
    return {'fields':{'bonus':{'value':'$325','quote':'Receive a $325 bonus.','source':'promotion'},'monthly_fee':{'value':'$15','quote':'Everyday Checking monthly service fee is $15.','source':'fees'}}}

def test_quotes_must_match_fetched_source():
    accepted,rejected=b.validate_fields(payload(),docs())
    assert set(accepted)=={'bonus','monthly_fee'} and not rejected
    assert accepted['bonus']['sourceHash']=='p'
    fake=payload();fake['fields']['bonus']['quote']='Receive a $999 bonus.'
    assert 'bonus' in b.validate_fields(fake,docs())[1]
    fake=payload();fake['fields']['bonus']['value']='Guaranteed approval'
    assert 'bonus' in b.validate_fields(fake,docs())[1]
    fake=payload();fake['fields']['bonus']['value']=325
    assert 'bonus' in b.validate_fields(fake,docs())[1]

def test_fees_cannot_be_sourced_from_promo_summary():
    fake=payload();fake['fields']['monthly_fee']={'value':'$325','quote':'Receive a $325 bonus.','source':'promotion'}
    assert 'monthly_fee' in b.validate_fields(fake,docs())[1]

def test_unsupported_schema_and_missing_evidence():
    with pytest.raises(ValueError):b.validate_fields([],docs())
    accepted,rejected=b.validate_fields({'fields':{'eligibility':'yes','payout':None}},docs())
    assert not accepted and rejected==['eligibility']

@pytest.mark.parametrize('url',['http://www.usbank.com/a','https://localhost/a','https://127.0.0.1/a','https://www.usbank.com.evil.test/a','https://user@www.usbank.com/a','https://www.usbank.com:444/a','https://www.usbank.com:bad/a','file:///etc/passwd'])
def test_private_or_unapproved_urls_rejected(url):
    assert not b.safe_url(url)

def test_supported_product_scope():
    assert b.profile_for('Wells Fargo $325 Checking Bonus')=='wells-consumer'
    assert b.profile_for('Wells Fargo Business $825 Checking Bonus') is None
    assert b.profile_for('U.S. Bank Business Checking Bonus')=='us-business'
    assert b.profile_for('US Bank personal checking bonus')=='us-consumer'
    assert b.verify_offer({'title':'Unknown Bank $300 Bonus','url':'https://source.test/'})['status']=='needs-review'

def test_success_and_changed_sources(monkeypatch):
    monkeypatch.setattr(b,'fetch_document',lambda role,url:next(d for d in docs() if d['role']==role))
    monkeypatch.setattr(b,'extract_ai',lambda *args:(payload(),'groq'))
    offer={'title':'Wells Fargo $325 Checking Bonus','url':'https://source.test/'}
    record=b.verify_offer(offer)
    assert record['status']=='evidence-found' and record['officialCheckedAt'] and record['aiCheckedAt']
    assert 'early_close' in record['missingFields']
    assert 'verified eligibility' in record['reason']
    other=b.verify_offer({'title':'Wells Fargo $125 Checking Bonus','url':'https://source.test/'})
    assert other['status']=='needs-review' and 'different offer' in other['reason']
    def no_ai(*args):raise AssertionError('unchanged evidence must not spend another AI call')
    monkeypatch.setattr(b,'extract_ai',no_ai)
    cached=b.verify_offer(offer,record)
    assert cached['fields']==record['fields'] and cached['aiCheckedAt']==record['aiCheckedAt']

def test_outage_does_not_present_old_evidence_as_current(monkeypatch):
    def failed(*args):raise ValueError('blocked bank page')
    monkeypatch.setattr(b,'fetch_document',failed)
    record=b.verify_offer({'title':'Wells Fargo $325 Checking Bonus','url':'https://source.test/'},{'fields':{'bonus':{'value':'$999'}}})
    assert record['status']=='needs-review' and not record['fields'] and not record['sources']

def test_html_scripts_and_styles_are_not_model_input():
    parser=b.VisibleText();parser.feed('<p>Bank terms</p><script>steal()</script><style>bad</style><p>End</p>')
    assert b.normalized(' '.join(parser.parts))=='Bank terms End'

def test_read_endpoint_has_no_parameters_and_one_job(monkeypatch):
    async def exercise():
        b._task=None;b._last_started=0
        def run():
            with b._lock:b._result={'schemaVersion':1,'status':'complete','records':{}}
        monkeypatch.setattr(b,'_run',run)
        first=await b.public_bank_bonus_verification()
        task=b._task
        second=await b.public_bank_bonus_verification()
        assert first['status']=='pending' and b._task is task
        await task
        third=await b.public_bank_bonus_verification()
        assert third['status']=='complete' and b._task is task
    asyncio.run(exercise())

def test_cycle_budget_and_document_reuse(monkeypatch):
    seen=[]
    monkeypatch.setattr(b,'fetch_document',lambda role,url:next(d for d in docs() if d['role']==role))
    def ai(offer,*args):seen.append(offer['title']);return payload(),'groq'
    monkeypatch.setattr(b,'extract_ai',ai)
    state={'budget':1,'documents':{},'extractions':{}}
    first={'title':'Wells Fargo $325 Checking Bonus','url':'https://source.test/one'}
    one=b.verify_offer(first,run_state=state)
    two=b.verify_offer({**first,'url':'https://source.test/two'},run_state=state)
    assert one['fields']==two['fields'] and len(seen)==1 and len(state['documents'])==2
    blocked=b.verify_offer({'title':'Wells Fargo $125 Checking Bonus','url':'https://source.test/three'},run_state=state)
    assert blocked['status']=='needs-review' and not blocked['fields'] and 'budget' in blocked['reason']
    assert b.profile_for('Wells Fargo $500 savings bonus') is None
    assert b.profile_for('US Bank Business Savings Bonus') is None

def test_provider_packet_contains_only_public_evidence(monkeypatch):
    import json
    seen={}
    monkeypatch.setenv('GROQ_API_KEY','synthetic-provider-key')
    monkeypatch.delenv('ANTHROPIC_API_KEY',raising=False)
    monkeypatch.setenv('GROQ_MODEL','openai/gpt-oss-120b')
    class Response:
        def raise_for_status(self):pass
        def json(self):return {'choices':[{'finish_reason':'stop','message':{'content':json.dumps(payload())}}]}
    def post(url,**kwargs):seen.update(url=url,packet=kwargs['json']);return Response()
    monkeypatch.setattr(b.httpx,'post',post)
    result,provider=b.extract_ai({'title':'Wells Fargo $325 Checking Bonus'},b.PROFILES['wells-consumer'],docs())
    assert provider=='groq' and result==payload()
    assert seen['packet']['reasoning_effort']=='low'
    user=json.loads(seen['packet']['messages'][1]['content'])
    assert set(user)=={'offerTitle','product','documents'}
    assert 'synthetic-provider-key' not in json.dumps(user)

@pytest.fixture
def provider(monkeypatch):
    import json
    monkeypatch.setenv('GROQ_API_KEY','synthetic-provider-key')
    monkeypatch.delenv('ANTHROPIC_API_KEY',raising=False)
    waits=[];calls=[]
    monkeypatch.setattr(b.time,'sleep',waits.append)
    class Response:
        def __init__(self,status=200,delay=None):
            self.status_code=status
            self.headers={'retry-after':str(delay)} if delay is not None else {'x-ratelimit-remaining-tokens':'7000'}
        def raise_for_status(self):
            if self.status_code>=400:raise ValueError('HTTP error')
        def json(self):return {'usage':{'total_tokens':123},'choices':[{'finish_reason':'stop','message':{'content':json.dumps(payload())}}]}
    sequence=[]
    def post(*args,**kwargs):
        calls.append(kwargs['json']);return sequence.pop(0)
    monkeypatch.setattr(b.httpx,'post',post)
    return Response,sequence,calls,waits

@pytest.mark.parametrize('code',[429,503])
def test_short_provider_failure_retries_once(provider,code):
    Response,sequence,calls,waits=provider
    sequence.extend([Response(code,2),Response()])
    state={'metrics':{},'dailyLimit':100000}
    result,provider_name=b.extract_ai({'title':'Wells Fargo $325 Checking Bonus'},b.PROFILES['wells-consumer'],docs(),state)
    assert result==payload() and provider_name=='groq' and len(calls)==2 and waits==[2]
    assert state['metrics']['retries']==1 and state['metrics']['reportedTokens']==123
    assert state['metrics']['remainingTokens']==7000
    assert state['dailyUsed']>123, 'failed calls must retain conservative budget reservations'

def test_long_rate_limit_queues_and_stops_other_provider_calls(provider):
    Response,sequence,calls,waits=provider
    sequence.append(Response(429,120))
    state={'metrics':{},'dailyLimit':100000}
    for _ in range(2):
        with pytest.raises(b.AIUnavailable) as exc:b.extract_ai({'title':'Bank checking'},b.PROFILES['wells-consumer'],docs(),state)
        assert exc.value.code=='rate-limited'
    assert len(calls)==1 and not waits and state['metrics']['throttled']==1

def test_daily_budget_never_calls_provider(provider):
    _,_,calls,_=provider
    with pytest.raises(b.AIUnavailable) as exc:b.extract_ai({'title':'Bank checking'},b.PROFILES['wells-consumer'],docs(),{'dailyUsed':999,'dailyLimit':1000})
    assert exc.value.code=='daily-budget' and not calls

def test_provider_retry_does_not_loop(provider):
    Response,sequence,calls,waits=provider
    sequence.extend([Response(503,1),Response(503,1)])
    with pytest.raises(b.AIUnavailable):b.extract_ai({'title':'Bank checking'},b.PROFILES['wells-consumer'],docs())
    assert len(calls)==2 and waits==[1]

def test_empty_and_incomplete_cache_rechecks_after_a_day(monkeypatch):
    monkeypatch.setattr(b,'fetch_document',lambda role,url:next(d for d in docs() if d['role']==role))
    calls=[]
    monkeypatch.setattr(b,'extract_ai',lambda *args:(calls.append(1) or payload(),'groq'))
    offer={'title':'Wells Fargo $325 Checking Bonus','url':'https://source.test/'}
    first=b.verify_offer(offer)
    assert first['extractionVersion']==b.EXTRACTION_VERSION
    cached=b.verify_offer(offer,first)
    assert cached['checkState']=='cached' and len(calls)==1
    first['aiCheckedAt']='2020-01-01T00:00:00+00:00'
    assert b.verify_offer(offer,first)['checkState']=='extracted' and len(calls)==2

def test_change_comparison_requires_same_offer_identity():
    offer={'title':'Wells Fargo $325 Checking Bonus','url':'https://source.test/'}
    record={'offerFingerprint':b.fingerprint(offer),'fields':{'monthly_fee':{'value':'$15'}}}
    old={'offerFingerprint':record['offerFingerprint'],'fields':{'monthly_fee':{'value':'$10'},'early_close':{'value':'$25'}}}
    changes=b.evidence_changes(old,record)
    assert {x['field']:x['kind'] for x in changes}=={'monthly_fee':'changed','early_close':'removed'}
    old['offerFingerprint']='different'
    assert not b.evidence_changes(old,record)

def test_queue_order_and_daily_accounting_survive_publication(monkeypatch):
    import json
    feed={'schemaVersion':2,'offers':[{'title':'Wells Fargo $325 Checking Bonus','url':'https://source.test/old'},{'title':'Wells Fargo $125 Checking Bonus','url':'https://source.test/new'}],
          'verification':{'records':{'https://source.test/old':{'lastAttemptAt':b.now()}},'usage':{'day':b.datetime.now(b.timezone.utc).date().isoformat(),'dailyAccountedTokens':123}}}
    monkeypatch.setattr(b,'_last_complete',None)
    monkeypatch.setenv('BANK_AI_CYCLE_LIMIT','1')
    monkeypatch.setattr(b,'fetch_bytes',lambda *args:(json.dumps(feed).encode(),'application/json',b.CATALOG_URL))
    monkeypatch.setattr(b,'fetch_document',lambda role,url:next(d for d in docs() if d['role']==role))
    calls=[]
    monkeypatch.setattr(b,'extract_ai',lambda offer,*args:(calls.append(offer['url']) or payload(),'groq'))
    catalog=b.build_catalog()
    assert calls==['https://source.test/new']
    assert catalog['usage']['dailyAccountedTokens']==123
    assert catalog['usage']['queued']==1 and catalog['usage']['newExtractions']==1
    assert len(catalog['coverage'])==4

def test_failed_document_is_fetched_only_once_in_a_cycle(monkeypatch):
    calls=[]
    def fail(*args):calls.append(1);raise ValueError('unavailable')
    monkeypatch.setattr(b,'fetch_document',fail)
    state={'budget':4,'documents':{},'metrics':{}}
    for title in ['Wells Fargo $325 Checking Bonus','Wells Fargo $125 Checking Bonus']:
        r=b.verify_offer({'title':title,'url':'https://source.test/'},run_state=state)
        assert r['checkState']=='failed' and not r['fields']
    assert len(calls)==1 and state['metrics']['documentFailures']==1

def test_new_profiles_do_not_cross_product_boundaries():
    assert b.profile_for('Chase $900 Checking Bonus')=='chase-consumer'
    assert b.profile_for('Chase Total Checking $400 Bonus')=='chase-consumer'
    for title in ['Chase Business Checking Bonus','Chase Private Client Checking Bonus','Chase Secure Checking Bonus','Chase Bank Coupon Codes: $400 Checking Bonuses']:
        assert b.profile_for(title) is None
    assert b.profile_for('U.S. Bank $450 Checking Bonus')=='us-consumer'
    assert b.profile_for('U.S. Bank Business Checking Bonus')=='us-business'

def test_product_excerpt_includes_late_fee_section():
    text='Generic navigation. '*1000+'Everyday Checking monthly service fee $15. '+'Unrelated savings. '*1000
    excerpt=b.model_excerpt({'text':text},'Everyday Checking')
    assert 'Everyday Checking monthly service fee $15.' in excerpt
    assert len(excerpt)<=9000

def test_bonus_keep_open_wording_can_come_from_promotion():
    d=docs();d[0]['text']+=' Keep account open until bonus posts.'
    accepted,rejected=b.validate_fields({'fields':{'close_restrictions':{'value':'Keep account open until bonus posts.','quote':'Keep account open until bonus posts.','source':'promotion'}}},d)
    assert accepted and not rejected


def test_cycle_budget_reduces_after_observed_throttling(monkeypatch):
    import json
    feed={'schemaVersion':2,'offers':[],'verification':{'usage':{'throttled':1,'day':'2020-01-01','dailyAccountedTokens':90000}}}
    monkeypatch.setattr(b,'_last_complete',None)
    monkeypatch.delenv('BANK_AI_CYCLE_LIMIT',raising=False)
    monkeypatch.setattr(b,'fetch_bytes',lambda *args:(json.dumps(feed).encode(),'application/json',b.CATALOG_URL))
    usage=b.build_catalog()['usage']
    assert usage['cycleLimit']==2 and usage['dailyAccountedTokens']==0
