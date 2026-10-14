"""Private, append-only BonusTracker recovery snapshots. No journal database access."""
import hashlib
import json
import re
from datetime import datetime, timezone
from fastapi import APIRouter, HTTPException, Request, Query
from fastapi.responses import JSONResponse
from uuid import uuid4
from starlette.concurrency import run_in_threadpool
from botocore.exceptions import ClientError
from botocore.config import Config
from auth import AuthError, auth_required, authorize_header
from config import Settings
from storage import R2Storage

router = APIRouter()
MAX_BYTES = 8 * 1024 * 1024
KEYS = frozenset(('bt_e_v4','bt_t_v4','bt_dd_methods','bt_bank_reqs','bt_offer_history_v1','bt_tc_archive_v1','bt_phone_book_v1','bt_user_datapoints_v1','bt_community_datapoints_v1','bt_community_datapoints_seed_v2','bt_profile_events_v1','bt_last_backup','bt_tc_learning_inbox_v320','bt_analyzer_memory_v1','bt_analyzer_training_library_v1','bt_analyzer_user_rules_v1','bt_last_restore'))
ID = re.compile(r'^[a-f0-9]{32}$')


def owner(request):
    if not auth_required():
        raise HTTPException(503, 'Secure cloud backup sign-in is not configured.')
    try:
        claims = authorize_header(request.headers.get('Authorization'))
    except AuthError as exc:
        raise HTTPException(exc.status_code, exc.detail)
    uid = claims.get('sub')
    if not isinstance(uid, str) or not uid:
        raise HTTPException(401, 'Cloud backup sign-in is required.')
    return hashlib.sha256(uid.encode()).hexdigest()


def store():
    settings = Settings.from_env()
    if not settings.r2_endpoint_url.startswith('https://'):
        raise HTTPException(503, 'Secure cloud backup storage is not configured.')
    if not all((settings.r2_bucket, settings.r2_endpoint_url, settings.r2_access_key_id, settings.r2_secret_access_key)):
        raise HTTPException(503, 'Cloud backup storage is not configured.')
    return R2Storage(settings,Config(connect_timeout=5,read_timeout=15,retries={'max_attempts':2},request_checksum_calculation='when_required',response_checksum_validation='when_required'))


def validate_snapshot(raw):
    if not isinstance(raw, str) or not 1 <= len(raw.encode('utf-8')) <= MAX_BYTES:
        raise ValueError('Backup is empty or too large')
    data = json.loads(raw)
    if not isinstance(data, dict) or data.get('format') != 'BonusTrackerCloudBackup' or data.get('version') != 1:
        raise ValueError('Unsupported backup version')
    if set(data) != {'format','version','appVersion','prefs','storageSnapshot'}:
        raise ValueError('Unsupported backup fields')
    if not isinstance(data['appVersion'], str) or len(data['appVersion']) > 40:
        raise ValueError('Invalid app version')
    if not isinstance(data['prefs'], dict) or set(data['prefs']) != {'dashYear','taxYear'} or any(type(v) is not int or not 1900 <= v <= 2200 for v in data['prefs'].values()):
        raise ValueError('Invalid preferences')
    snap = data['storageSnapshot']
    if not isinstance(snap, dict) or not set(snap).issubset(KEYS) or 'bt_e_v4' not in snap:
        raise ValueError('Backup contains unsupported storage or is missing records')
    if any(not isinstance(v, str) for v in snap.values()):
        raise ValueError('Storage values must be exact strings')
    entries = json.loads(snap['bt_e_v4'])
    if not isinstance(entries, list) or len(entries) > 10000:
        raise ValueError('Invalid account records')
    if any(not isinstance(e, dict) or not isinstance(e.get('bank'), str) or not e['bank'].strip() for e in entries):
        raise ValueError('Invalid account identity')
    return data, len(entries)


def key_for(uid, ident):
    if not isinstance(ident,str) or not ID.fullmatch(ident):
        raise HTTPException(400, 'Invalid backup identity.')
    return f'bonus-tracker-recovery/v1/{uid}/{ident}.json'


def receipt(ident, head):
    meta = head.get('Metadata', {})
    return {'id': ident, 'createdAt': meta.get('created-at'), 'sha256': meta.get('sha256'),
            'entryCount': int(meta.get('entry-count','0')), 'bytes': head.get('ContentLength',0),
            'reason': meta.get('reason',''), 'appVersion': meta.get('app-version',''), 'verified': True}


def read_verified(storage, uid, ident):
    key = key_for(uid, ident)
    try:
        head = storage.client.head_object(Bucket=storage.bucket, Key=key)
        if head.get('ContentLength',0) > MAX_BYTES:
            raise ValueError('Backup exceeds the supported size')
        raw, _ = storage.read(key)
    except FileNotFoundError:
        raise HTTPException(404, 'Backup not found.')
    except ClientError as exc:
        if str(exc.response.get('Error',{}).get('Code')) in ('404','NoSuchKey','NotFound'):
            raise HTTPException(404, 'Backup not found.')
        raise
    digest = hashlib.sha256(raw).hexdigest()
    if digest != head.get('Metadata',{}).get('sha256'):
        raise ValueError('Backup integrity check failed')
    text = raw.decode('utf-8')
    _, count = validate_snapshot(text)
    if count != int(head.get('Metadata',{}).get('entry-count','-1')):
        raise ValueError('Backup record count mismatch')
    return {'backup': receipt(ident,head), 'snapshot': text}


def save_verified(storage, uid, packet):
    if not isinstance(packet,dict) or set(packet) != {'id','sha256','reason','snapshot'}:
        raise ValueError('Unsupported upload fields')
    ident = packet['id']; key = key_for(uid, ident)
    if packet['reason'] not in ('automatic','manual','pre-restore','pre-reset','scheduled'):
        raise ValueError('Invalid backup reason')
    data, count = validate_snapshot(packet['snapshot'])
    raw = packet['snapshot'].encode('utf-8'); digest = hashlib.sha256(raw).hexdigest()
    if packet['sha256'] != digest:
        raise ValueError('Upload checksum mismatch')
    metadata = {'sha256':digest,'entry-count':str(count),'created-at':datetime.now(timezone.utc).isoformat(),
                'reason':packet['reason'],'app-version':data['appVersion']}
    try:
        storage.client.put_object(Bucket=storage.bucket,Key=key,Body=raw,ContentType='application/json',
                                  Metadata=metadata,IfNoneMatch='*')
    except ClientError as exc:
        if exc.response.get('ResponseMetadata',{}).get('HTTPStatusCode') != 412:
            raise
        existing = read_verified(storage,uid,ident)
        if existing['backup']['sha256'] != digest:
            raise HTTPException(409,'This backup identity already contains different data.')
        return existing['backup']
    # A successful upload alone is insufficient: verify a fresh read before acknowledgment.
    return read_verified(storage,uid,ident)['backup']


def list_backups(storage, uid, offset=0):
    prefix = f'bonus-tracker-recovery/v1/{uid}/'
    rows=[]; continuation=None
    while True:
        args={'Bucket':storage.bucket,'Prefix':prefix,'MaxKeys':1000}
        if continuation:args['ContinuationToken']=continuation
        result=storage.client.list_objects_v2(**args)
        rows.extend(x for x in result.get('Contents',[]) if ID.fullmatch(x['Key'][len(prefix):].removesuffix('.json')))
        if not result.get('IsTruncated'):break
        continuation=result.get('NextContinuationToken')
        if not continuation:raise ValueError('Incomplete cloud backup listing')
    rows.sort(key=lambda x:x['LastModified'],reverse=True)
    backups=[]
    for row in rows[offset:offset+50]:
        ident=row['Key'][len(prefix):].removesuffix('.json')
        head=storage.client.head_object(Bucket=storage.bucket,Key=row['Key'])
        # Listed snapshots were verified on upload; restore rechecks the full bytes.
        backups.append(receipt(ident,head))
    return {'backups':backups,'total':len(rows),'nextOffset':offset+50 if offset+50<len(rows) else None,'provider':'Cloudflare R2','retained':'Versions retained; this app never automatically deletes backups'}


async def operation(fn, *args):
    try:return await run_in_threadpool(fn,*args)
    except HTTPException:raise
    except (ValueError,UnicodeError,json.JSONDecodeError):
        raise HTTPException(422,'Backup validation or integrity check failed. Your records were not replaced.')
    except Exception:
        raise HTTPException(503,'Cloud storage is unavailable. Keep your local records and retry.')


def probe_storage(storage, uid):
    key=f'bonus-tracker-recovery/health/{uid}/{uuid4().hex}.json'
    raw=b'BonusTracker recovery storage verification'
    digest=hashlib.sha256(raw).hexdigest()
    storage.client.put_object(Bucket=storage.bucket,Key=key,Body=raw,ContentType='application/json',Metadata={'sha256':digest},IfNoneMatch='*')
    try:
        returned,_=storage.read(key)
        if hashlib.sha256(returned).hexdigest()!=digest:raise ValueError('Storage probe mismatch')
    finally:
        storage.client.delete_object(Bucket=storage.bucket,Key=key)
    return {'ready':True,'provider':'Cloudflare R2','readWriteVerified':True,'checkedAt':datetime.now(timezone.utc).isoformat(),'checkpointHours':12}


@router.get('/api/bonus-tracker/backups/health')
async def backup_health(request:Request):
    uid=owner(request)
    result=await operation(probe_storage,store(),uid)
    return JSONResponse(result,headers={'Cache-Control':'no-store'})


@router.get('/api/bonus-tracker/backups')
async def get_backups(request:Request, offset:int=Query(0,ge=0,le=100000)):
    uid=owner(request)
    return JSONResponse(await operation(list_backups,store(),uid,offset),headers={'Cache-Control':'no-store'})


@router.post('/api/bonus-tracker/backups')
async def put_backup(request:Request):
    uid=owner(request)
    raw=bytearray()
    async for part in request.stream():
        raw.extend(part)
        if len(raw) > MAX_BYTES*2+4096:raise HTTPException(413,'Backup upload is too large.')
    try:packet=json.loads(raw)
    except (ValueError,UnicodeError):raise HTTPException(400,'Invalid backup upload.')
    return JSONResponse(await operation(save_verified,store(),uid,packet),headers={'Cache-Control':'no-store'})


@router.get('/api/bonus-tracker/backups/{ident}')
async def get_backup(ident:str,request:Request):
    uid=owner(request)
    key_for(uid,ident)
    return JSONResponse(await operation(read_verified,store(),uid,ident),headers={'Cache-Control':'no-store'})


def scheduled_checkpoint(storage, uid, now=None):
    """A persistent twelve-hour checkpoint of the latest verified uploaded state."""
    now = now or datetime.now(timezone.utc)
    slot = int(now.timestamp()) // (12*3600)
    ident = hashlib.sha256((uid+':scheduled:'+str(slot)).encode()).hexdigest()[:32]
    # The slot identity is stable across process restarts and multiple instances.
    try:
        read_verified(storage,uid,ident)
        return {'created':False,'reason':'already-checkpointed'}
    except HTTPException as exc:
        if exc.status_code != 404:raise
    catalog=list_backups(storage,uid)
    if not catalog['backups']:return {'created':False,'reason':'no-uploaded-data'}
    latest = read_verified(storage,uid,catalog['backups'][0]['id'])
    result = save_verified(storage,uid,{'id':ident,'reason':'scheduled','snapshot':latest['snapshot'],'sha256':latest['backup']['sha256']})
    return {'created':True,'backup':result}


async def scheduled_recovery_loop():
    import asyncio
    import logging
    import os
    probed=False
    while True:
        try:
            if auth_required() and os.getenv('AUTHORIZED_USER_ID','').strip():
                uid=hashlib.sha256(os.environ['AUTHORIZED_USER_ID'].strip().encode()).hexdigest()
                storage=store()
                if not probed:
                    await run_in_threadpool(probe_storage,storage,uid)
                    logging.getLogger('uvicorn.error').info('bonus_backup_storage_probe_success')
                    probed=True
                await run_in_threadpool(scheduled_checkpoint,storage,uid)
        except asyncio.CancelledError:raise
        except Exception:
            logging.getLogger(__name__).warning('bonus_backup_checkpoint_failed; retrying on next scheduled check')
        await asyncio.sleep(300)
