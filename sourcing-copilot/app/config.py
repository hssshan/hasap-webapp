"""Environment configuration.

Everything secret lives in `.env` (see `.env.example`). Business rules that you
may want to tweak from your phone (exchange rate, cargo price, markup...) live in
the database `settings` table and are edited on the Settings screen instead.

Keys are read *fresh* on every call (`env()`), so the Setup page always reflects
the current `.env` after a restart, and tests can monkeypatch os.environ.
"""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env", override=False)


def env(name: str, default: str = "") -> str:
    value = os.environ.get(name, default)
    return value.strip() if isinstance(value, str) else default


def env_bool(name: str, default: bool = False) -> bool:
    raw = env(name, "")
    if raw == "":
        return default
    return raw.lower() in {"1", "true", "yes", "on"}


def env_int(name: str, default: int) -> int:
    try:
        return int(env(name, str(default)))
    except ValueError:
        return default


def env_float(name: str, default: float) -> float:
    try:
        return float(env(name, str(default)))
    except ValueError:
        return default


def data_dir() -> Path:
    path = Path(env("DATA_DIR") or str(BASE_DIR / "data"))
    path.mkdir(parents=True, exist_ok=True)
    (path / "uploads").mkdir(exist_ok=True)
    return path


def database_url() -> str:
    return env("DATABASE_URL") or f"sqlite:///{data_dir() / 'copilot.db'}"


# Model choice: small/cheap model for classification and short text, a mid model
# for vision (screenshots) and review summaries. Both overridable in .env.
def model_small() -> str:
    return env("CLAUDE_MODEL_SMALL") or "claude-haiku-5-5"


def model_vision() -> str:
    return env("CLAUDE_MODEL_VISION") or "claude-sonnet-5-5"


def parse_chat_ids(raw: str) -> set[int]:
    ids: set[int] = set()
    for part in raw.replace(";", ",").split(","):
        part = part.strip()
        if part.lstrip("-").isdigit():
            ids.add(int(part))
    return ids
