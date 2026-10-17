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

def test_invalid_amount_is_skipped_and_valid_rows_are_retained(setup,monkeypatch):
    s=seed();b.write_state('owner',s,None)
    monkeypatch.setattr(b,'get_data',lambda *a,**kw:data(account(transactions=[
        txn(ident='bad',amount='NaN'),txn(ident='good',amount='2.00',description='Coffee shop')])))
    result=b.sync('owner')
    k=b.account_key(account())
    assert result['status']=='connected'
    assert len(result['transactions'][k]) == 1
    assert 'invalid transaction' in result['warnings'][0]
    assert b.read_state('owner')[0]['transactions'][k]['good']['amount']=='2.00'

def test_sync_cadence_and_no_unselected_fetch(setup,monkeypatch):
    s=seed();b.write_state('owner',s,None);calls=[]
    def fetch(access,selected):calls.append(selected);return data(account(transactions=[txn()]))
    monkeypatch.setattr(b,'get_data',fetch)
    assert b.sync('owner')['connected']
    b.sync('owner');assert len(calls)==1 and len(calls[0])==1
    s,tag=b.read_state('owner');s['mappings']={};s['nextSyncAt']=0;b.write_state('owner',s,tag)
    b.sync('owner');assert len(calls)==2  # Accounts still sync without a bonus mapping.

def test_failed_sync_is_throttled_and_retains_evidence(setup,monkeypatch):
    s=seed();b.merge(s,data(account(transactions=[txn()])),NOW);b.write_state('owner',s,None)
    def fail(*a):raise HTTPException(403,'Access rejected')
    monkeypatch.setattr(b,'get_data',fail);r=b.sync('owner')
    assert r['error']=='Access rejected' and r['transactions'] and r['nextSyncAt']==NOW+3600

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
    assert int(dict(called[0][1]['params'])['start-date']) == NOW-89*b.DAY
    assert int(dict(called[0][1]['params'])['end-date']) == NOW
    assert int(dict(called[0][1]['params'])['end-date'])-int(dict(called[0][1]['params'])['start-date']) <= 89*b.DAY


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
    synced = c.post(p + '/sync', headers=h).json()
    assert synced['monitoredAccounts'] == [k], 'First connection must enable transaction history automatically'
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
    assert public['transactions'][skey][b.public_ref(skey,'m1')]['category'] == 'unreviewed'
    assert 'possible_recurring' in public['transactions'][skey][b.public_ref(skey,'m1')]['signals']


def test_ambiguous_same_amount_transfer_remains_unconfirmed(setup):
    # One receiving credit cannot be counted as evidence for multiple outgoing transfers.
    left = account('one', 'left', [txn('out1', '-100', 'Transfer payment')])
    right = account('two', 'right', [txn('out2', '-100', 'Transfer payment')])
    receiving = account('three', 'receive', [txn('in', '100', 'Transfer payment')])
    s = b.empty_state(); s['accounts'] = b.normalize_accounts(data(left, right, receiving))
    s['monitoredAccounts'] = [a['key'] for a in s['accounts']]
    b.merge(s, data(left, right, receiving), NOW)
    public = b.public_state(s)
    for account_row, txn_id in [(left, 'out1'), (right, 'out2'), (receiving, 'in')]:
        key = b.account_key(account_row)
        row = public['transactions'][key][b.public_ref(key, txn_id)]
        assert row['confidence'] != 'high'
        assert 'matched_internal_transfer' not in row['signals']


def test_internal_transfer_matches_are_unique_and_respect_dates(setup):
    left = account('one', 'left', [
        txn('out1', '-125', 'Transfer to own savings'),
        {**txn('out2', '-125', 'Transfer to own savings'), 'posted': NOW - 14 * b.DAY},
    ])
    right = account('two', 'right', [
        txn('in1', '125', 'Transfer from own checking'),
        {**txn('in2', '125', 'Transfer from own checking'), 'posted': NOW - 14 * b.DAY},
    ])
    s = b.empty_state(); s['accounts'] = b.normalize_accounts(data(left, right))
    s['monitoredAccounts'] = [a['key'] for a in s['accounts']]
    b.merge(s, data(left, right), NOW)
    public = b.public_state(s)
    for source, ident in [(left, 'out1'), (left, 'out2'), (right, 'in1'), (right, 'in2')]:
        key = b.account_key(source)
        row = public['transactions'][key][b.public_ref(key, ident)]
        assert row['category'] == 'transfer' and row['confidence'] == 'high'
        assert row['signals'].count('matched_internal_transfer') == 1


def test_automatic_transfer_never_overrides_user_review_or_pending(setup):
    debit = account('one', 'left', [txn('out', '-200', 'Transfer payment')])
    credit = account('two', 'right', [txn('in', '200', 'Transfer payment')])
    s = b.empty_state(); s['accounts'] = b.normalize_accounts(data(debit, credit))
    s['monitoredAccounts'] = [a['key'] for a in s['accounts']]
    b.merge(s, data(debit, credit), NOW)
    left, right = b.account_key(debit), b.account_key(credit)
    s['transactionReviews'] = {left: {'out': {'category': 'fees'}}}
    public = b.public_state(s)
    saved = public['transactions'][left][b.public_ref(left, 'out')]
    candidate = public['transactions'][right][b.public_ref(right, 'in')]
    assert saved['category'] == 'fees' and saved['categorySource'] == 'user'
    assert 'matched_internal_transfer' not in saved['signals']
    assert 'matched_internal_transfer' not in candidate['signals']
    s['transactionReviews'] = {}
    s['transactions'][right]['in']['pending'] = True
    public = b.public_state(s)
    assert 'matched_internal_transfer' not in public['transactions'][left][b.public_ref(left, 'out')]['signals']


def test_generic_payment_and_bonus_are_not_internal_transfer_evidence(setup):
    outgoing = account('one', 'left', [txn('out', '-300', 'Transfer payment')])
    payroll = account('two', 'right', [txn('income', '300', 'Employer Payroll Payment')])
    s = b.empty_state(); s['accounts'] = b.normalize_accounts(data(outgoing, payroll))
    s['monitoredAccounts'] = [a['key'] for a in s['accounts']]
    b.merge(s, data(outgoing, payroll), NOW)
    public = b.public_state(s)
    lk, rk = b.account_key(outgoing), b.account_key(payroll)
    assert 'matched_internal_transfer' not in public['transactions'][lk][b.public_ref(lk, 'out')]['signals']
    row = public['transactions'][rk][b.public_ref(rk, 'income')]
    assert row['category'] == 'income'
    assert 'matched_internal_transfer' not in row['signals']


def test_evidence_inbox_review_is_owner_private_and_exact_cycle(setup, monkeypatch):
    s = seed()
    account_row = account(transactions=[
        txn('salary', '500', 'Payroll Direct Deposit'),
        txn('payout', '200', 'New Account Promotional Bonus'),
        txn('fee', '-8', 'Monthly Service Fee')])
    key = b.account_key(account_row)
    b.merge(s, data(account_row), NOW)
    b.write_state('owner', s, None)
    c = client(monkeypatch)
    h = {'Authorization': 'Bearer synthetic'}
    route = '/api/bonus-tracker/simplefin/evidence-review'
    review = {'account': key, 'transaction': b.public_ref(key, 'salary'),
              'entryId': 'cycle1', 'bank': 'Synthetic CU', 'opened': '2026-10-01',
              'decision': 'reviewed', 'requirementId': 'dd_minimum'}
    result = c.put(route, headers=h, json=review)
    assert result.status_code == 200, result.text
    receipt = result.json()['evidenceReviews'][key][review['transaction']]
    assert receipt['status'] == 'reviewed'
    assert receipt['requirementId'] == 'dd_minimum'
    assert 'access' not in result.json()
    assert 'salary' not in json.dumps(result.json()['evidenceReviews'])
    for field, value in [('entryId', 'other-cycle'), ('bank', 'Other CU'),
                         ('opened', '2026-10-02')]:
        bad = c.put(route, headers=h, json={**review, field: value})
        assert bad.status_code == 400
        assert b.read_state('owner')[0]['evidenceReviews'][key]['salary']['status'] == 'reviewed'
    wrong_account = c.put(route, headers=h, json={**review, 'account': 'f' * 64})
    assert wrong_account.status_code == 400
    wrong_transaction = c.put(route, headers=h, json={**review, 'transaction': 'f' * 64})
    assert wrong_transaction.status_code == 400
    invalid_kind = c.put(route, headers=h, json={**review,
                            'transaction': b.public_ref(key, 'payout')})
    assert invalid_kind.status_code == 400
    fee_matched_to_dd = c.put(route, headers=h, json={**review,
                            'transaction': b.public_ref(key, 'fee')})
    assert fee_matched_to_dd.status_code == 400
    unauthenticated = c.put(route, json=review)
    assert unauthenticated.status_code == 401
    assert c.get('/api/bonus-tracker/simplefin', headers=h).json()['evidenceReviews'][key][review['transaction']] == receipt
    cleared = c.put(route, headers=h, json={**review, 'decision': 'clear',
                                            'requirementId': None})
    assert cleared.status_code == 200
    assert review['transaction'] not in cleared.json().get('evidenceReviews', {}).get(key, {})


def test_inbox_dismissals_survive_resync_and_invalidate_provider_corrections(setup, monkeypatch):
    s = seed()
    a = account(transactions=[txn('salary', '500', 'Payroll Direct Deposit')])
    key = b.account_key(a)
    b.merge(s, data(a), NOW)
    b.write_state('owner', s, None)
    c = client(monkeypatch)
    url = '/api/bonus-tracker/simplefin/evidence-review'
    h = {'Authorization': 'Bearer synthetic'}
    body = {'account': key, 'transaction': b.public_ref(key, 'salary'),
            'entryId': 'cycle1', 'bank': 'Synthetic CU', 'opened': '2026-10-01',
            'decision': 'dismissed'}
    first = c.put(url, headers=h, json=body)
    assert first.status_code == 200
    s, tag = b.read_state('owner')
    assert s['evidenceReviews'][key]['salary']['status'] == 'dismissed'
    b.merge(s, data(a), NOW)
    b.write_state('owner', s, tag)
    assert b.read_state('owner')[0]['evidenceReviews'][key]['salary']['status'] == 'dismissed'
    changed = account(transactions=[txn('salary', '550', 'Payroll Direct Deposit corrected')])
    s, tag = b.read_state('owner')
    b.merge(s, data(changed), NOW)
    assert 'salary' not in s.get('evidenceReviews', {}).get(key, {})
    b.write_state('owner', s, tag)
    assert not c.get('/api/bonus-tracker/simplefin', headers=h).json().get('evidenceReviews', {}).get(key)


def test_evidence_reviews_dont_leak_across_bonus_cycles_or_unlink(setup, monkeypatch):
    s = seed()
    a = account(transactions=[txn('salary', '500', 'Payroll Direct Deposit')])
    key = b.account_key(a)
    b.merge(s, data(a), NOW)
    b.write_state('owner', s, None)
    c = client(monkeypatch)
    h = {'Authorization': 'Bearer synthetic'}
    url = '/api/bonus-tracker/simplefin/evidence-review'
    body = {'account': key, 'transaction': b.public_ref(key, 'salary'),
            'entryId': 'cycle1', 'bank': 'Synthetic CU', 'opened': '2026-10-01',
            'decision': 'reviewed', 'requirementId': 'dd'}
    assert c.put(url, headers=h, json=body).status_code == 200
    next_cycle = {'entryId': 'cycle2', 'bank': 'Synthetic CU', 'opened': '2026-10-09'}
    remapped = c.put('/api/bonus-tracker/simplefin/selection', headers=h,
                    json={'mappings': {key: next_cycle}})
    assert remapped.status_code == 200
    assert not remapped.json().get('evidenceReviews')
    assert c.put(url, headers=h, json=body).status_code == 400
    # Re-linking a new cycle intentionally clears old evidence until the next sync.
    assert c.put(url, headers=h, json={**body, **next_cycle}).status_code == 400
    next_state, tag = b.read_state('owner')
    b.merge(next_state, data(a), NOW)
    b.write_state('owner', next_state, tag)
    assert c.put(url, headers=h, json={**body, **next_cycle}).status_code == 200
    unmapped = c.put('/api/bonus-tracker/simplefin/selection', headers=h,
                     json={'mappings': {}})
    assert unmapped.status_code == 200
    assert not unmapped.json().get('evidenceReviews')
    assert not b.read_state('owner')[0]['evidenceReviews']


def test_evidence_inbox_only_accepts_posted_supported_transactions(setup, monkeypatch):
    s = seed()
    a = account(transactions=[
        txn('pending', '500', 'Payroll Direct Deposit', True),
        txn('unrelated', '-100', 'Coffee at a cafe'),
        {**txn('old', '200', 'Payroll Direct Deposit'), 'posted': 1790000000}])
    k = b.account_key(a)
    b.merge(s, data(a), NOW)
    b.write_state('owner', s, None)
    c = client(monkeypatch)
    base = {'account': k, 'entryId': 'cycle1', 'bank': 'Synthetic CU',
            'opened': '2026-10-01', 'decision': 'reviewed'}
    for raw in ['pending', 'unrelated', 'old']:
        response = c.put('/api/bonus-tracker/simplefin/evidence-review',
                         headers={'Authorization': 'Bearer synthetic'},
                         json={**base, 'transaction': b.public_ref(k, raw)})
        assert response.status_code == 400, (raw, response.text)


def test_optional_bonus_product_link_preserves_same_cycle_transactions(setup, monkeypatch):
    s = seed()
    a = account(transactions=[txn('pay', '500', 'Payroll Direct Deposit')])
    key = b.account_key(a)
    b.merge(s, data(a), NOW)
    b.write_state('owner', s, None)
    c = client(monkeypatch)
    h = {'Authorization': 'Bearer synthetic'}
    original = {'entryId': 'cycle1', 'bank': 'Synthetic CU', 'opened': '2026-10-01'}
    path = '/api/bonus-tracker/simplefin/selection'
    changed = c.put(path, headers=h, json={'mappings': {key: {**original, 'productId': 'checking'}}})
    assert changed.status_code == 200, changed.text
    assert changed.json()['mappings'][key]['productId'] == 'checking'
    assert b.public_ref(key, 'pay') in changed.json()['transactions'][key]
    # Changes to product assignment do not delete transaction evidence.
    changed_again = c.put(path, headers=h, json={'mappings': {key: {**original, 'productId': 'share'}}})
    assert changed_again.status_code == 200
    assert b.public_ref(key, 'pay') in changed_again.json()['transactions'][key]
    # Preserve original legacy mapping shape for existing users.
    legacy = c.put(path, headers=h, json={'mappings': {key: original}})
    assert legacy.status_code == 200
    assert legacy.json()['mappings'][key] == original
    assert b.public_ref(key, 'pay') in legacy.json()['transactions'][key]
    # A change of bonus cycle still isolates its saved evidence.
    new_cycle = c.put(path, headers=h, json={'mappings': {key: {**original, 'opened': '2026-10-10'}}})
    assert new_cycle.status_code == 200
    assert not new_cycle.json().get('transactions', {}).get(key)


def test_product_link_input_validation_and_scope(setup, monkeypatch):
    s = seed()
    b.write_state('owner', s, None)
    c = client(monkeypatch)
    h = {'Authorization': 'Bearer synthetic'}
    k = b.account_key(account())
    original = {'entryId': 'cycle1', 'bank': 'Synthetic CU', 'opened': '2026-10-01'}
    path = '/api/bonus-tracker/simplefin/selection'
    for invalid in ('Checking', 'checking savings', 'a'*41, '../checking', ''):
        response = c.put(path, headers=h, json={'mappings': {k: {**original, 'productId': invalid}}})
        assert response.status_code == 422
    unknown = c.put(path, headers=h,
                    json={'mappings': {'f'*64: {**original, 'productId': 'checking'}}})
    assert unknown.status_code == 400
    assert c.put(path, json={'mappings': {k: {**original, 'productId': 'checking'}}}).status_code == 401



def test_product_assignment_preserves_review_decisions_but_resets_requirement_associations(
        setup, monkeypatch):
    """Choosing share savings cannot erase bank evidence or attach checking DD progress."""
    s = seed()
    a = account(transactions=[
        txn('salary', '500', 'Payroll Direct Deposit'),
        txn('fee', '-8', 'Monthly Service Fee')])
    key = b.account_key(a)
    b.merge(s, data(a), NOW)
    b.write_state('owner', s, None)
    c = client(monkeypatch)
    h = {'Authorization': 'Bearer synthetic'}
    mapping = {'entryId': 'cycle1', 'bank': 'Synthetic CU', 'opened': '2026-10-01'}
    endpoint = '/api/bonus-tracker/simplefin/evidence-review'
    sel = '/api/bonus-tracker/simplefin/selection'
    base = {'account': key, **mapping}
    payday = {**base, 'transaction': b.public_ref(key, 'salary'),
              'decision': 'reviewed', 'requirementId': 'dd_main'}
    assert c.put(endpoint, headers=h, json=payday).status_code == 200
    fee = {**base, 'transaction': b.public_ref(key, 'fee'),
           'decision': 'dismissed'}
    assert c.put(endpoint, headers=h, json=fee).status_code == 200

    # Adding an account-product label retains the review state, but drops
    # product-specific requirement matches pending explicit reconfirmation.
    chosen = c.put(sel, headers=h,
                   json={'mappings': {key: {**mapping, 'productId': 'checking'}}})
    assert chosen.status_code == 200, chosen.text
    receipts = chosen.json()['evidenceReviews'][key]
    assert receipts[payday['transaction']]['status'] == 'reviewed'
    assert receipts[payday['transaction']]['requirementId'] is None
    assert receipts[fee['transaction']]['status'] == 'dismissed'
    assert payday['transaction'] in chosen.json()['transactions'][key]

    # The optional product field must not break submitting a new review.
    after = c.put(endpoint, headers=h, json=payday)
    assert after.status_code == 200, after.text
    assert after.json()['evidenceReviews'][key][payday['transaction']]['requirementId'] == 'dd_main'

    # Idempotent saving of the same product leaves explicit review associations intact.
    unchanged = c.put(sel, headers=h,
                      json={'mappings': {key: {**mapping, 'productId': 'checking'}}})
    assert unchanged.status_code == 200
    assert unchanged.json()['evidenceReviews'][key][payday['transaction']]['requirementId'] == 'dd_main'

    # A correction from checking to companion savings must not auto-attach the
    # reviewed checking deposit to the same promotion requirement.
    corrected = c.put(sel, headers=h,
                      json={'mappings': {key: {**mapping, 'productId': 'share'}}})
    assert corrected.status_code == 200
    assert corrected.json()['evidenceReviews'][key][payday['transaction']]['status'] == 'reviewed'
    assert corrected.json()['evidenceReviews'][key][payday['transaction']]['requirementId'] is None
    assert corrected.json()['evidenceReviews'][key][fee['transaction']]['status'] == 'dismissed'
    assert b.Decimal(corrected.json()['transactions'][key][payday['transaction']]['amount']) == b.Decimal('500.00')

    # Different cycles must never inherit the old review receipts.
    changed_cycle = c.put(sel, headers=h, json={
        'mappings': {key: {**mapping, 'opened': '2026-10-11', 'productId': 'checking'}}})
    assert changed_cycle.status_code == 200
    assert not changed_cycle.json().get('evidenceReviews', {}).get(key)
    assert b.read_state('owner')[0]['evidenceReviews'] == {}


def test_pending_zero_posted_uses_transacted_at_and_preserves_cleared_audit(setup):
    s=seed(); k=b.account_key(account())
    pending={**txn('pending','-0.21','ACCTVERIFY trial dep',pending=True),
             'posted':0,'transacted_at':NOW-100}
    b.merge(s,data(account(transactions=[pending,txn('posted','-8','Coffee cafe')])),NOW)
    assert s['transactions'][k]['pending']['posted']==NOW-100
    before=b.public_state(s)
    assert len(before['transactions'][k])==2
    b.merge(s,data(account(transactions=[txn('posted','-8','Coffee cafe')])),NOW+1)
    assert s['transactions'][k]['pending']['pendingClearedAt']==NOW+1
    assert len(b.public_state(s)['transactions'][k])==1
    assert 'pending' in s['transactions'][k], 'Clearing a pending row must never erase saved financial evidence'


@pytest.mark.parametrize('description, amount', [
    ('ACCTVERIFY', '0.15'), ('TRIAL DEP', '-0.11'),
    ('RTPTSTSTTL', '0.25'), ('RTP TST STTL', '-0.33'),
    ('MTG PYMT $0.00', '0.00'), ('PRENOTE', '0.00'),
])
def test_verification_patterns_are_not_income_or_expenses(description,amount):
    category, confidence, signals=b.automatic_category({
        'pending':False,'amount':amount,'description':description,
        'kind':b.classify(amount,description)})
    assert category=='verification'
    assert confidence=='high'
    assert signals==[]


def test_user_learned_category_precedence_over_automatic_matching(setup):
    """An explicit apply-to-similar correction wins over guessed transfers."""
    debit = account('one', 'left', [txn('out', '-200', 'Transfer payment')])
    credit = account('two', 'right', [txn('in', '200', 'Transfer payment')])
    s = b.empty_state()
    s['accounts'] = b.normalize_accounts(data(debit, credit))
    s['monitoredAccounts'] = [a['key'] for a in s['accounts']]
    b.merge(s, data(debit, credit), NOW)
    left, right = b.account_key(debit), b.account_key(credit)
    s['merchantRules'] = {
        b.cache_key('Transfer payment'): {'category': 'other', 'updatedAt': NOW}
    }
    public = b.public_state(s)
    for account_key, raw in [(left, 'out'), (right, 'in')]:
        row = public['transactions'][account_key][b.public_ref(account_key, raw)]
        assert row['category'] == 'other'
        assert row['categorySource'] == 'user_rule'
        assert 'matched_internal_transfer' not in row['signals']

    # Matching microdeposits must also defer to a saved user merchant rule.
    small = account('one', 'micro', [
        txn('first', '0.17', 'Trial deposit synthetic'),
        txn('second', '0.29', 'Trial deposit synthetic')
    ])
    t = b.empty_state()
    t['accounts'] = b.normalize_accounts(data(small))
    t['monitoredAccounts'] = [b.account_key(small)]
    b.merge(t, data(small), NOW)
    t['merchantRules'] = {
        b.cache_key('Trial deposit synthetic'): {'category': 'other', 'updatedAt': NOW}
    }
    p = b.public_state(t)
    for ident in ('first', 'second'):
        row = p['transactions'][b.account_key(small)][b.public_ref(b.account_key(small), ident)]
        assert row['category'] == 'other'
        assert row['categorySource'] == 'user_rule'


@pytest.mark.parametrize('bank_description', [
    'Transfer', 'Transaction', 'Payment', 'POS Transaction'
])
def test_generic_merchant_title_keeps_original_bank_description(bank_description):
    assert b.merchant_name(bank_description) == bank_description


def test_monthly_card_and_transfer_debits_do_not_become_subscriptions():
    account="a"*64
    s=b.empty_state()
    s["transactions"]={account:{
        "card1":{"id":"card1","description":"Capital One CRCARDPMT","amount":"-200.00","posted":NOW-35*b.DAY,"pending":False,"kind":"withdrawal"},
        "card2":{"id":"card2","description":"Capital One CRCARDPMT","amount":"-200.00","posted":NOW-5*b.DAY,"pending":False,"kind":"withdrawal"},
        "move1":{"id":"move1","description":"External account transfer","amount":"-1000.00","posted":NOW-35*b.DAY,"pending":False,"kind":"withdrawal"},
        "move2":{"id":"move2","description":"External account transfer","amount":"-1000.00","posted":NOW-5*b.DAY,"pending":False,"kind":"withdrawal"}
    }}
    result={}
    b.enrich_transactions(s,result)
    rows=list(result["transactions"][account].values())
    assert all(row["category"]=="transfer" for row in rows)
    assert all("possible_recurring" in row["signals"] for row in rows)
    s["transactionReviews"]={account:{"card1":{"category":"subscription"}}}
    result={}
    b.enrich_transactions(s,result)
    assert next(row for row in result["transactions"][account].values() if row["description"]=="Capital One CRCARDPMT" and row["posted"]==NOW-35*b.DAY)["category"]=="subscription"


def test_simplefin_preserves_brokerage_type_metadata():
    raw={"accounts":[{"id":"acct1","conn_id":"conn1","name":"Robinhood individual",
                       "type":"investment","subtype":"individual","balance":"100","currency":"USD"}]}
    found=b.normalize_accounts(raw)
    assert found[0]["accountType"]=="investment"
    assert found[0]["accountSubtype"]=="individual"


def test_ai_sync_metadata_contains_only_counts():
    state=b.empty_state()
    state['financeAILastSync']={'at':1792134000,'attempted':51,'categorized':46,'failed':5}
    public=b.public_state(state)
    assert public['financeAILastSync']==state['financeAILastSync']
    assert public['financeVersion']=='3.4.81'


def test_exact_nonround_reciprocal_move_matches_between_accounts_same_bank():
    primary,secondary='a'*64,'b'*64
    s=b.empty_state()
    s['transactions']={primary:{'out':{'description':'To Personal Checking','amount':'-7379.04',
      'posted':NOW-80000,'pending':False,'kind':'withdrawal'}},
      secondary:{'in':{'description':'From Personal Savings','amount':'7379.04',
      'posted':NOW-78000,'pending':False,'kind':'deposit'}}}
    output={}
    b.enrich_transactions(s,output)
    for account in (primary,secondary):
        row=next(iter(output['transactions'][account].values()))
        assert row['category']=='transfer'
        assert 'matched_internal_transfer' in row['signals']
    s['transactionReviews']={primary:{'out':{'category':'shopping'}}}
    b.enrich_transactions(s,output:={})
    assert next(iter(output['transactions'][primary].values()))['category']=='shopping'
    assert next(iter(output['transactions'][primary].values()))['categorySource']=='user'


def test_saved_automatic_old_subscription_reclassified_without_mutating_ledger():
    a='a'*64;s=b.empty_state()
    s['transactions']={a:{'old':{'description':'To Brokerage','amount':'-2000',
       'posted':NOW-100,'pending':False,'kind':'withdrawal',
       'category':'subscription','categorySource':'automatic'}}}
    before=s['transactions'][a]['old'].copy()
    b.enrich_transactions(s,out:={})
    row=next(iter(out['transactions'][a].values()))
    assert row['category']=='transfer'
    assert 'investment_transfer' in row['signals']
    assert s['transactions'][a]['old']==before

def test_date_window_is_bounded_for_discovery_and_activity(setup, monkeypatch):
    requests=[]
    def fake_network(*args,**kwargs):
        request_params=dict(kwargs['params'])
        requests.append(request_params)
        return json.dumps(data(account())).encode()
    monkeypatch.setattr(b,'network',fake_network)
    b.get_data(ACCESS, balances=True)
    b.get_data(ACCESS,[{'id':'a'}])
    for params in requests:
        assert int(params['end-date'])==NOW
        assert int(params['start-date'])==NOW-89*b.DAY
        assert 0<int(params['end-date'])-int(params['start-date'])<=90*b.DAY


def test_capped_notice_is_not_attention_and_old_warning_clears(setup):
    initial=seed()
    initial['warnings']=['Requested date range exceeds limit of 90 days and was capped.']
    initial['status']='attention'
    initial['error']='Old connection timeout'
    k=b.account_key(account())
    initial['monitoredAccounts']=[k]
    payload=data(account(transactions=[txn('fresh','-4.00','Coffee Shop')]))
    payload['errlist']=[{'conn_id':'one','msg':'Requested date range exceeds limit of 90 days and was capped.'}]
    merged=b.merge(copy.deepcopy(initial),payload,NOW)
    assert merged['status']=='attention'  # old error until successful request clears it
    assert merged['warnings']==[]
    assert len(merged['notices'])==1
    assert merged['institutionIssues']=={}
    merged.pop('error',None)
    b.set_provider_status(merged)
    assert merged['status']=='connected'
    assert 'attentionKind' not in b.public_state(merged)['accounts'][0]
    assert 'fresh' in merged['transactions'][k]


def test_per_bank_login_issue_does_not_flag_other_institution(setup):
    other=account(conn='two',ident='b')
    initial=seed()
    initial['accounts']=b.normalize_accounts({
        'accounts':[account(),other],
        'connections':[{'conn_id':'one','name':'Chase'},{'conn_id':'two','name':'Robinhood'}]})
    initial['monitoredAccounts']=[a['key'] for a in initial['accounts']]
    payload={'accounts':[account(),other],
             'connections':[{'conn_id':'one','name':'Chase'},{'conn_id':'two','name':'Robinhood'}],
             'errlist':[{'conn_id':'one','msg':'Chase bank login required'}]}
    merged=b.merge(initial,payload,NOW)
    result=b.public_state(merged)
    assert result['status']=='attention'
    bank_map={row['bank']:row for row in result['accounts']}
    assert bank_map['Chase']['attentionKind']=='bank_login'
    assert bank_map['Chase']['attentionReason']=='Chase bank login required'
    assert 'attentionKind' not in bank_map['Robinhood']
    assert len(merged['institutionIssues'])==1
    assert b.warning_details({
      'connections':payload['connections'],
      'errlist':[{'conn_id':'two','msg':'SimpleFIN access rejected'}]})[2]['two']['kind']=='rejected'


def test_unattributed_notice_does_not_flag_every_bank(setup):
    payload={'connections':[{'conn_id':'one','name':'Chase'},{'conn_id':'two','name':'Robinhood'}],
             'errlist':[{'msg':'Bank login required, please sign in.'}]}
    notices,warnings,issues=b.warning_details(payload)
    assert warnings and issues=={} and notices==[]
