"""Finance-only encrypted preferences: strict schemas and safe conditional updates."""
import base64
import io
import sys
from pathlib import Path
from cryptography.fernet import Fernet
from botocore.exceptions import ClientError
from fastapi import HTTPException
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import finance_preferences as f

class Memory:
    bucket="fake"
    def __init__(self):
        self.client=self
        self.data={}
        self.rev=0
    def get_object(self,Bucket,Key):
        if Key not in self.data:raise ClientError({"Error":{"Code":"NoSuchKey"}},"GetObject")
        raw,tag=self.data[Key]
        return {"Body":io.BytesIO(raw),"ETag":tag}
    def put_object(self,Bucket,Key,Body,**kwargs):
        old=self.data.get(Key)
        if (kwargs.get("IfNoneMatch")=="*" and old) or (kwargs.get("IfMatch") and (not old or old[1]!=kwargs["IfMatch"])):
            raise ClientError({"Error":{"Code":"PreconditionFailed"}},"PutObject")
        self.rev+=1
        self.data[Key]=(Body,str(self.rev))

def test_preferences_encrypted_per_owner_and_verified(monkeypatch):
    m=Memory()
    monkeypatch.setattr(f,"store",lambda:m)
    key=Fernet.generate_key()
    monkeypatch.setattr(f,"cipher",lambda:Fernet(key))
    state=f._default()
    assert f._read("owner-a")[0]==state
    state["budgets"]={"groceries":{"amount":"250.00"}}
    state["revision"]=1
    assert f._write("owner-a",state,None)==state
    assert f._read("owner-a")[0]==state
    assert f._read("owner-b")[0]==f._default()
    assert b"groceries" not in next(iter(m.data.values()))[0]
    with pytest.raises(HTTPException) as exc:
        f._write("owner-a",state,None)
    assert exc.value.status_code==409

@pytest.mark.parametrize("section,key,value",[
    ("budgets","groceries",{"amount":"350.17"}),
    ("duplicates","simplefin:"+("a"*64)+"|plaid:"+("b"*64),{"primary":"plaid:"+("b"*64)}),
    ("recurring","synthetic stream|debit",{"name":"Streaming","dismissed":True}),
    ("transactionMeta","simplefin:"+("a"*64)+":fake-1",{"name":"Store","note":"Personal","exclude":True})
])
def test_safe_metadata_validation(section,key,value):
    assert f._validate(section,key,value)

@pytest.mark.parametrize("section,key,value",[
    ("budgets","transfer",{"amount":"150"}),
    ("budgets","groceries",{"amount":"NaN"}),
    ("budgets","groceries",{"amount":"-1.00"}),
    ("budgets","groceries",{"amount":"10.0001"}),
    ("duplicates","simplefin:"+"a"*64+"|plaid:"+"b"*64,{"primary":"simplefin:"+"c"*64}),
    ("recurring","bad!",{"name":"No"}),
    ("transactionMeta","plaid:a:b",{"note":"X"}),
])
def test_invalid_preferences_fail_closed(section,key,value):
    with pytest.raises(HTTPException):
        f._validate(section,key,value)


def test_uncategorized_budget_is_allowed_without_changing_transaction_classification():
    assert f._validate('budgets', 'unreviewed', {'amount':'125.50'}) == {'amount':'125.50'}
    with pytest.raises(HTTPException):
        f._validate('budgets', 'transfer', {'amount':'125.50'})
