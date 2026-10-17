"""Synthetic encrypted 24-month archive regressions. No network or live provider."""
import sys
import io
from pathlib import Path
from cryptography.fernet import Fernet
from botocore.exceptions import ClientError
from fastapi import HTTPException
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from finance_history_archive import archive_old, read_month, archive_key, DAY

class Bucket:
    bucket = 'synthetic'
    def __init__(self):
        self.client=self
        self.rows={}
        self.rev=0
        self.fail=False
    def get_object(self, Bucket, Key):
        if Key not in self.rows:
            raise ClientError({'Error':{'Code':'NoSuchKey'}},'GetObject')
        raw,tag=self.rows[Key]
        return {'Body':io.BytesIO(raw),'ETag':tag}
    def put_object(self, Bucket,Key,Body,**params):
        if self.fail:
            raise ClientError({'Error':{'Code':'PreconditionFailed'}},'PutObject')
        prior=self.rows.get(Key)
        if ('IfNoneMatch' in params and prior) or ('IfMatch' in params and (not prior or prior[1] != params['IfMatch'])):
            raise ClientError({'Error':{'Code':'PreconditionFailed'}},'PutObject')
        self.rev+=1
        self.rows[Key]=(Body,str(self.rev))

def test_archive_moves_only_verified_old_records_and_keeps_original_unchanged():
    now=1750000000
    old=now-120*DAY
    rows={'acct-1':{
       'old':{'id':'old','posted':old,'amount':'-19.02','description':'Synthetic merchant'},
       'recent':{'id':'recent','posted':now-2*DAY,'amount':'1.00'}
    }}
    original=str(rows)
    bucket=Bucket()
    key=Fernet.generate_key()
    hot,months=archive_old(bucket,Fernet(key),'simplefin','synthetic-owner',rows,now)
    assert str(rows)==original, 'Archiver must never mutate existing saved data'
    assert set(hot['acct-1'])=={'recent'}
    assert months and len(months)==1
    assert read_month(bucket,Fernet(key),'simplefin','synthetic-owner',months[0])['acct-1']['old']['amount']=='-19.02'
    assert bucket.rows[archive_key('simplefin','synthetic-owner',months[0])][0] != b'Synthetic merchant'
    # Second archival request is idempotent and preserves historical rows.
    _,months2=archive_old(bucket,Fernet(key),'simplefin','synthetic-owner',rows,now)
    assert months2==months
    assert len(read_month(bucket,Fernet(key),'simplefin','synthetic-owner',months[0])['acct-1'])==1

def test_archive_cas_failure_retains_all_hot_input():
    now=1750000000
    rows={'a':{'t':{'posted':now-100*DAY,'amount':'2'}}}
    bucket=Bucket();bucket.fail=True
    with pytest.raises(HTTPException) as exc:
        archive_old(bucket,Fernet(Fernet.generate_key()),'plaid','test-user',rows,now)
    assert exc.value.status_code==409
    assert 't' in rows['a']

@pytest.mark.parametrize('month',['2026-13','2026/02','february','2026-1',''])
def test_archive_key_rejects_untrusted_paths(month):
    with pytest.raises(ValueError):
        archive_key('simplefin','synthetic-user',month)
