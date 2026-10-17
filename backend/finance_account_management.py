"""Additive account controls. Never changes bonus mappings or lifecycle records."""
import copy
import hashlib
import time
import uuid
from fastapi import HTTPException
from pydantic import BaseModel, Field


def institution_handle(account):
    value = account.get('connection') or account.get('bank') or account.get('key')
    return hashlib.sha256(('simplefin-bank\0' + str(value)).encode()).hexdigest()


def syncing(doc, account):
    key = account['key']
    removed = doc.get('accountExclusions', {}).get(key, {}).get('removedAt')
    excluded_bank = institution_handle(account) in doc.get('institutionExclusions', {})
    return not removed and not excluded_bank and doc.get('accountProfiles', {}).get(key, {}).get('syncEnabled', True)


def public_accounts(doc, accounts):
    result = []
    for account in accounts:
        row = copy.deepcopy(account)
        exclusion = doc.get('accountExclusions', {}).get(account['key'], {})
        row.update(removedAt=exclusion.get('removedAt'), syncEnabled=syncing(doc, account))
        if 'connectionHandle' not in row:
            row['connectionHandle'] = institution_handle(account)
        row['institutionRemoved'] = institution_handle(account) in doc.get('institutionExclusions', {})
        result.append(row)
    return result


def merge_profiles(existing, supplied):
    result = copy.deepcopy(existing)
    for key, values in supplied.items():
        result[key] = {**result.get(key, {}), **values}
    return result


class AccountAction(BaseModel):
    account: str = Field(pattern=r'^[a-f0-9]{64}$')
    action: str
    removalId: str | None = None
    deleteHistory: bool = False


def action(doc, accounts, body, now=None):
    """Remove is a persistent sync exclusion; restore is additive and idempotent."""
    now = int(time.time()) if now is None else now
    if body.account not in {a['key'] for a in accounts}:
        raise HTTPException(404, 'Account not found.')
    exclusions = doc.setdefault('accountExclusions', {})
    prior = exclusions.get(body.account, {})
    if body.action == 'remove':
        if not prior.get('removedAt'):
            exclusions[body.account] = {'removedAt': now, 'removalId': uuid.uuid4().hex,
                                        'deleteRequested': body.deleteHistory}
        return exclusions[body.account]
    if body.action == 'restore':
        for account in accounts:
            if account['key'] == body.account:
                doc.get('institutionExclusions', {}).pop(institution_handle(account), None)
        if body.removalId and prior.get('removalId') != body.removalId:
            raise HTTPException(409, 'Account changed. Reload before restoring.')
        if prior.get('historyDeleted'):
            exclusions[body.account] = {'deletedBefore': prior.get('deletedBefore', prior.get('removedAt')), 'historyDeleted': True}
        else:
            exclusions.pop(body.account, None)
        return {'restored': True}
    if body.action == 'delete_history':
        if (not body.deleteHistory or not prior.get('deleteRequested') or
                not body.removalId or prior.get('removalId') != body.removalId or
                now < prior.get('removedAt', now) + 10):
            raise HTTPException(409, 'Confirm removal and wait for the Undo window before deleting history.')
        return {'delete': True, **prior}
    raise HTTPException(422, 'Choose remove or restore.')


def purge_history(doc, account):
    """Only invoked following explicit delete-history confirmation and Undo expiry."""
    doc.get('transactions', {}).pop(account, None)
    for item in doc.get('items', {}).values():
        raw = {a.get('id') for a in item.get('accounts', []) if a['key'] == account}
        item['transactions'] = {k: v for k, v in item.get('transactions', {}).items() if v.get('account') not in raw}
    for field in ('transactionReviews', 'evidenceReviews', 'balanceHistory'):
        doc.get(field, {}).pop(account, None)
    doc['accountExclusions'][account]['historyDeleted'] = True
    doc['accountExclusions'][account]['deletedBefore'] = doc['accountExclusions'][account]['removedAt']


def filter_after_removal(doc, transactions):
    for key, rows in transactions.items():
        settings = doc.get('accountExclusions', {}).get(key, {})
        deleted = settings.get('deletedBefore')
        if deleted:
            transactions[key] = {i: row for i, row in rows.items() if row.get('posted', 0) > deleted}
            rows = transactions[key]
        cutoff = settings.get('removedAt')
        if cutoff:
            transactions[key] = {i: row for i, row in rows.items() if row.get('posted', 0) <= cutoff}
    return transactions
