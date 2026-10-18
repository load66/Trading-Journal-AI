"""Synthetic independent vault tests; temporary disk, no provider calls."""
import hashlib
import json
import sqlite3
import sys
from pathlib import Path
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import bonus_churn_vault as vault

A, B = 'a' * 64, 'b' * 64

def packet(**changes):
    data = dict(format='BonusTrackerChurnRecord',version=1,bank='Synthetic Bank',accountType='business',entryId='SYN-B-01',opened='2025-11-03',closed='2026-03-11',bonusRecd='2026-02-10',bonus=250,tcSourceRaw='Exact saved original promotion. ' * 20,feeScheduleRaw='Original fee schedule for this enrolled offer.')
    data.update(changes)
    raw = json.dumps(data)
    return {'snapshot': raw, 'sha256': hashlib.sha256(raw.encode()).hexdigest()}

def test_immutable_encrypted_independent_versions_and_owner(tmp_path):
    first = packet(); result = vault.save(tmp_path, A, first)
    assert result['verified'] and result['storage'] == 'independent-volume'
    assert result['missingFields'] == []
    assert vault.save(tmp_path,A,first)['id'] == result['id']
    second = vault.save(tmp_path,A,packet(closed='2026-03-12'))
    assert vault.catalog(tmp_path,A)['total'] == 2
    assert vault.catalog(tmp_path,B)['total'] == 0
    assert vault.read(tmp_path,A,result['id'])['snapshot'] == first['snapshot']
    assert second['id'] != result['id']
    with pytest.raises(HTTPException) as err: vault.read(tmp_path,B,result['id'])
    assert err.value.status_code == 404
    assert b'Exact saved original' not in (tmp_path/'churn.sqlite3').read_bytes()
    assert (tmp_path/'.vault-key').stat().st_mode & 0o777 == 0o600

def test_missing_is_preserved_corruption_and_key_loss_fail_closed(tmp_path):
    p = packet(opened='',closed='',bonusRecd='',tcSourceRaw='')
    item = vault.save(tmp_path,A,p)
    assert set(item['missingFields']) == {'opened','closed','bonusRecd','tcSourceRaw'}
    with sqlite3.connect(tmp_path/'churn.sqlite3') as db:
        db.execute('UPDATE records SET body=? WHERE owner=?',(b'corrupted',A))
    with pytest.raises(Exception): vault.read(tmp_path,A,item['id'])
    (tmp_path/'.vault-key').unlink()
    with pytest.raises(ValueError): vault.catalog(tmp_path,A)
    assert not (tmp_path/'.vault-key').exists()

@pytest.mark.parametrize('changes',[{'opened':'2026-02-30'},{'access_token':'private'},{'account_number':'123'},{'financialRules':{'secret':'private'}},{'accountType':'unknown'}])
def test_validation_no_private_fields(changes):
    with pytest.raises(ValueError): vault.validate(packet(**changes)['snapshot'])

def test_checksum_and_mount_required(tmp_path,monkeypatch):
    p=packet();p['sha256']='0'*64
    with pytest.raises(ValueError): vault.save(tmp_path,A,p)
    monkeypatch.delenv('RAILWAY_VOLUME_MOUNT_PATH',raising=False)
    with pytest.raises(HTTPException) as err: vault.root()
    assert err.value.status_code == 503
    assert not (tmp_path/'churn.sqlite3').exists()

def test_routes_auth_pagination_and_safe_errors(tmp_path,monkeypatch):
    app=FastAPI();app.include_router(vault.router)
    monkeypatch.setattr(vault,'root',lambda:tmp_path)
    def auth(request):
        if request.headers.get('Authorization') != 'Bearer synthetic':raise HTTPException(401,'Sign in')
        return A
    monkeypatch.setattr(vault,'owner',auth)
    client=TestClient(app)
    assert client.get('/api/bonus-tracker/churn-vault').status_code == 401
    headers={'Authorization':'Bearer synthetic'}
    result=client.post('/api/bonus-tracker/churn-vault',json=packet(),headers=headers)
    assert result.status_code == 200
    assert result.headers['cache-control'] == 'no-store'
    digest=result.json()['id']
    assert client.get('/api/bonus-tracker/churn-vault/'+digest,headers=headers).json()['verified']
    assert client.get('/api/bonus-tracker/churn-vault?offset=-1',headers=headers).status_code == 422
    assert client.delete('/api/bonus-tracker/churn-vault/'+digest,headers=headers).status_code == 405
    assert client.post('/api/bonus-tracker/churn-vault',json=packet(opened='invalid'),headers=headers).status_code == 422
