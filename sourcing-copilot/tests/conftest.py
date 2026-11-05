import os
from datetime import datetime

import pytest

KEYS = ["ANTHROPIC_API_KEY", "YOUTUBE_API_KEY", "TELEGRAM_BOT_TOKEN", "TELEGRAM_ALLOWED_CHAT_IDS", "TELEGRAM_API_ID",
        "TELEGRAM_API_HASH", "TELEGRAM_SESSION", "TREND_IMPORT_SECRET", "APP_PASSWORD", "APP_SECRET_KEY"]

NOW = datetime(2026, 11, 4, 12, 0, 0)


@pytest.fixture(autouse=True)
def clean_env(monkeypatch, tmp_path):
    for k in KEYS:
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("SCHEDULER_ENABLED", "false")
    monkeypatch.setenv("SEED_MOCK_DATA", "false")
    monkeypatch.setenv("IMAGE_FETCH_ENABLED", "false")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    yield


@pytest.fixture
def db(tmp_path):
    from app import db as dbmod
    dbmod.init_engine(f"sqlite:///{tmp_path / 'test.db'}")
    s = dbmod.SessionLocal()
    yield s
    s.close()


@pytest.fixture
def seeded(db):
    from app.seed import seed
    seed(db)
    return db


@pytest.fixture
def client(tmp_path):
    from fastapi.testclient import TestClient
    from app.main import app
    with TestClient(app) as c:
        yield c


@pytest.fixture
def seeded_client(monkeypatch, tmp_path):
    monkeypatch.setenv("SEED_MOCK_DATA", "true")
    from fastapi.testclient import TestClient
    from app.main import app
    with TestClient(app) as c:
        yield c
