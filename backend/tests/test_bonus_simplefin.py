import base64
import copy
import hashlib
import io
import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pytest
from cryptography.fernet import Fernet
from botocore.exceptions import ClientError
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
import bonus_simplefin as b

NOW=1792134000
ACCESS='https://synthetic:secret@beta-bridge.simplefin.org/simplefin'
CLAIM='https://beta-bridge.simplefin.org/simplefin/claim/synthetic'
TOKEN=base64.b64encode(CLAIM.encode()).decode()

class Memory:
    bucket='synthetic'
    def __init__(self):self.client=self;self.items={};self.rev=0
    def get_object(self,Bucket,Key):
        if Key not in self.items:raise ClientError({'Error':{'Code':'NoSuchKey'}},'GetObject')
        raw,tag=self.items[Key];return {'Body':io.BytesIO(raw),'ETag':tag}
    def put_object(self,Bucket,Key,Body,**kw):
        old=self.items.get(Key)
        if (kw.get('IfNoneMatch')=='*' and old) or (kw.get('IfMatch') and (not old or old[1]!=kw['IfMatch'])):
            raise ClientError({'Error':{'Code':'PreconditionFailed'}},'PutObject')
        self.rev+=1;self.items[Key]=(Body,str(self.rev))

@pytest.fixture
def setup(monkeypatch):
    m=Memory();monkeypatch.setenv('SIMPLEFIN_ENCRYPTION_KEY',Fernet.generate_key().decode());monkeypatch.setattr(b,'store',lambda:m);monkeypatch.setattr(b.time,'time',lambda:NOW)
    return m

def account(conn='one',ident='a',transactions=None):
    return {'id':ident,'conn_id':conn,'name':'Checking','currency':'USD','balance':'50.00',
            'available-balance':'45.00','balance-date':NOW-300,'transactions':transactions or []}

def data(*accounts):return {'accounts':list(accounts),'connections':[{'conn_id':'one','name':'Synthetic CU'}],'errlist':[]}

def txn(ident='t1',amount='200',description='New Account Promotional Bonus',pending=False):
    return {'id':ident,'amount':amount,'description':description,'posted':NOW-100,'pending':pending}

def seed():
    a=account();k=b.account_key(a)
    return {'version':1,'access':ACCESS,'accounts':b.normalize_accounts(data(a)),'mappings':{k:{'entryId':'cycle1','bank':'Synthetic CU','opened':'2026-10-01'}},'transactions':{}}

@pytest.mark.parametrize('url',[
 'http://beta-bridge.simplefin.org/simplefin/claim/a','https://127.0.0.1/simplefin/claim/a',
 'https://beta-bridge.simplefin.org.attacker.test/simplefin/claim/a','https://beta-bridge.simplefin.org:8080/simplefin/claim/a',
 'https://user:pass@beta-bridge.simplefin.org/simplefin/claim/a','https://beta-bridge.simplefin.org/simplefin/claim/a?x=1',
 'https://beta-bridge.simplefin.org/simplefin/claim/../../info','https://beta-bridge.simplefin.org/simplefin/claim/a#x'])
def test_url_allowlist(url):
    with pytest.raises(HTTPException):b.safe_url(url,claim=True)

def test_token_and_access_validation():
    assert b.claim_url(TOKEN)==CLAIM
    with pytest.raises(HTTPException):b.claim_url('not base64')
    with pytest.raises(HTTPException):b.safe_url('https://beta-bridge.simplefin.org/simplefin')

def test_encryption_isolation_and_conflicting_write(setup):
    s=seed();tag=b.write_state('owner',s,None)
    raw=setup.items[b.key('owner')][0]
    assert b'secret' not in raw and b'Synthetic CU' not in raw
    assert b.read_state('owner')[0]==s
    assert 'access' not in b.read_state('other')[0]
    b.write_state('owner',s,tag)
    with pytest.raises(HTTPException) as e:b.write_state('owner',s,tag)
    assert e.value.status_code==409
    assert 'access' not in b.public_state(s)
    assert ACCESS not in json.dumps(b.public_state(s))

def test_merge_signs_pending_duplicate_corrections_and_missing(setup):
    s=seed();k=b.account_key(account())
    b.merge(s,data(account(transactions=[txn(),txn(),txn('pending','200',pending=True),txn('pay','500','Payroll'),txn('transfer','900','Transfer from savings'),txn('interest','.17','Dividend Deposit')])),NOW)
    assert len(s['transactions'][k])==5
    assert s['transactions'][k]['pending']['pending'] is True
    assert s['transactions'][k]['t1']['kind']=='possible_bonus'
    assert s['transactions'][k]['pay']['kind']=='other_deposit'
    assert s['transactions'][k]['transfer']['kind']=='other_deposit'
    b.merge(s,data(account(transactions=[txn('t1','0','Corrected Promotional Bonus'),txn('reversal','-200','Promotional Bonus Reversal')])),NOW+1)
    assert s['transactions'][k]['t1']['changedAt']==NOW+1
    assert s['transactions'][k]['reversal']['kind']=='possible_reversal'
    b.merge(s,data(),NOW+2)
    assert k in s['missingAccounts'] and len(s['transactions'][k])==6

def test_exact_account_scope_same_id_different_connection(setup):
    s=seed();b.merge(s,data(account('two',transactions=[txn('wrong')])),NOW)
    assert not s['transactions']
    assert b.account_key(account('one'))!=b.account_key(account('two'))
    assert [a['key'] for a in s['accounts']] == [b.account_key(account('one'))]

def test_invalid_amount_does_not_commit_partial_merge(setup,monkeypatch):
    s=seed();b.write_state('owner',s,None)
    monkeypatch.setattr(b,'get_data',lambda *a,**kw:data(account(transactions=[txn(amount='NaN')])))
    result=b.sync('owner');assert result['status']=='attention' and not result['transactions']

def test_sync_cadence_and_no_unselected_fetch(setup,monkeypatch):
    s=seed();b.write_state('owner',s,None);calls=[]
    def fetch(access,selected):calls.append(selected);return data(account(transactions=[txn()]))
    monkeypatch.setattr(b,'get_data',fetch)
    assert b.sync('owner')['connected']
    b.sync('owner');assert len(calls)==1 and len(calls[0])==1
    s,tag=b.read_state('owner');s['mappings']={};s['nextSyncAt']=0;b.write_state('owner',s,tag)
    b.sync('owner');assert len(calls)==1

def test_failed_sync_is_throttled_and_retains_evidence(setup,monkeypatch):
    s=seed();b.merge(s,data(account(transactions=[txn()])),NOW);b.write_state('owner',s,None)
    def fail(*a):raise HTTPException(403,'Access rejected')
    monkeypatch.setattr(b,'get_data',fail);r=b.sync('owner')
    assert r['error']=='Access rejected' and r['transactions'] and r['nextSyncAt']==NOW+b.DAY

def client(monkeypatch):
    def auth(req):
        if req.headers.get('Authorization')!='Bearer synthetic':raise HTTPException(401,'Sign in')
        return 'owner'
    monkeypatch.setattr(b,'owner',auth);app=FastAPI();app.include_router(b.router);return TestClient(app)

def test_auth_all_endpoints_and_disconnect_tombstone(setup,monkeypatch):
    c=client(monkeypatch);prefix='/api/bonus-tracker/simplefin';h={'Authorization':'Bearer synthetic'}
    for method,path,payload in [('get','',None),('post','/sync',None),('post','/discover',None),('delete','',None),('post','/connect',{'setup_token':TOKEN}),('post','/reconnect',{'setup_token':TOKEN}),('put','/selection',{'mappings':{}}),('put','/monitoring',{'accounts':[]}),('put','/account-profiles',{'profiles':{}}),('put','/transaction-review',{'account':'a'*64,'transaction':'b'*64,'category':'other'})]:
        assert getattr(c,method)(prefix+path,**({'json':payload} if payload else {})).status_code==401
    b.write_state('owner',seed(),None)
    r=c.delete(prefix,headers=h);assert r.status_code==200 and not r.json()['connected']
    assert 'access' not in b.read_state('owner')[0]
    assert b.key('owner') in setup.items

def test_connect_preserves_claimed_access_after_discovery_failure(setup,monkeypatch):
    c=client(monkeypatch);monkeypatch.setattr(b,'network',lambda *a,**kw:ACCESS.encode())
    def fail(*a,**kw):raise HTTPException(502,'Try later')
    monkeypatch.setattr(b,'get_data',fail)
    r=c.post('/api/bonus-tracker/simplefin/connect',headers={'Authorization':'Bearer synthetic'},json={'setup_token':TOKEN})
    assert r.status_code==200 and r.json()['connected'] and r.json()['error']=='Try later'
    assert b.read_state('owner')[0]['access']==ACCESS
    assert ACCESS not in r.text and TOKEN not in r.text


def test_reconnect_replaces_access_and_preserves_financial_history(setup, monkeypatch):
    s = seed(); k = b.account_key(account()); s['monitoredAccounts'] = [k]
    s['accountProfiles'] = {k: {'kind':'checking','order':0,'balanceMode':'asset'}}
    b.merge(s, data(account(transactions=[txn()])), NOW)
    b.write_state('owner', s, None)
    replacement = 'https://replacement:new-secret@beta-bridge.simplefin.org/simplefin'
    monkeypatch.setattr(b, 'network', lambda *a, **kw: replacement.encode())
    c = client(monkeypatch); h = {'Authorization':'Bearer synthetic'}
    result = c.post('/api/bonus-tracker/simplefin/reconnect', headers=h,
                    json={'setup_token':TOKEN})
    assert result.status_code == 200
    payload = result.json()
    assert payload['connected'] and payload['status'] == 'connected'
    assert payload['nextSyncAt'] == 0
    assert payload['mappings'] == s['mappings']
    assert payload['monitoredAccounts'] == [k]
    assert payload['accountProfiles'][k]['kind'] == 'checking'
    assert payload['transactions'][k][b.public_ref(k,'t1')]['kind'] == 'possible_bonus'
    stored = b.read_state('owner')[0]
    assert stored['access'] == replacement
    assert ACCESS not in result.text and replacement not in result.text and TOKEN not in result.text


def test_failed_reconnect_keeps_existing_access_and_evidence(setup, monkeypatch):
    s = seed(); k = b.account_key(account()); b.merge(s, data(account(transactions=[txn()])), NOW)
    b.write_state('owner', s, None)
    def fail(*a, **kw):
        raise HTTPException(403, 'Replacement rejected')
    monkeypatch.setattr(b, 'network', fail)
    c = client(monkeypatch); h = {'Authorization':'Bearer synthetic'}
    result = c.post('/api/bonus-tracker/simplefin/reconnect', headers=h,
                    json={'setup_token':TOKEN})
    assert result.status_code == 200
    assert result.json()['status'] == 'attention'
    stored = b.read_state('owner')[0]
    assert stored['access'] == ACCESS
    assert 't1' in stored['transactions'][k]

def test_mapping_changes_clear_previous_cycle_and_keep_other_accounts(setup,monkeypatch):
    s=seed();b.merge(s,data(account(transactions=[txn()])),NOW);b.write_state('owner',s,None)
    c=client(monkeypatch);k=b.account_key(account())
    r=c.put('/api/bonus-tracker/simplefin/selection',headers={'Authorization':'Bearer synthetic'},json={'mappings':{k:{'entryId':'cycle2','bank':'Synthetic CU','opened':'2026-10-16'}}})
    assert r.status_code==200 and not r.json()['transactions']

def test_error_sanitization():
    assert b.clean('<script>https://name:secret@example.test/foo')=='<script>[link removed]'


def test_lease_and_stale_writes_cannot_resurrect_disconnected_state(setup):
    b.write_state('owner',seed(),None)
    s,tag=b.reserve('owner','sync')
    with pytest.raises(HTTPException) as e:b.reserve('owner','disconnect')
    assert e.value.status_code==409
    b.finish('owner',s,tag)
    old,tag=b.read_state('owner')
    b.write_state('owner',{'version':1,'accounts':[],'mappings':{},'transactions':{}},tag)
    with pytest.raises(HTTPException):b.write_state('owner',old,tag)
    assert 'access' not in b.read_state('owner')[0]


def test_wrong_key_fails_closed(setup,monkeypatch):
    b.write_state('owner',seed(),None)
    monkeypatch.setenv('SIMPLEFIN_ENCRYPTION_KEY',Fernet.generate_key().decode())
    with pytest.raises(HTTPException) as e:b.read_state('owner')
    assert e.value.status_code==503


def test_network_never_follows_redirects_or_sends_credentials_in_url(setup,monkeypatch):
    called=[]
    def network(url,**kw):
        called.append((url,kw));return json.dumps(data(account())).encode()
    monkeypatch.setattr(b,'network',network)
    b.get_data(ACCESS,[{'id':'a'}])
    assert 'secret' not in called[0][0] and '@' not in called[0][0]
    assert called[0][1]['auth']==('synthetic','secret')
    assert ('account','a') in called[0][1]['params']
    assert ('pending','1') in called[0][1]['params']
    assert int(dict(called[0][1]['params'])['start-date']) == NOW-90*b.DAY


def test_private_routes_disable_caching(setup,monkeypatch):
    c=client(monkeypatch)
    r=c.get('/api/bonus-tracker/simplefin',headers={'Authorization':'Bearer synthetic'})
    assert r.headers['cache-control']=='no-store'


def test_invalid_date_and_unknown_accounts_rejected(setup,monkeypatch):
    b.write_state('owner',seed(),None);c=client(monkeypatch);h={'Authorization':'Bearer synthetic'}
    for opened,key in [('2026-02-30',b.account_key(account())),('2026-10-01','unknown')]:
        r=c.put('/api/bonus-tracker/simplefin/selection',headers=h,json={'mappings':{key:{'entryId':'other','bank':'Other','opened':opened}}})
        assert r.status_code in (400,422)


def test_monitor_unlinked_account_sync_and_opt_out_deletes_history(setup, monkeypatch):
    s = seed()
    k = b.account_key(account())
    s['mappings'] = {}
    b.write_state('owner', s, None)
    c = client(monkeypatch)
    p = '/api/bonus-tracker/simplefin'
    h = {'Authorization': 'Bearer synthetic'}
    fetched = []
    def fake_get_data(access, selected):
        fetched.append([a['id'] for a in selected])
        return data(account(transactions=[txn('activity', '-12.50', 'Coffee shop')]))
    monkeypatch.setattr(b, 'get_data', fake_get_data)
    assert c.post(p + '/sync', headers=h).json().get('lastSuccess') is None
    enrolled = c.put(p + '/monitoring', headers=h, json={'accounts': [k]})
    assert enrolled.status_code == 200
    assert enrolled.json()['monitoredAccounts'] == [k]
    synced = c.post(p + '/sync', headers=h).json()
    assert fetched == [['a']]
    assert synced['transactions'][k][b.public_ref(k,'activity')]['kind'] == 'withdrawal'
    assert synced['lastSuccess'] == NOW
    c.post(p + '/sync', headers=h)
    assert len(fetched) == 1  # Same 24-hour provider budget.
    opted_out = c.put(p + '/monitoring', headers=h, json={'accounts': []})
    assert opted_out.status_code == 200
    assert opted_out.json()['monitoredAccounts'] == []
    assert k not in opted_out.json()['transactions']


def test_monitored_account_survives_bonus_unlink_without_reclassifying(setup, monkeypatch):
    s = seed()
    k = b.account_key(account())
    s['monitoredAccounts'] = [k]
    b.merge(s, data(account(transactions=[txn()])), NOW)
    b.write_state('owner', s, None)
    c = client(monkeypatch)
    p = '/api/bonus-tracker/simplefin'
    h = {'Authorization': 'Bearer synthetic'}
    result = c.put(p + '/selection', headers=h, json={'mappings': {}})
    assert result.status_code == 200
    assert result.json()['mappings'] == {}
    assert k in result.json()['transactions']
    assert result.json()['transactions'][k][b.public_ref(k,'t1')]['kind'] == 'possible_bonus'


def test_monitor_rejects_unknown_duplicate_or_unconnected_accounts(setup, monkeypatch):
    s = seed()
    k = b.account_key(account())
    b.write_state('owner', s, None)
    c = client(monkeypatch)
    p = '/api/bonus-tracker/simplefin/monitoring'
    h = {'Authorization': 'Bearer synthetic'}
    for keys in [[k, k], ['bad-key'], ['f' * 64]]:
        result = c.put(p, headers=h, json={'accounts': keys})
        assert result.status_code in (400, 422)
    assert b.read_state('owner')[0].get('monitoredAccounts', []) == []


def test_public_state_hides_provider_identifiers_and_secret(setup):
    s = seed()
    public = b.public_state(s)
    assert public['accounts'][0]['key'] == b.account_key(account())
    assert 'id' not in public['accounts'][0]
    assert 'connection' not in public['accounts'][0]
    assert public['monitoredAccounts'] == []
    assert ACCESS not in json.dumps(public)
    assert public['mappings'] == s['mappings']


def test_missing_monitored_account_flags_attention_without_deleting_evidence(setup):
    s = seed()
    k = b.account_key(account())
    s['mappings'] = {}
    s['monitoredAccounts'] = [k]
    b.merge(s, data(account(transactions=[txn()])), NOW)
    b.merge(s, data(), NOW + 1)
    assert s['status'] == 'attention'
    assert s['missingAccounts'] == [k]
    assert k in s['transactions']


def test_profiles_available_balance_history_and_net_worth_inputs_are_owner_private(setup, monkeypatch):
    s = seed(); k = b.account_key(account()); b.merge(s, data(account()), NOW); b.write_state('owner', s, None)
    c = client(monkeypatch); h = {'Authorization':'Bearer synthetic'}; p = '/api/bonus-tracker/simplefin'
    r = c.put(p + '/account-profiles', headers=h, json={'profiles':{k:{'kind':'checking','order':3,'balanceMode':'asset'}}})
    assert r.status_code == 200
    payload = r.json()
    assert payload['accountProfiles'][k] == {'kind':'checking','order':3,'balanceMode':'asset'}
    assert payload['accounts'][0]['availableBalance'] == '45.00'
    assert payload['balanceHistory'][k][-1]['balance'] == '50.00'
    raw = setup.items[b.key('owner')][0]
    assert b'checking' not in raw and b'45.00' not in raw
    for kind in ('credit_card','brokerage','retirement','loan','mortgage','other_asset','other_liability'):
        assert c.put(p + '/account-profiles', headers=h, json={'profiles':{k:{'kind':kind,'order':0,'balanceMode':'exclude'}}}).status_code == 200


def test_transaction_enrichment_review_and_provider_revision_invalidates_review(setup, monkeypatch):
    s = seed(); k = b.account_key(account()); s['monitoredAccounts'] = [k]
    b.merge(s, data(account(transactions=[txn('coffee','-6.25','POS Coffee 123456'),
                                          txn('salary','900','Employer Direct Deposit')])), NOW)
    b.write_state('owner', s, None)
    c = client(monkeypatch); h = {'Authorization':'Bearer synthetic'}; p = '/api/bonus-tracker/simplefin'
    public = c.get(p, headers=h).json(); ref = b.public_ref(k,'coffee')
    assert public['transactions'][k][ref]['merchant'] == 'Coffee'
    assert public['transactions'][k][b.public_ref(k,'salary')]['signals'] == ['possible_direct_deposit']
    reviewed = c.put(p + '/transaction-review', headers=h,
                     json={'account':k,'transaction':ref,'category':'dining'})
    assert reviewed.status_code == 200
    assert reviewed.json()['transactions'][k][ref]['category'] == 'dining'
    assert reviewed.json()['transactions'][k][ref]['categorySource'] == 'user'
    stored, tag = b.read_state('owner')
    b.merge(stored, data(account(transactions=[txn('coffee','-7.00','POS Coffee 123456')])), NOW+1)
    b.write_state('owner', stored, tag)
    changed = c.get(p, headers=h).json()['transactions'][k][ref]
    assert changed['categorySource'] == 'automatic' and changed['changedAt'] == NOW+1
    assert changed['revisions'][-1]['amount'] == '-6.25'
    serialized = json.dumps(c.get(p, headers=h).json())
    assert '"coffee"' not in serialized and '"salary"' not in serialized
    assert 'transactionReviews' not in serialized and 'audit' not in serialized


def test_high_confidence_internal_transfer_and_recurring_detection(setup):
    left = account('one','a',[txn('out','-100','Transfer payment')])
    right = account('two','b',[txn('in','100','Transfer payment')])
    recurring = account('three','c',[txn('m1','-12.99','STREAM SERVICE'),
                                     {**txn('m2','-12.99','STREAM SERVICE'),'posted':NOW-30*b.DAY}])
    s = b.empty_state(); s['accounts'] = b.normalize_accounts(data(left,right,recurring))
    s['monitoredAccounts'] = [a['key'] for a in s['accounts']]
    b.merge(s, data(left,right,recurring), NOW)
    public = b.public_state(s)
    lkey, rkey, skey = [a['key'] for a in s['accounts']]
    assert public['transactions'][lkey][b.public_ref(lkey,'out')]['confidence'] == 'high'
    assert 'matched_internal_transfer' in public['transactions'][rkey][b.public_ref(rkey,'in')]['signals']
    assert public['transactions'][skey][b.public_ref(skey,'m1')]['category'] == 'subscription'
