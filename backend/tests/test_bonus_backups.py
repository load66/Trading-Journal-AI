import copy
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import hashlib
import json
from datetime import datetime,timezone,timedelta
from io import BytesIO
import pytest
from fastapi import FastAPI,HTTPException
from fastapi.testclient import TestClient
from botocore.exceptions import ClientError
import bonus_backups as b
from auth import AuthError

class MemoryR2:
    def __init__(self):self.objects={};self.bucket='synthetic';self.client=self;self.corrupt=False;self.tick=0
    def put_object(self,Bucket,Key,Body,Metadata,**kw):
        assert kw['IfNoneMatch']=='*'
        if Key in self.objects:raise ClientError({'Error':{'Code':'PreconditionFailed'},'ResponseMetadata':{'HTTPStatusCode':412}},'PutObject')
        self.tick+=1
        self.objects[Key]={'body':Body,'Metadata':Metadata,
                           'LastModified':datetime(2026,1,1,tzinfo=timezone.utc)+timedelta(microseconds=self.tick)}
    def head_object(self,Bucket,Key):
        if Key not in self.objects:raise ClientError({'Error':{'Code':'404'},'ResponseMetadata':{'HTTPStatusCode':404}},'HeadObject')
        x=self.objects[Key];return {'Metadata':x['Metadata'],'ContentLength':len(x['body'])}
    def delete_object(self,Bucket,Key):self.objects.pop(Key,None)
    def read(self,key):
        if key not in self.objects:raise FileNotFoundError(key)
        return (b'corrupted' if self.corrupt else self.objects[key]['body']),'application/json'
    def list_objects_v2(self,Bucket,Prefix,**kw):
        return {'Contents':[{'Key':k,'LastModified':v['LastModified']} for k,v in self.objects.items() if k.startswith(Prefix)]}

def packet(ident='a'*32):
    data={'format':'BonusTrackerCloudBackup','version':1,'appVersion':'3.4.53','prefs':{'dashYear':2026,'taxYear':2026},'storageSnapshot':{'bt_e_v4':json.dumps([{'id':'account-1','bank':'Synthetic CU','notes':'private notes','customTimers':[{'done':False}]}]),'bt_t_v4':'[]'}}
    raw=json.dumps(data)
    return {'id':ident,'reason':'manual','snapshot':raw,'sha256':hashlib.sha256(raw.encode()).hexdigest()}

def test_upload_readback_and_fresh_device_recovery():
    s=MemoryR2();p=packet();r=b.save_verified(s,'owner',p)
    assert r['verified'] and r['entryCount']==1
    assert b.list_backups(s,'owner')['backups'][0]['id']==p['id']
    assert b.read_verified(s,'owner',p['id'])['snapshot']==p['snapshot']
    assert b.save_verified(s,'owner',p)==r
    assert len(s.objects)==1

def test_no_overwrite_and_user_isolation():
    s=MemoryR2();p=packet();b.save_verified(s,'owner',p)
    changed=packet();changed['snapshot']=changed['snapshot'].replace('Synthetic CU','Different Bank');changed['sha256']=hashlib.sha256(changed['snapshot'].encode()).hexdigest()
    with pytest.raises(HTTPException) as e:b.save_verified(s,'owner',changed)
    assert e.value.status_code==409
    assert b.list_backups(s,'other')['backups']==[]
    with pytest.raises(HTTPException) as e:b.read_verified(s,'other',p['id'])
    assert e.value.status_code==404
    with pytest.raises(HTTPException):b.key_for('owner','../private')

def test_corruption_and_checksum_never_acknowledged():
    s=MemoryR2();p=packet();p['sha256']='0'*64
    with pytest.raises(ValueError):b.save_verified(s,'owner',p)
    assert not s.objects
    p=packet();s.corrupt=True
    with pytest.raises(ValueError):b.save_verified(s,'owner',p)
    with pytest.raises(ValueError):b.read_verified(s,'owner',p['id'])

def test_tokens_and_unknown_keys_rejected():
    p=packet();d=json.loads(p['snapshot']);d['storageSnapshot']['bonus-ai-auth']='secret'
    with pytest.raises(ValueError):b.validate_snapshot(json.dumps(d))
    d=json.loads(p['snapshot']);d['version']=2
    with pytest.raises(ValueError):b.validate_snapshot(json.dumps(d))

def test_entry_scoped_sources_roundtrip_without_credential_leak():
    """Legacy T&C resolver text survives an owner-verified snapshot and new device."""
    source = {'account-synthetic': {'raw': 'Synthetic enrolled terms and fee details.',
                                    'sourceId': 'src-test', 'kind': 'stored-entry-source'}}
    p = packet('b' * 32)
    data = json.loads(p['snapshot'])
    data['storageSnapshot']['bt_tc_entry_sources_v3'] = json.dumps(source)
    p['snapshot'] = json.dumps(data)
    p['sha256'] = hashlib.sha256(p['snapshot'].encode()).hexdigest()
    assert b.validate_snapshot(p['snapshot'])[1] == 1
    storage = MemoryR2()
    result = b.save_verified(storage, 'owner', p)
    assert result['verified']
    restored = json.loads(b.read_verified(storage, 'owner', p['id'])['snapshot'])
    assert restored['storageSnapshot']['bt_tc_entry_sources_v3'] == json.dumps(source)
    assert 'bonus-ai-auth' not in restored['storageSnapshot']
    restored['storageSnapshot']['bonus-ai-auth'] = 'not-allowed'
    with pytest.raises(ValueError):
        b.validate_snapshot(json.dumps(restored))


def test_twelve_hour_checkpoint_survives_restart():
    s=MemoryR2();b.save_verified(s,'owner',packet());now=datetime(2026,10,14,12,tzinfo=timezone.utc)
    assert b.scheduled_checkpoint(s,'owner',now)['created']
    assert not b.scheduled_checkpoint(s,'owner',now+timedelta(hours=11))['created']
    assert b.scheduled_checkpoint(s,'owner',now+timedelta(hours=12))['created']
    assert len(s.objects)==1
    assert not b.scheduled_checkpoint(s,'new-owner',now)['created']

def test_routes_require_owner_auth_before_storage(monkeypatch):
    monkeypatch.setattr(b,'auth_required',lambda:True)
    monkeypatch.setattr(b,'authorize_header',lambda h: {'sub':'owner'} if h=='Bearer owner' else (_ for _ in ()).throw(AuthError(401,'Authentication required')))
    s=MemoryR2();monkeypatch.setattr(b,'store',lambda:s)
    app=FastAPI();app.include_router(b.router);client=TestClient(app)
    assert client.get('/api/bonus-tracker/backups').status_code==401
    assert client.post('/api/bonus-tracker/backups',json=packet()).status_code==401
    assert client.post('/api/bonus-tracker/backups',json=packet(),headers={'Authorization':'Bearer owner'}).status_code==200
    assert client.get('/api/bonus-tracker/backups',headers={'Authorization':'Bearer owner'}).json()['total']==1
    monkeypatch.setattr(b,'auth_required',lambda:False)
    assert client.get('/api/bonus-tracker/backups',headers={'Authorization':'Bearer owner'}).status_code==503


def test_storage_probe_touches_only_disposable_namespace():
    s=MemoryR2();b.save_verified(s,'owner',packet());before=set(s.objects)
    assert b.probe_storage(s,'owner')['readWriteVerified']
    assert set(s.objects)==before


def different_packet(n):
    p=packet(f'{n:032x}')
    p['snapshot']=p['snapshot'].replace('private notes',f'private notes {n}')
    p['sha256']=hashlib.sha256(p['snapshot'].encode()).hexdigest()
    return p


def test_retention_is_bounded_and_keeps_previous_distinct_states():
    s=MemoryR2()
    s.objects['journal/screenshots/keep.png']={'body':b'image','Metadata':{},'LastModified':datetime.now(timezone.utc)}
    b.save_verified(s,'other-owner',packet())
    for n in range(1,15):
        result=b.save_verified(s,'owner',different_packet(n))
        assert result['retention']['cleanup']=='complete'
        assert result['retention']['retainedCopies']<=4
    assert 'journal/screenshots/keep.png' in s.objects
    assert b.read_verified(s,'other-owner','a'*32)['backup']['verified']
    ids={x['id'] for x in b.list_backups(s,'owner')['backups']}
    assert ids=={f'{n:032x}' for n in (12,13,14)}
    for ident in ids:assert b.read_verified(s,'owner',ident)['backup']['verified']


def test_failed_replacement_does_not_remove_existing_backups():
    s=MemoryR2()
    for n in range(1,4):b.save_verified(s,'owner',different_packet(n))
    before=set(s.objects);s.corrupt=True
    with pytest.raises(ValueError):b.save_verified(s,'owner',different_packet(4))
    assert before.issubset(s.objects)


def test_cleanup_failure_keeps_new_verified_backup(monkeypatch):
    s=MemoryR2()
    for n in range(1,4):b.save_verified(s,'owner',different_packet(n))
    monkeypatch.setattr(s,'delete_object',lambda **kw:(_ for _ in ()).throw(RuntimeError('unavailable')))
    result=b.save_verified(s,'owner',different_packet(4))
    assert result['verified'] and result['retention']['cleanup']=='pending'
    assert b.read_verified(s,'owner',f'{4:032x}')['backup']['verified']
    assert len(s.objects)==4


def test_current_checkpoint_survives_duplicate_uploads_and_does_not_outrank_newer_data():
    s=MemoryR2();now=datetime.now(timezone.utc)
    old=b.save_verified(s,'owner',different_packet(1))
    checkpoint=b.scheduled_checkpoint(s,'owner',now)['backup']
    for n in range(2,10):b.save_verified(s,'owner',different_packet(n),checkpoint_now=now)
    assert b.read_verified(s,'owner',checkpoint['id'])['backup']['verified']
    assert len(s.objects)<=4
    assert not b.scheduled_checkpoint(s,'owner',now)['created']
    # Model an in-flight checkpoint completing after a newer upload.
    late_id='f'*32
    b.save_verified(s,'owner',{**different_packet(1),'id':late_id,'reason':'scheduled'},data_at=old['dataAt'],checkpoint_now=now)
    assert b.list_backups(s,'owner')['backups'][0]['id']==f'{9:032x}'


def test_safety_snapshot_keeps_selected_recovery_until_restored_replacement():
    s=MemoryR2();now=datetime.now(timezone.utc)
    b.save_verified(s,'owner',different_packet(1))
    b.scheduled_checkpoint(s,'owner',now)
    for n in (2,3,4):b.save_verified(s,'owner',different_packet(n),checkpoint_now=now)
    selected=b.read_verified(s,'owner',f'{2:032x}')
    safety={**different_packet(5),'reason':'pre-restore'}
    saved=b.save_verified(s,'owner',safety,checkpoint_now=now)
    assert saved['retention']['cleanup']=='deferred-for-safety'
    assert b.read_verified(s,'owner',f'{2:032x}')['snapshot']==selected['snapshot']
    restored={**different_packet(2),'id':f'{99:032x}'}
    result=b.save_verified(s,'owner',restored,checkpoint_now=now)
    assert result['verified'] and result['retention']['retainedCopies']<=4
    assert b.list_backups(s,'owner')['backups'][0]['id']==restored['id']
    assert b.read_verified(s,'owner',restored['id'])['snapshot']==selected['snapshot']


def test_credit_card_plans_exact_recovery():
    p = packet('d' * 32)
    data = json.loads(p['snapshot'])
    cards = json.dumps({'version': 1, 'cards': [{'current': {'id': 'synthetic', 'name': 'Synthetic card'}, 'history': [{'name': 'Prior plan'}]}]})
    data['storageSnapshot']['bt_credit_cards_v1'] = cards
    p['snapshot'] = json.dumps(data)
    p['sha256'] = hashlib.sha256(p['snapshot'].encode()).hexdigest()
    storage = MemoryR2()
    assert b.save_verified(storage, 'owner', p)['verified']
    recovered = json.loads(b.read_verified(storage, 'owner', p['id'])['snapshot'])
    assert recovered['storageSnapshot']['bt_credit_cards_v1'] == cards
    assert b.list_backups(storage, 'other')['backups'] == []
