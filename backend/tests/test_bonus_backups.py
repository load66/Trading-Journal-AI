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
    def __init__(self):self.objects={};self.bucket='synthetic';self.client=self;self.corrupt=False
    def put_object(self,Bucket,Key,Body,Metadata,**kw):
        assert kw['IfNoneMatch']=='*'
        if Key in self.objects:raise ClientError({'Error':{'Code':'PreconditionFailed'},'ResponseMetadata':{'HTTPStatusCode':412}},'PutObject')
        self.objects[Key]={'body':Body,'Metadata':Metadata,'LastModified':datetime.now(timezone.utc)}
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

def test_twelve_hour_checkpoint_survives_restart():
    s=MemoryR2();b.save_verified(s,'owner',packet());now=datetime(2026,10,14,12,tzinfo=timezone.utc)
    assert b.scheduled_checkpoint(s,'owner',now)['created']
    assert not b.scheduled_checkpoint(s,'owner',now+timedelta(hours=11))['created']
    assert b.scheduled_checkpoint(s,'owner',now+timedelta(hours=12))['created']
    assert len(s.objects)==3
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
