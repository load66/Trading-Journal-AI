"""Synthetic, offline account-lifecycle regressions. Provider calls are forbidden."""
import copy
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from cryptography.fernet import Fernet
import bonus_simplefin as sf
import bonus_plaid_accounts as pa
import bonus_plaid_trial as quota
import finance_account_management as life
from test_bonus_simplefin import Memory, account, data, txn, NOW, ACCESS

@pytest.fixture
def bank(monkeypatch):
    memory=Memory()
    monkeypatch.setenv('SIMPLEFIN_ENCRYPTION_KEY', Fernet.generate_key().decode())
    monkeypatch.setattr(sf, 'store', lambda:memory)
    monkeypatch.setattr(sf.time, 'time', lambda:NOW)
    monkeypatch.setattr(sf, 'owner', lambda _r:'alice')
    monkeypatch.setattr(sf, 'network', lambda *_a, **_kw:pytest.fail('Live SimpleFIN forbidden'))
    monkeypatch.setattr(pa, '_provider', lambda *_a, **_kw:pytest.fail('Live Plaid forbidden'))
    a,b,c=account(ident='a'),account(ident='b'),account(conn='other',ident='c')
    payload=data(a,b,c)
    payload['connections'].append({'conn_id':'other','name':'Other Synthetic Bank'})
    state=sf.empty_state();state.update(access=ACCESS,accounts=sf.normalize_accounts(payload),lastSuccess=NOW)
    keys=[a['key'] for a in state['accounts']]
    state['monitoredAccounts']=keys
    state['transactions']={k:{'old':{**txn(description='Synthetic grocery'),'observedAt':NOW}} for k in keys}
    state['mappings']={keys[0]:{'entryId':'bonus-unchanged','bank':'Synthetic CU','opened':'2026-10-01'}}
    sf.write_state('alice',state,None)
    app=FastAPI();app.include_router(sf.router)
    return TestClient(app), keys, payload, state, memory


def test_remove_sibling_sync_discovery_restore_and_bonus_unchanged(bank,monkeypatch):
    client,keys,payload,original,_=bank
    response=client.post('/api/bonus-tracker/simplefin/account-action',json={'account':keys[0],'action':'remove'})
    assert response.status_code==200
    result=response.json()
    assert result['accounts'][0]['removedAt']==NOW
    assert result['transactions'][keys[0]]  # keep history by default
    calls=[]
    def provider(_access,selected=None,**_kw):
        calls.append([a['key'] for a in selected])
        incoming=copy.deepcopy(payload)
        for a in incoming['accounts']:a['transactions']=[txn('new',description='Synthetic new debit')]
        return incoming
    monkeypatch.setattr(sf,'get_data',provider)
    sf.sync('alice')
    saved,_=sf.read_state('alice')
    assert keys[0] not in calls[0] and set(calls[0])==set(keys[1:])
    assert 'new' not in saved['transactions'][keys[0]]
    assert 'new' in saved['transactions'][keys[1]]
    assert saved['mappings']==original['mappings']
    assert saved['accountExclusions'][keys[0]]['removedAt']==NOW
    restored=client.post('/api/bonus-tracker/simplefin/account-action',json={'account':keys[0],'action':'restore'})
    assert restored.status_code==200 and restored.json()['accounts'][0]['removedAt'] is None
    assert sf.read_state('alice')[0]['transactions'][keys[0]]==original['transactions'][keys[0]]


def test_hide_rename_and_legacy_profile_save_preserves_settings(bank):
    client,keys,*_=bank
    values={'kind':'checking','order':0,'balanceMode':'asset','hidden':True,'includeSpending':False,'displayName':'My cash','syncEnabled':False}
    path='/api/bonus-tracker/simplefin/account-profiles'
    assert client.put(path,json={'profiles':{keys[0]:values}}).status_code==200
    legacy={'kind':'checking','order':1,'balanceMode':'exclude'}
    assert client.put(path,json={'profiles':{keys[0]:legacy}}).status_code==200
    saved=sf.read_state('alice')[0]['accountProfiles'][keys[0]]
    assert saved['hidden'] is True and saved['includeSpending'] is False and saved['displayName']=='My cash'
    assert saved['syncEnabled'] is False
    assert client.put(path,json={'profiles':{keys[0]:{**legacy,'hidden':False}}}).json()['accountProfiles'][keys[0]]['hidden'] is False


def test_explicit_delete_has_undo_window_and_does_not_touch_siblings(bank,monkeypatch):
    client,keys,_,original,_=bank
    base={'account':keys[0]}
    removed=client.post('/api/bonus-tracker/simplefin/account-action',json={**base,'action':'remove','deleteHistory':True}).json()['accountAction']
    delete={**base,'action':'delete_history','deleteHistory':True,'removalId':removed['removalId']}
    assert client.post('/api/bonus-tracker/simplefin/account-action',json=delete).status_code==409
    monkeypatch.setattr(sf.time,'time',lambda:NOW+11)
    assert client.post('/api/bonus-tracker/simplefin/account-action',json=delete).status_code==200
    saved,_=sf.read_state('alice')
    assert keys[0] not in saved['transactions']
    assert saved['transactions'][keys[1]]==original['transactions'][keys[1]]
    assert saved['mappings']==original['mappings']
    assert client.post('/api/bonus-tracker/simplefin/account-action',json={**base,'action':'restore'}).status_code==200
    assert sf.read_state('alice')[0]['accountExclusions'][keys[0]]['deletedBefore']==NOW


def test_undo_prevents_delayed_history_deletion(bank,monkeypatch):
    client,keys,*_=bank
    payload={'account':keys[0],'action':'remove','deleteHistory':True}
    receipt=client.post('/api/bonus-tracker/simplefin/account-action',json=payload).json()['accountAction']
    assert client.post('/api/bonus-tracker/simplefin/account-action',json={**payload,'action':'restore','removalId':receipt['removalId']}).status_code==200
    monkeypatch.setattr(sf.time,'time',lambda:NOW+11)
    assert client.post('/api/bonus-tracker/simplefin/account-action',json={**payload,'action':'delete_history','removalId':receipt['removalId']}).status_code==409
    assert keys[0] in sf.read_state('alice')[0]['transactions']


def test_simplefin_disconnect_one_institution_preserves_other_history(bank):
    client,keys,_,original,_=bank
    handle=life.institution_handle(original['accounts'][0])
    result=client.post('/api/bonus-tracker/simplefin/institution-action',json={'handle':handle})
    assert result.status_code==200
    saved,_=sf.read_state('alice')
    assert not life.syncing(saved,saved['accounts'][0])
    assert not life.syncing(saved,saved['accounts'][1])
    assert life.syncing(saved,saved['accounts'][2])
    assert saved['transactions']==original['transactions'] and saved['mappings']==original['mappings']
    restored=client.post('/api/bonus-tracker/simplefin/institution-action',json={'handle':handle,'restore':True})
    assert restored.status_code==200
    assert all(life.syncing(sf.read_state('alice')[0],a) for a in saved['accounts'])


def test_failure_backoff_and_daily_request_budget(bank,monkeypatch):
    _,_,_,_,_=bank
    monkeypatch.setattr(sf,'get_data',lambda *_a,**_k: (_ for _ in ()).throw(HTTPException(502,'Synthetic outage')))
    clock=NOW
    for hours in (1,3,6,24):
        monkeypatch.setattr(sf.time,'time',lambda:clock)
        result=sf.sync('alice')
        assert result['nextSyncAt']==clock+hours*3600
        assert result['health']['lastError']=='HTTPException'
        clock=result['nextSyncAt']


def test_plaid_existing_item_update_never_reserves_or_exchanges(monkeypatch):
    doc={'version':1,'used':10,'holds':{},'items':{'synthetic':{'owner':'alice','access':'fake','institution':'Synthetic'}}}
    monkeypatch.setattr(quota,'owner',lambda _r:'alice')
    monkeypatch.setattr(quota,'_ledger',lambda:(copy.deepcopy(doc),'etag'))
    monkeypatch.setattr(quota,'_write',lambda *_:pytest.fail('Update mode must not write quota'))
    calls=[]
    def provider(endpoint,body):
        calls.append(endpoint)
        assert endpoint=='/link/token/create'
        assert body['access_token']=='fake' and body['update']=={'account_selection_enabled':True}
        assert 'products' not in body
        return {'link_token':'synthetic-update'}
    monkeypatch.setattr(quota,'_provider',provider)
    handle=pa.connection_handle('alice','synthetic')
    result=quota.update_link(handle,None)
    assert result['existingItem'] and doc['used']==10 and calls==['/link/token/create']
    with pytest.raises(HTTPException):quota.update_link('b'*64,None)


def test_plaid_exclusion_preserves_old_history_and_filters_next_delta():
    key=pa._account_key('item','a')
    old={'items':{'item':{'accounts':[{'key':key,'id':'a'}], 'transactions':{'old':{'account':'a','posted':1}}}},
         'accountExclusions':{key:{'removedAt':10}}}
    candidate=copy.deepcopy(old);candidate['items']['item']['transactions']['new']={'account':'a','posted':20}
    pa._preserve_excluded(candidate,old)
    assert candidate['items']['item']['transactions']==old['items']['item']['transactions']


def test_all_simplefin_requests_share_24_per_day_budget(bank,monkeypatch):
    _,_,_,_,_=bank
    state,tag=sf.read_state('alice')
    state['requestAttempts']=[NOW-2]*24
    sf.write_state('alice',state,tag)
    monkeypatch.setattr(sf,'get_data',lambda *_a,**_k:pytest.fail('Quota must stop before a remote call'))
    result=sf.sync('alice')
    assert result['status']=='attention'
    assert 'Daily bank refresh limit' in result['error']
