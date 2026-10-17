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
    ("budgets","unreviewed",{"amount":"125.50"}),
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

def test_saved_finance_rules_are_validated_and_do_not_change_account_records():
    assert f._validate("rules","example employer",{"category":"income","employer":True}) == {
        "category":"income","employer":True,"ownAccount":False}
    assert f._validate("rules","to citibank",{"category":"transfer","ownAccount":True})["ownAccount"]
    assert f._validate("rules","example employer",None) is None
    with pytest.raises(HTTPException):
        f._validate("rules","to citibank",{"category":"income","ownAccount":True})
    with pytest.raises(HTTPException):
        f._validate("rules","example employer",{"category":"transfer","employer":True})
    with pytest.raises(HTTPException):
        f._validate("rules","x",{"category":"transfer"})


def test_private_owner_name_preferences_are_scoped_and_not_logged(monkeypatch):
    expected={"names":["OWNER NAME","SECOND ALIAS"]}
    assert f._validate("ownerNames","primary",expected)==expected
    assert f._validate("ownerNames","primary",None) is None
    assert f._validate("budgets","insurance",{"amount":"89.14"}) == {"amount":"89.14"}
    with pytest.raises(HTTPException):
        f._validate("budgets","loan_proceeds",{"amount":"89.14"})
    with pytest.raises(HTTPException):
        f._validate("ownerNames","primary",{"names":["EXAMPLE OWNER 12345"]})
    with pytest.raises(HTTPException):
        f._validate("ownerNames","invalid-key",expected)
    m=Memory()
    monkeypatch.setattr(f,"store",lambda:m)
    key=Fernet.generate_key()
    monkeypatch.setattr(f,"cipher",lambda:Fernet(key))
    state=f._default()
    state["ownerNames"]["primary"]=expected
    state["revision"]=1
    f._write("owner-a",state,None)
    assert f._read("owner-a")[0]["ownerNames"]["primary"]==expected
    assert f._read("owner-b")[0]["ownerNames"]=={}
    stored=next(iter(m.data.values()))[0]
    assert b"OWNER NAME" not in stored
