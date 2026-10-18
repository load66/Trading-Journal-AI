"""Synthetic-only verification of the owner-only hosting usage endpoint."""
import sys
from pathlib import Path
from datetime import datetime, timezone
from fastapi import FastAPI
from fastapi.testclient import TestClient
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import bonus_usage as u
import bonus_backups as b
from auth import AuthError

AT = datetime(2026,10,17,12,tzinfo=timezone.utc)


def _remote(token,query,variables):
    assert token == 'synthetic-do-not-log'
    if 'BonusHostingMemory' in query:
        return {"metrics":[{"tags":{"serviceId":"svc-a"},"values":[{"ts":1,"value":0.27},{"ts":2,"value":0.33}]},
                           {"tags":{"serviceId":"svc-b"},"values":[{"ts":1,"value":0.6}]}]}
    return {"project":{"services":{"edges":[{"node":{"id":"svc-a","name":"api"}},
                                          {"node":{"id":"svc-b","name":"leaps-alpaca-live"}}]}},
            "usage":[
                {"measurement":m,"value":v,"tags":{"serviceId":id}}
                for id,parts in {"svc-a":{"CPU_USAGE":3,"MEMORY_USAGE_GB":400,"NETWORK_TX_GB":0.1},
                                 "svc-b":{"CPU_USAGE":12,"MEMORY_USAGE_GB":900,"NETWORK_TX_GB":0.2}}.items()
                for m,v in parts.items()]}


def _configured(monkeypatch):
    monkeypatch.setenv('RAILWAY_API_TOKEN','synthetic-do-not-log')
    monkeypatch.setenv('RAILWAY_PROJECT_ID','synthetic-project')
    monkeypatch.setenv('RAILWAY_ENVIRONMENT_ID','synthetic-env')
    monkeypatch.setattr(u,'_railway_query',_remote)


def test_normal_railway_with_real_units_and_average_memory(monkeypatch):
    _configured(monkeypatch)
    value=u._railway_usage(AT)
    assert value['status']=='ok'
    assert value['costSoFar']>0 and value['projectedMonthEnd']>value['costSoFar']
    assert {s['name'] for s in value['services']}=={'api','leaps-alpaca-live'}
    assert value['services'][0]['avgMemoryGb']==0.3


def test_usage_near_and_over_editable_plan(monkeypatch):
    _configured(monkeypatch)
    costs=u._railway_usage(AT)
    projection=costs['projectedMonthEnd']
    def chip(limit):
        ratio=projection/limit
        return 'over' if ratio>1 else 'near' if ratio>=0.8 else 'within'
    assert chip(projection*1.4)=='within'
    assert chip(projection/0.9)=='near'
    assert chip(projection/1.1)=='over'


def test_no_token_is_not_zero(monkeypatch):
    monkeypatch.delenv('RAILWAY_API_TOKEN',raising=False)
    monkeypatch.setattr(u,'_railway_query',lambda *_: (_ for _ in ()).throw(AssertionError('should not query')))
    value=u._railway_usage(AT)
    assert value['status']=='not_configured'
    assert value['costSoFar'] is None
    assert value['projectedMonthEnd'] is None


def test_invalid_remote_response_fails_closed(monkeypatch):
    _configured(monkeypatch)
    monkeypatch.setattr(u,'_railway_query',lambda *_: {'usage':[]})
    value=u._railway_usage(AT)
    assert value['status']=='unavailable'
    assert value['costSoFar'] is None
    assert 'synthetic-do-not-log' not in str(value)


def test_r2_listing_and_failure(monkeypatch):
    class FakeStorage:
        def usage_summary(self):
            return {'stored_bytes':325000000,'object_count':23}
    monkeypatch.setattr(u,'store',lambda:FakeStorage())
    value=u._r2_usage()
    assert value['usedBytes']==325000000
    assert value['freeBytes']==10_000_000_000
    monkeypatch.setattr(u,'store',lambda: (_ for _ in ()).throw(RuntimeError('secret')))
    assert u._r2_usage()['usedBytes'] is None


def test_one_hour_cache(monkeypatch):
    u._cache.clear()
    calls=[]
    monkeypatch.setattr(u,'usage_snapshot',lambda now: calls.append(now) or {'railway':{'status':'ok'}})
    first=u.cached_usage('owner')
    assert u.cached_usage('owner')==first
    assert len(calls)==1
    cached=list(u._cache)[0]
    stamp,value=u._cache[cached]
    u._cache[cached]=(stamp-3601,value)
    u.cached_usage('owner')
    assert len(calls)==2
    u._cache.clear()


def test_owner_only_auth_checked_before_any_api_calls(monkeypatch):
    u._cache.clear()
    monkeypatch.setattr(b,'auth_required',lambda:True)
    monkeypatch.setattr(b,'authorize_header',lambda header: {'sub':'owner'} if header=='Bearer owner' else
                        (_ for _ in ()).throw(AuthError(401,'No session')))
    called=[]
    monkeypatch.setattr(u,'cached_usage',lambda user:called.append(user) or {'month':'2026-10'})
    app=FastAPI();app.include_router(u.router)
    client=TestClient(app)
    assert client.get('/api/bonus-tracker/usage').status_code==401
    assert not called
    assert client.get('/api/bonus-tracker/usage',headers={'Authorization':'Bearer owner'}).status_code==200
    assert len(called)==1
    monkeypatch.setattr(b,'auth_required',lambda:False)
    assert client.get('/api/bonus-tracker/usage',headers={'Authorization':'Bearer owner'}).status_code==503
    assert len(called)==1
