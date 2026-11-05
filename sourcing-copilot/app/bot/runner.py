"""Starts the bot's long-polling loop in a background thread when TELEGRAM_BOT_TOKEN is set."""
from __future__ import annotations

import logging
import threading
import time
from typing import Any

from ..config import env
from ..db import SessionLocal
from .api import TelegramAPI
from .handlers import BotHandler

log = logging.getLogger(__name__)


class BotRunner:
    def __init__(self):
        self.api: TelegramAPI | None = None
        self.thread: threading.Thread | None = None
        self.stop_event = threading.Event()
        self.me: dict | None = None
        self.error: str | None = None
        self.offset: int | None = None

    @property
    def active(self) -> bool:
        return self.api is not None and self.thread is not None and self.thread.is_alive()

    def status(self) -> dict[str, Any]:
        return {"token_set": bool(env("TELEGRAM_BOT_TOKEN")), "running": self.active,
                "username": (self.me or {}).get("username"), "error": self.error}

    def start(self) -> bool:
        token = env("TELEGRAM_BOT_TOKEN")
        if not token or self.active:
            return False
        try:
            self.api = TelegramAPI(token)
            self.me = self.api.get_me()
            self.api.call("deleteWebhook")
        except Exception as e:  # bad token / no network: app keeps running
            self.error = f"{type(e).__name__}: {e}"
            log.warning("Telegram bot not started: %s", self.error)
            self.api = None
            return False
        self.error = None
        self.stop_event.clear()
        self.thread = threading.Thread(target=self._loop, name="telegram-bot", daemon=True)
        self.thread.start()
        log.info("Telegram bot @%s started", self.me.get("username"))
        return True

    def stop(self) -> None:
        self.stop_event.set()

    def _loop(self) -> None:
        handler = BotHandler(self.api)
        backoff = 2
        while not self.stop_event.is_set():
            try:
                updates = self.api.get_updates(offset=self.offset, timeout=25)
                backoff = 2
            except Exception as e:
                self.error = str(e)[:200]
                time.sleep(backoff)
                backoff = min(backoff * 2, 120)
                continue
            for upd in updates:
                self.offset = upd["update_id"] + 1
                db = SessionLocal()
                try:
                    handler.handle_update(db, upd)
                except Exception as e:  # one bad message must not kill the bot
                    log.exception("bot update failed: %s", e)
                    db.rollback()
                finally:
                    db.close()


RUNNER = BotRunner()
