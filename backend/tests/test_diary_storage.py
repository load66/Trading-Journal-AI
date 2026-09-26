import os
import sqlite3
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))


class FakeStorage:
    def __init__(self):
        self.saved = {}
        self.deleted = []

    def save(self, path, data, content_type=None):
        self.saved[path] = (data, content_type)
        return path

    def signed_url(self, path, expires_in=900):
        return f"https://signed.example/{path}?ttl={expires_in}"

    def delete(self, path):
        self.deleted.append(path)

    def local_path(self, path):
        return None


@pytest.fixture
def journal(tmp_path, monkeypatch):
    db = tmp_path / "journal.db"
    upload_dir = tmp_path / "uploads"

    monkeypatch.setenv("APP_ENV", "local")
    monkeypatch.setenv("AUTH_MODE", "disabled")
    monkeypatch.setenv("DATABASE_MODE", "sqlite")
    monkeypatch.setenv("DATABASE_PATH", str(db))
    monkeypatch.setenv("STORAGE_MODE", "local")
    monkeypatch.setenv("UPLOAD_DIR", str(upload_dir))

    for name in ("database", "storage", "auth", "main"):
        sys.modules.pop(name, None)

    import database
    database.DB_PATH = str(db)

    import main

    fake_storage = FakeStorage()
    monkeypatch.setattr(
        main,
        "get_diary_storage",
        lambda config=None: fake_storage,
    )
    monkeypatch.setattr(main, "apply_aliases", lambda conn, analysis: analysis)
    monkeypatch.setattr(main, "save_analysis_to_db", lambda *args, **kwargs: None)

    from fastapi.testclient import TestClient

    with TestClient(main.app) as client:
        conn = sqlite3.connect(db)
        conn.execute(
            "INSERT INTO accounts (id, name, type) VALUES (1, 'Day', 'day_trading')"
        )
        conn.commit()
        conn.close()

        yield client, main, fake_storage, db


def test_remote_diary_upload_uses_object_path_signed_url_and_temp_cleanup(
    journal,
    monkeypatch,
):
    client, main, storage, db = journal
    analyzed_paths = []

    def analyze(path, date, context):
        analyzed_paths.append(path)
        assert Path(path).exists()
        return {
            "diary_date": date,
            "overall_summary": "ok",
            "patterns_identified": [],
            "improvement_areas": [],
            "trade_analyses": [],
        }

    monkeypatch.setattr(main, "analyze_diary_entry", analyze)

    response = client.post(
        "/api/upload-diary",
        data={"date": "2026-09-26", "account_id": "1"},
        files={"file": ("desk shot.png", b"not-a-real-png", "image/png")},
    )
    assert response.status_code == 200, response.text

    body = response.json()
    object_path = body["image_path"]
    assert object_path in storage.saved
    assert object_path.startswith("2026-09-26/1/")
    assert analyzed_paths
    assert not Path(analyzed_paths[0]).exists()

    conn = sqlite3.connect(db)
    stored = conn.execute(
        "SELECT image_path FROM diary_entries WHERE id=?",
        (body["id"],),
    ).fetchone()[0]
    conn.close()
    assert stored == object_path

    listing = client.get("/api/diary")
    assert listing.status_code == 200
    entry = listing.json()[0]
    assert entry["image_url"].startswith("https://signed.example/")

    deleted = client.delete(f"/api/diary/{body['id']}")
    assert deleted.status_code == 200
    assert object_path in storage.deleted


def test_temp_image_is_cleaned_when_ai_analysis_fails(journal, monkeypatch):
    client, main, storage, _ = journal
    analyzed_paths = []

    def fail(path, date, context):
        analyzed_paths.append(path)
        assert Path(path).exists()
        raise RuntimeError("AI unavailable")

    monkeypatch.setattr(main, "analyze_diary_entry", fail)

    response = client.post(
        "/api/upload-diary",
        data={"date": "2026-09-26", "account_id": "1"},
        files={"file": ("note.png", b"image", "image/png")},
    )
    assert response.status_code == 200
    assert "AI unavailable" in response.json()["analysis_error"]
    assert analyzed_paths
    assert not Path(analyzed_paths[0]).exists()
