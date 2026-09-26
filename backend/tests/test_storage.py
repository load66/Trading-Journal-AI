import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from storage import LocalDiaryStorage, SupabaseDiaryStorage, safe_diary_object_path


class FakeBucket:
    def __init__(self):
        self.uploads = []
        self.removes = []

    def upload(self, path, data, options):
        self.uploads.append((path, data, options))

    def create_signed_url(self, path, expires_in):
        return {"signedURL": f"https://signed.example/{path}?ttl={expires_in}"}

    def remove(self, paths):
        self.removes.append(paths)


class FakeStorageApi:
    def __init__(self, bucket):
        self.bucket = bucket

    def from_(self, name):
        assert name == "private-diary"
        return self.bucket


class FakeClient:
    def __init__(self, bucket):
        self.storage = FakeStorageApi(bucket)


def test_local_storage_round_trip(tmp_path):
    storage = LocalDiaryStorage(str(tmp_path))
    path = "2026-09-26/1/note.jpg"

    assert storage.save(path, b"image", "image/jpeg") == path
    assert storage.local_path(path).read_bytes() == b"image"
    assert storage.signed_url(path) is None

    storage.delete(path)
    assert not (tmp_path / path).exists()


def test_supabase_storage_uses_private_signed_urls():
    bucket = FakeBucket()
    storage = SupabaseDiaryStorage(FakeClient(bucket), "private-diary")

    storage.save("note.jpg", b"image", "image/jpeg")
    assert bucket.uploads[0][2]["content-type"] == "image/jpeg"

    signed = storage.signed_url("note.jpg", 900)
    assert signed == "https://signed.example/note.jpg?ttl=900"

    storage.delete("note.jpg")
    assert bucket.removes == [["note.jpg"]]


def test_generated_object_path_cannot_escape_storage_prefix():
    path = safe_diary_object_path(
        "2026-09-26",
        7,
        "../../evil name.JPG",
    )
    assert path.startswith("2026-09-26/7/")
    assert ".." not in path
    assert "evil_name.jpg" in path
