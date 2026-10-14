import hashlib
import json
import sys
from pathlib import Path
from io import BytesIO
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from botocore.exceptions import ClientError
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import venza_cloud as v
from auth import AuthError

class MemoryR2:
    bucket='synthetic'
    def __init__(self):self.objects={};self.client=self;self.corrupt=False
    def get_object(self,Bucket,Key):
        if Key not in self.objects:raise ClientError({'Error':{'Code':'NoSuchKey'}},'GetObject')
        x=self.objects[Key];return {**x,'Body':BytesIO(b'bad' if self.corrupt else x['raw']),'ContentLength':len(x['raw'])}
    def put_object(self,Bucket,Key,Body,Metadata,**kw):
        old=self.objects.get(Key)
        if (kw.get('IfNoneMatch')=='*' and old) or ('IfMatch' in kw and (not old or old['ETag']!=kw['IfMatch'])):
            raise ClientError({'ResponseMetadata':{'HTTPStatusCode':412}},'PutObject')
        etag='"'+hashlib.md5(Body).hexdigest()+'"'
        self.objects[Key]={'raw':Body,'ETag':etag,'Metadata':Metadata}
        return {'ETag':etag}

def packet(revision=None,mi='70000'):
    raw=json.dumps({'format':'VenzaCloudState','version':1,'storageSnapshot':{'vz_log':json.dumps([{'id':1,'type':'oil','mi':int(mi),'desc':'Synthetic oil service'}]),'vz_mi':mi,'vz_inventory':'[]','vz_photo_1':'data:image/jpeg;base64,dGVzdA=='}})
    return {'snapshot':raw,'sha256':hashlib.sha256(raw.encode()).hexdigest(),'baseRevision':revision}

def test_round_trip_replacement_new_phone_and_isolation():
    s=MemoryR2();assert not v.read_state(s,'owner')['exists']
    p=packet();saved=v.save_state(s,'owner',p)
    assert saved['verified'] and v.read_state(s,'owner')['snapshot']==p['snapshot']
    assert not v.read_state(s,'another-owner')['exists']
    s.objects['journal/screenshot.png']={'raw':b'keep'}
    for i in range(10):saved=v.save_state(s,'owner',packet(saved['revision'],str(70001+i)))
    assert len(s.objects)==2 and s.objects['journal/screenshot.png']['raw']==b'keep'

def test_stale_phone_cannot_overwrite_newer_copy():
    s=MemoryR2();first=v.save_state(s,'owner',packet());v.save_state(s,'owner',packet(first['revision'],'71000'))
    with pytest.raises(HTTPException) as exc:v.save_state(s,'owner',packet(first['revision'],'72000'))
    assert exc.value.status_code==409
    assert '71000' in v.read_state(s,'owner')['snapshot']
    with pytest.raises(HTTPException):v.save_state(s,'owner',packet())

def test_checksum_bad_json_and_credentials_rejected():
    s=MemoryR2();p=packet();p['sha256']='0'*64
    with pytest.raises(ValueError):v.save_state(s,'owner',p)
    assert not s.objects
    for key in ('trading-journal-auth','vz_cloud_auth','vz_cloud_meta','bt_e_v4','vz_migration_../../journal'):
        d=json.loads(packet()['snapshot']);d['storageSnapshot'][key]='secret'
        with pytest.raises(ValueError):v.validate_snapshot(json.dumps(d))
    d=json.loads(packet()['snapshot']);d['storageSnapshot']['vz_inventory']='not json'
    with pytest.raises(ValueError):v.validate_snapshot(json.dumps(d))
    v.save_state(s,'owner',packet());s.corrupt=True
    with pytest.raises(ValueError):v.read_state(s,'owner')

def test_auth_before_storage_and_endpoints(monkeypatch):
    s=MemoryR2()
    def owner(request):
        if request.headers.get('Authorization')!='Bearer owner':raise HTTPException(401,'Sign in')
        return 'owner'
    monkeypatch.setattr(v,'owner',owner);monkeypatch.setattr(v,'store',lambda:s)
    app=FastAPI();app.include_router(v.router);c=TestClient(app)
    assert c.get('/api/venza/state').status_code==401
    assert c.put('/api/venza/state',json=packet()).status_code==401
    assert not s.objects
    headers={'Authorization':'Bearer owner'}
    assert c.put('/api/venza/state',json=packet(),headers=headers).json()['verified']
    assert c.get('/api/venza/state',headers=headers).json()['exists']
    assert c.put('/api/venza/state',json=packet(),headers=headers).status_code==409
    assert c.get('/api/venza/state',headers=headers).headers['cache-control']=='no-store'
