"""Private Finance preferences, independent of bank credentials and bonus milestones.

Only owner-scoped annotations. Existing provider records remain immutable.
"""
import copy
import json
import re
from decimal import Decimal, InvalidOperation
from fastapi import APIRouter, Request, Response, HTTPException
from pydantic import BaseModel
from botocore.exceptions import ClientError
from bonus_backups import owner, store
from bonus_simplefin import cipher

router = APIRouter(prefix="/api/bonus-tracker/finances")
MAX_OBJECT = 512 * 1024
LIMIT_ENTRIES = 2000
CATEGORIES = frozenset(("housing","loan","utilities","groceries","dining","transportation",
    "shopping","insurance","health","entertainment","fees","taxes","travel","subscription","other","unreviewed"))
RULE_CATEGORIES = CATEGORIES | frozenset(('income','transfer','loan_proceeds','refund','verification','bonus'))
ACCOUNT = re.compile(r"^(simplefin|plaid):[a-f0-9]{64}$")
PAIR = re.compile(r"^((simplefin):[a-f0-9]{64})\|((plaid):[a-f0-9]{64})$")
ENTRY = re.compile(r"^(simplefin|plaid):[a-f0-9]{64}:[a-zA-Z0-9_-]{1,128}$")


def _private(response: Response):
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"


def _key(uid):
    return "bonus-tracker-finances/v1/" + uid + "/preferences.enc"


def _default():
    return {"version":1,"revision":0,"budgets":{},"duplicates":{},
            "recurring":{},"transactionMeta":{},"rules":{}}


def _read(uid):
    s=store()
    try:
        response=s.client.get_object(Bucket=s.bucket,Key=_key(uid))
    except ClientError as exc:
        if exc.response.get("Error",{}).get("Code") in ("NoSuchKey","404"):
            return _default(), None
        raise HTTPException(503,"Finance settings are temporarily unavailable.") from None
    try:
        raw=response["Body"].read(MAX_OBJECT+1)
        if len(raw)>MAX_OBJECT:
            raise ValueError("too large")
        doc=json.loads(cipher().decrypt(raw))
        if not isinstance(doc,dict) or doc.get("version")!=1:
            raise ValueError("invalid")
        doc.setdefault("rules",{})
        if not isinstance(doc["rules"],dict):
            raise ValueError("rules")
        return doc,response["ETag"]
    except Exception:
        raise HTTPException(503,"Finance settings need attention; saved values remain untouched.") from None


def _write(uid,doc,etag):
    raw=cipher().encrypt(json.dumps(doc,sort_keys=True,separators=(",",":")).encode())
    if len(raw)>MAX_OBJECT:
        raise HTTPException(413,"Finance settings exceed the safe size limit.")
    s=store()
    try:
        s.client.put_object(Bucket=s.bucket,Key=_key(uid),Body=raw,ContentType="application/octet-stream",
                            **({"IfMatch":etag} if etag else {"IfNoneMatch":"*"}))
    except ClientError as exc:
        if exc.response.get("Error",{}).get("Code") in ("PreconditionFailed","ConditionalRequestConflict","409","412"):
            raise HTTPException(409,"Finance settings changed; reload before saving.") from None
        raise HTTPException(503,"Finance settings could not be saved.") from None
    read,_tag=_read(uid)
    if read!=doc:
        raise HTTPException(503,"Finance settings readback failed. Your prior settings remain available.")
    return doc


def _decimal(value):
    try:
        n=Decimal(str(value))
        if not n.is_finite() or n<0 or n>10000000 or n.as_tuple().exponent < -2:
            raise ValueError()
        return format(n,".2f")
    except (ValueError,InvalidOperation):
        raise HTTPException(422,"Enter a nonnegative budget with two decimal places.") from None


class Change(BaseModel):
    revision:int
    section:str
    key:str
    value:dict|None


def _validate(section,key,value):
    if section=="budgets":
        if key not in CATEGORIES or (value is not None and set(value)!={"amount"}):
            raise HTTPException(422,"Invalid spending budget.")
        return None if value is None else {"amount":_decimal(value["amount"])}
    if section=="duplicates":
        if not PAIR.fullmatch(key):
            raise HTTPException(422,"Choose one bank account from each connection.")
        if value is None:
            return None
        pair=key.split("|")
        if value=={"separate":True}:
            return {"separate":True}
        if set(value)!={"primary"} or value["primary"] not in pair:
            raise HTTPException(422,"Select the preferred account.")
        return {"primary":value["primary"]}
    if section=="recurring":
        if not re.fullmatch(r"[a-z0-9| _-]{1,160}",key):
            raise HTTPException(422,"Invalid recurring item.")
        if value is None:
            return None
        if not isinstance(value,dict) or set(value)-{"name","dismissed","confirmed"}:
            raise HTTPException(422,"Invalid recurring item changes.")
        name=value.get("name","")
        if not isinstance(name,str) or len(name)>90 or not isinstance(value.get("dismissed",False),bool) or not isinstance(value.get("confirmed",False),bool):
            raise HTTPException(422,"Invalid recurring item changes.")
        return {"name":name.strip(),"dismissed":value.get("dismissed",False),"confirmed":value.get("confirmed",False)}
    if section=="rules":
        if not isinstance(key,str) or not 2 <= len(key) <= 90 or any(ord(c)<32 for c in key):
            raise HTTPException(422,"Choose a valid merchant or transaction description.")
        if value is None:
            return None
        if (not isinstance(value,dict) or set(value)-{"category","employer","ownAccount"}
                or value.get("category") not in RULE_CATEGORIES
                or type(value.get("employer",False)) is not bool
                or type(value.get("ownAccount",False)) is not bool):
            raise HTTPException(422,"Choose a valid Finance category rule.")
        if value.get("ownAccount") and value.get("category")!="transfer":
            raise HTTPException(422,"Own-account rules must be transfers.")
        if value.get("employer") and value.get("category")!="income":
            raise HTTPException(422,"Employer rules must be income.")
        return {"category":value["category"],"employer":value.get("employer",False),
                "ownAccount":value.get("ownAccount",False)}
    if section=="transactionMeta":
        if not ENTRY.fullmatch(key):
            raise HTTPException(422,"Invalid transaction.")
        if value is None:
            return None
        if not isinstance(value,dict) or set(value)-{"name","note","exclude","markRecurring"}:
            raise HTTPException(422,"Invalid transaction changes.")
        name,note=value.get("name",""),value.get("note","")
        if not isinstance(name,str) or len(name)>90 or not isinstance(note,str) or len(note)>500:
            raise HTTPException(422,"Transaction note is too long.")
        if not isinstance(value.get("exclude",False),bool) or not isinstance(value.get("markRecurring",False),bool):
            raise HTTPException(422,"Invalid transaction switches.")
        return {"name":name.strip(),"note":note.strip(),"exclude":value.get("exclude",False),"markRecurring":value.get("markRecurring",False)}
    raise HTTPException(422,"Unknown Finance settings section.")


@router.get("/preferences")
def get_preferences(request:Request,response:Response):
    _private(response)
    return _read(owner(request))[0]


@router.put("/preferences")
def update_preferences(body:Change,request:Request,response:Response):
    _private(response)
    uid=owner(request)
    doc,etag=_read(uid)
    if body.revision!=doc.get("revision",0):
        raise HTTPException(409,"These settings changed; reload before saving.")
    if body.section not in ("budgets","duplicates","recurring","transactionMeta","rules"):
        raise HTTPException(422,"Unknown Finance settings section.")
    value=_validate(body.section,body.key,body.value)
    new=copy.deepcopy(doc)
    rows=new.setdefault(body.section,{})
    if value is None:
        rows.pop(body.key,None)
    else:
        if body.key not in rows and len(rows)>=LIMIT_ENTRIES:
            raise HTTPException(413,"Too many Finance settings.")
        rows[body.key]=value
    new["revision"]=int(doc.get("revision",0))+1
    return _write(uid,new,etag)
