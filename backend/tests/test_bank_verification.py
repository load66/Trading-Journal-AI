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
    assert b.profile_for('US Bank personal checking bonus') is None
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
