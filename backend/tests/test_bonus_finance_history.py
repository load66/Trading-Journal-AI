"""Synthetic-only history integrity and account privacy regressions."""
import hashlib
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest
from botocore.exceptions import ClientError
from cryptography.fernet import Fernet
import bonus_finance_history as history


class MemoryStore:
    bucket = "synthetic"
    def __init__(self):
        self.client = self
        self.objects = {}
        self.rev = 0
    def put_object(self, Bucket, Key, Body, **kwargs):
        if kwargs.get("IfNoneMatch") == "*" and Key in self.objects:
            raise ClientError({"Error":{"Code":"PreconditionFailed"}}, "PutObject")
        self.objects[Key] = Body
    def get_object(self, Bucket, Key):
        import io
        if Key not in self.objects:
            raise ClientError({"Error":{"Code":"NoSuchKey"}}, "GetObject")
        return {"Body":io.BytesIO(self.objects[Key])}
    def delete_object(self, Bucket, Key):
        self.objects.pop(Key, None)


def test_1000_transactions_are_partitioned_verified_and_reused():
    store = MemoryStore()
    fernet = Fernet(Fernet.generate_key())
    account = "a"*64
    rows = {f"synthetic-{i}":{"id":f"synthetic-{i}", "amount":"12.34",
                 "description":"Synthetic record "+("x"*250),
                 "posted": 1792134000 - i*86400,
                 "pending":False} for i in range(1000)}
    records = {account:rows}
    prefix = "private-test/owner-A/state.enc"
    manifest = history.store_shards(store.client,store.bucket,prefix,records,[],fernet,8*1024*1024)
    assert len(manifest) >= 5
    assert len(manifest) <= 1000
    assert all(entry["key"].startswith(prefix+"/history/") for entry in manifest)
    assert all(b"Synthetic record" not in blob for blob in store.objects.values())
    assert history.read_shards(store.client,store.bucket,prefix,manifest,fernet,8*1024*1024) == records
    old_keys = set(store.objects)
    again = history.store_shards(store.client,store.bucket,prefix,records,manifest,fernet,8*1024*1024)
    assert again == manifest and set(store.objects) == old_keys


def test_corrupted_shard_fails_closed_and_owner_prefix_is_scoped():
    from fastapi import HTTPException
    store = MemoryStore(); fernet=Fernet(Fernet.generate_key())
    prefix="owner-one"
    manifest=history.store_shards(store.client,store.bucket,prefix,
          {"account":{"synthetic":{"posted":1792134000,"amount":"1"}}},[],fernet,8*1024*1024)
    with pytest.raises(HTTPException):
        history.read_shards(store.client,store.bucket,"owner-two",manifest,fernet,8*1024*1024)
    store.objects[manifest[0]["key"]]=b"corrupted"
    with pytest.raises(HTTPException):
        history.read_shards(store.client,store.bucket,prefix,manifest,fernet,8*1024*1024)


def test_superseded_shards_deleted_only_after_verified_manifest():
    store=MemoryStore(); fernet=Fernet(Fernet.generate_key())
    prefix="private-test"
    older=history.store_shards(store.client,store.bucket,prefix,
        {"account":{"one":{"posted":1792134000,"amount":"1"}}},[],fernet,8*1024*1024)
    newer=history.store_shards(store.client,store.bucket,prefix,
        {"account":{"two":{"posted":1792134000,"amount":"2"}}},older,fernet,8*1024*1024)
    assert older[0]["key"] in store.objects
    history.read_shards(store.client,store.bucket,prefix,newer,fernet,8*1024*1024)
    history.prune_old_shards(store.client,store.bucket,prefix,older,newer)
    assert older[0]["key"] not in store.objects
    assert newer[0]["key"] in store.objects
