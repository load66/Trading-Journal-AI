import asyncio
import json
import sys
from pathlib import Path
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import bonus_questions as b
from auth import AuthError

@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(b, 'auth_required', lambda: True)
    monkeypatch.setattr(b, 'authorize_header', lambda value: {} if value == 'Bearer owner' else (_ for _ in ()).throw(AuthError(401, 'Authentication required')))
    monkeypatch.setattr(b, '_lock', asyncio.Lock())
    monkeypatch.setattr(b, '_cache', {})
    monkeypatch.setattr(b, '_usage_day', '')
    monkeypatch.setattr(b, '_calls', 0)
    monkeypatch.setattr(b, '_anonymous_calls', 0)
    monkeypatch.setattr(b, '_last_call', 0)
    app = FastAPI();app.include_router(b.router)
    return TestClient(app)

def packet():
    return {'question':'What remains?', 'facts':[{'id':'terms','label':'Saved terms','text':'Receive a $325 bonus after 90 days.'},{'id':'close_gate','label':'Tracker closure checks','text':'Bonus not received. Close date not confirmed.'}]}

def test_auth_required_before_input_or_ai(client, monkeypatch):
    calls=[]
    async def fake(p):calls.append(p);return {}
    monkeypatch.setattr(b, 'provider', fake)
    assert client.post('/api/bonus-tracker/ask',json=packet()).status_code==401
    assert not calls

def test_local_auth_disabled_fails_closed(client, monkeypatch):
    monkeypatch.setattr(b,'auth_required',lambda:False)
    assert client.post('/api/bonus-tracker/ask',json=packet(),headers={'Authorization':'Bearer owner'}).status_code==503

def test_owner_and_identical_context_cache(client,monkeypatch):
    calls=[]
    async def fake(p):calls.append(p);return {'answer':'The bonus is not received.','factIds':['close_gate'],'missing':[]}
    monkeypatch.setattr(b,'provider',fake)
    first=client.post('/api/bonus-tracker/ask',json=packet(),headers={'Authorization':'Bearer owner'})
    second=client.post('/api/bonus-tracker/ask',json=packet(),headers={'Authorization':'Bearer owner'})
    assert first.status_code==second.status_code==200
    assert first.json()['cached'] is False and second.json()['cached'] is True
    assert len(calls)==1 and 'facts' not in first.json()
    assert client.post('/api/bonus-tracker/ask',json=packet()).status_code==401

def test_changed_facts_invalidate_answer(client,monkeypatch):
    async def fake(p):return {'answer':'Check saved facts.','factIds':['terms'],'missing':[]}
    monkeypatch.setattr(b,'provider',fake)
    first=client.post('/api/bonus-tracker/ask',json=packet(),headers={'Authorization':'Bearer owner'})
    monkeypatch.setattr(b,'_last_call',0)
    p=packet();p['facts'][0]['text']='Updated saved terms.'
    assert client.post('/api/bonus-tracker/ask',json=p,headers={'Authorization':'Bearer owner'}).json()['cached'] is False

def test_invalid_packet_excludes_extra_account_data():
    p=packet();p['notes']='private';
    with pytest.raises(ValueError):b.validate_packet(p)
    p=packet();p['facts'].append(p['facts'][0])
    with pytest.raises(ValueError):b.validate_packet(p)
    p=packet();p['question']='x'*501
    with pytest.raises(ValueError):b.validate_packet(p)
    p=packet();p['facts'][0]['text']='x'*12001
    with pytest.raises(ValueError):b.validate_packet(p)

def test_answer_grounding():
    good={'answer':'Saved terms mention $325 after 90 days.','factIds':['terms'],'missing':[]}
    assert b.validate_answer(good,packet()['facts'])==good
    assert b.validate_answer({**good,'answer':'Saved bonus: $325.'},packet()['facts'])['answer']=='Saved bonus: $325.'
    with pytest.raises(ValueError):b.validate_answer({**good,'answer':'You will receive $999.'},packet()['facts'])
    with pytest.raises(ValueError):b.validate_answer({**good,'factIds':['madeup']},packet()['facts'])
    with pytest.raises(ValueError):b.validate_answer({**good,'factIds':[]},packet()['facts'])
    with pytest.raises(ValueError):b.validate_answer({**good,'missing':'unknown'},packet()['facts'])

def test_daily_limit_and_failures_count(client,monkeypatch):
    monkeypatch.setenv('BONUS_QUESTION_DAILY_LIMIT','1')
    async def fake(p):raise ValueError('secret provider detail')
    monkeypatch.setattr(b,'provider',fake)
    r=client.post('/api/bonus-tracker/ask',json=packet(),headers={'Authorization':'Bearer owner'})
    assert r.status_code==503 and 'secret' not in r.text
    monkeypatch.setattr(b,'_last_call',0)
    assert client.post('/api/bonus-tracker/ask',json=packet(),headers={'Authorization':'Bearer owner'}).status_code==429

def test_payload_bound(client):
    assert client.post('/api/bonus-tracker/ask',content='x'*65001,headers={'Authorization':'Bearer owner'}).status_code==400


def test_no_journal_or_url_fetch_contract():
    source=Path(b.__file__).read_text()
    assert 'get_db' not in source and 'DATABASE_URL' not in source and 'diary' not in source
    assert "'https://api.groq.com/openai/v1/chat/completions'" in source


PUBLIC_HEADERS = {'Origin': 'https://load66.github.io'}


def test_public_questions_need_no_signin_and_cache(client, monkeypatch):
    calls=[]
    async def fake(p):
        calls.append(p)
        return {'answer':'Check the saved closure checks.','factIds':['close_gate'],'missing':[]}
    monkeypatch.setattr(b,'provider',fake)
    first=client.post('/public/bonus-tracker/ask',json=packet(),headers=PUBLIC_HEADERS)
    second=client.post('/public/bonus-tracker/ask',json=packet(),headers=PUBLIC_HEADERS)
    assert first.status_code==second.status_code==200
    assert first.json()['cached'] is False and second.json()['cached'] is True
    assert len(calls)==1 and b._anonymous_calls==1
    assert client.post('/api/bonus-tracker/ask',json=packet(),headers=PUBLIC_HEADERS).status_code==401


def test_public_origin_and_input_gates_precede_provider(client,monkeypatch):
    calls=[]
    async def fake(p):calls.append(p);return {}
    monkeypatch.setattr(b,'provider',fake)
    for headers in [{},{'Origin':'https://other.example'},{'Origin':'null'}]:
        assert client.post('/public/bonus-tracker/ask',json=packet(),headers=headers).status_code==403
    assert client.post('/public/bonus-tracker/ask',content='{}',headers=PUBLIC_HEADERS).status_code==415
    p=packet();p['notes']='private'
    assert client.post('/public/bonus-tracker/ask',json=p,headers=PUBLIC_HEADERS).status_code==400
    assert not calls


def test_public_attempt_cap_includes_failures(client,monkeypatch):
    monkeypatch.setattr(b,'_usage_day',b.datetime.now(b.timezone.utc).date().isoformat())
    monkeypatch.setattr(b,'_anonymous_calls',19)
    calls=[]
    async def fake(p):calls.append(p);raise ValueError('provider secret')
    monkeypatch.setattr(b,'provider',fake)
    assert client.post('/public/bonus-tracker/ask',json=packet(),headers=PUBLIC_HEADERS).status_code==503
    monkeypatch.setattr(b,'_last_call',0)
    denied=client.post('/public/bonus-tracker/ask',json=packet(),headers=PUBLIC_HEADERS)
    assert denied.status_code==429 and len(calls)==1 and b._anonymous_calls==20
    assert 'provider secret' not in denied.text


def test_public_cannot_bypass_shared_budget(client,monkeypatch):
    monkeypatch.setattr(b,'_usage_day',b.datetime.now(b.timezone.utc).date().isoformat())
    monkeypatch.setattr(b,'_calls',30)
    assert client.post('/public/bonus-tracker/ask',json=packet(),headers=PUBLIC_HEADERS).status_code==429


def test_equivalent_numeric_format_preserves_grounding():
    facts=[{'id':'terms','text':'Maintain $1,500.00 and receive a $50 bonus.'}]
    assert b.validate_answer({'answer':'Maintain $1500 and receive $50.00.','factIds':['terms'],'missing':[]},facts)
    with pytest.raises(ValueError):
        b.validate_answer({'answer':'Maintain $15000.','factIds':['terms'],'missing':[]},facts)


def test_strict_response_contract_uses_known_fact_ids():
    fmt=b.answer_format(packet(),'openai/gpt-oss-120b')
    schema=fmt['json_schema']['schema']
    assert fmt['json_schema']['strict'] is True
    assert schema['additionalProperties'] is False
    assert set(schema['required'])=={'answer','factIds','missing'}
    assert schema['properties']['factIds']['items']['enum']==['terms','close_gate']
    assert b.answer_format(packet(),'other-model')=={'type':'json_object'}


def test_provider_uses_constrained_output(monkeypatch):
    monkeypatch.setenv('GROQ_API_KEY','synthetic-secret')
    monkeypatch.setenv('BONUS_QUESTION_MODEL','openai/gpt-oss-120b')
    requests=[]
    class FakeClient:
        def __init__(self,**kw):pass
        async def __aenter__(self):return self
        async def __aexit__(self,*args):pass
        async def post(self,url,**kw):
            requests.append(kw['json'])
            return b.httpx.Response(200,json={'choices':[{'finish_reason':'stop','message':{'content':json.dumps({'answer':'Bonus not received.','factIds':['close_gate'],'missing':[]})}}]},request=b.httpx.Request('POST',url))
    monkeypatch.setattr(b.httpx,'AsyncClient',FakeClient)
    assert asyncio.run(b.provider(packet()))['factIds']==['close_gate']
    assert requests[0]['response_format']['type']=='json_schema'
    assert len(requests)==1


def test_failure_diagnostics_do_not_log_private_details(client,monkeypatch,caplog):
    async def failed(p):raise ValueError('private account data and secret')
    monkeypatch.setattr(b,'provider',failed)
    r=client.post('/api/bonus-tracker/ask',json=packet(),headers={'Authorization':'Bearer owner'})
    assert r.status_code==503
    assert 'category=response_validation' in caplog.text
    assert 'private account' not in caplog.text and 'secret' not in caplog.text
