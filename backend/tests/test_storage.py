import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from config import ConfigError, Settings  # noqa: E402
from storage import ChartStorage, DiaryStorage, R2Storage  # noqa: E402
from botocore.exceptions import ClientError  # noqa: E402


def cfg(tmp_path, **overrides):
    env = {
        "APP_ENV": "test",
        "AUTH_MODE": "disabled",
        "DATABASE_MODE": "sqlite",
        "DATABASE_PATH": ":memory:",
        "STORAGE_MODE": "local",
        "UPLOAD_DIR": str(tmp_path),
    }
    env.update(overrides)
    return Settings.from_env(env)


def test_local_storage_round_trip(tmp_path):
    store = DiaryStorage(cfg(tmp_path))
    store.save("entry.jpg", b"abc", "image/jpeg")
    assert store.read("entry.jpg") == (b"abc", "image/jpeg")
    store.delete("entry.jpg")
    assert not (tmp_path / "entry.jpg").exists()


def test_supabase_storage_requires_server_credentials(tmp_path):
    settings = cfg(tmp_path, STORAGE_MODE="supabase", SUPABASE_URL="https://p.supabase.co")
    with pytest.raises(ConfigError, match="SUPABASE_SECRET_KEY"):
        DiaryStorage(settings)


def test_object_names_cannot_escape_storage_root(tmp_path):
    store = DiaryStorage(cfg(tmp_path))
    with pytest.raises(ValueError):
        store.save("../secret", b"x", "application/octet-stream")


def test_supabase_private_read_uses_authenticated_endpoint(tmp_path, monkeypatch):
    settings = cfg(
        tmp_path,
        STORAGE_MODE="supabase",
        SUPABASE_URL="https://p.supabase.co",
        SUPABASE_SECRET_KEY="sb_secret_server",
        SUPABASE_STORAGE_BUCKET="diary",
    )
    seen = {}

    class FakeResponse:
        content = b"private-bytes"
        headers = {"content-type": "image/jpeg"}

        def raise_for_status(self):
            return None

    def fake_get(url, headers=None, timeout=None):
        seen["url"] = url
        seen["headers"] = headers
        return FakeResponse()

    monkeypatch.setattr("storage.httpx.get", fake_get)

    store = DiaryStorage(settings)
    data, content_type = store.read("2026/entry.jpg")

    assert data == b"private-bytes"
    assert content_type == "image/jpeg"
    assert seen["url"] == (
        "https://p.supabase.co/storage/v1/object/authenticated/"
        "diary/2026/entry.jpg"
    )
    assert seen["headers"]["apikey"] == "sb_secret_server"
    assert "Authorization" not in seen["headers"]


def test_r2_storage_requires_complete_configuration(tmp_path):
    with pytest.raises(ConfigError, match="R2_"):
        cfg(tmp_path, CHART_STORAGE_MODE="r2")


def test_r2_storage_round_trip_uses_private_s3_api(tmp_path, monkeypatch):
    settings = cfg(
        tmp_path,
        CHART_STORAGE_MODE="r2",
        R2_BUCKET="trading-journal-screenshots",
        R2_ENDPOINT_URL="https://account.r2.cloudflarestorage.com",
        R2_ACCESS_KEY_ID="access",
        R2_SECRET_ACCESS_KEY="secret",
    )
    seen = {}

    class FakeBody:
        def read(self):
            return b"chart-bytes"

    class FakeS3:
        def put_object(self, **kwargs):
            seen["put"] = kwargs

        def get_object(self, **kwargs):
            seen["get"] = kwargs
            return {"Body": FakeBody(), "ContentType": "image/webp"}

        def delete_object(self, **kwargs):
            seen["delete"] = kwargs

    monkeypatch.setattr("storage.boto3.client", lambda *args, **kwargs: FakeS3())
    store = R2Storage(settings)
    store.save("trade-review/abc/chart.webp", b"chart-bytes", "image/webp")
    assert store.read("trade-review/abc/chart.webp") == (b"chart-bytes", "image/webp")
    store.delete("trade-review/abc/chart.webp")

    assert seen["put"]["Bucket"] == "trading-journal-screenshots"
    assert seen["put"]["Key"] == "trade-review/abc/chart.webp"
    assert seen["get"]["Bucket"] == "trading-journal-screenshots"
    assert seen["delete"]["Bucket"] == "trading-journal-screenshots"


def test_chart_storage_falls_back_to_existing_storage_for_legacy_object(tmp_path, monkeypatch):
    settings = cfg(
        tmp_path,
        CHART_STORAGE_MODE="r2",
        R2_BUCKET="trading-journal-screenshots",
        R2_ENDPOINT_URL="https://account.r2.cloudflarestorage.com",
        R2_ACCESS_KEY_ID="access",
        R2_SECRET_ACCESS_KEY="secret",
    )
    legacy = DiaryStorage(settings.__class__(**{**settings.__dict__, "storage_mode": "local"}))
    legacy.save("trade-review/legacy/chart.webp", b"legacy", "image/webp")

    class MissingS3:
        def get_object(self, **kwargs):
            raise ClientError(
                {"Error": {"Code": "NoSuchKey"}, "ResponseMetadata": {"HTTPStatusCode": 404}},
                "GetObject",
            )

        def put_object(self, **kwargs):
            return None

        def delete_object(self, **kwargs):
            return None

    monkeypatch.setattr("storage.boto3.client", lambda *args, **kwargs: MissingS3())
    store = ChartStorage(settings, legacy)
    assert store.read("trade-review/legacy/chart.webp") == (b"legacy", "image/webp")


def test_chart_storage_existing_mode_does_not_construct_r2(tmp_path, monkeypatch):
    settings = cfg(tmp_path)
    monkeypatch.setattr(
        "storage.boto3.client",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("R2 should not be constructed")),
    )
    store = ChartStorage(settings)
    store.save("trade-review/local/chart.webp", b"local", "image/webp")
    assert store.read("trade-review/local/chart.webp") == (b"local", "image/webp")
