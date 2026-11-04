"""Minimal Telegram Bot API client (plain HTTPS, long polling — no webhook / public URL needed)."""
from __future__ import annotations

import logging
from typing import Any

import httpx

log = logging.getLogger(__name__)


class TelegramAPI:
    def __init__(self, token: str, client: httpx.Client | None = None):
        self.token = token
        self.base = f"https://api.telegram.org/bot{token}"
        self.client = client or httpx.Client(timeout=40)

    def call(self, method: str, **params: Any) -> Any:
        r = self.client.post(f"{self.base}/{method}", json={k: v for k, v in params.items() if v is not None})
        data = r.json()
        if not data.get("ok"):
            raise RuntimeError(f"Telegram {method} failed: {data.get('description')}")
        return data.get("result")

    def get_me(self) -> dict:
        return self.call("getMe")

    def get_updates(self, offset: int | None = None, timeout: int = 30) -> list[dict]:
        return self.call("getUpdates", offset=offset, timeout=timeout, allowed_updates=["message", "callback_query"])

    def send_message(self, chat_id: int, text: str, buttons: list[list[dict]] | None = None) -> dict:
        markup = {"inline_keyboard": buttons} if buttons else None
        # Telegram limit is 4096 chars
        return self.call("sendMessage", chat_id=chat_id, text=text[:4000], reply_markup=markup,
                         disable_web_page_preview=True)

    def answer_callback(self, callback_id: str, text: str = "") -> Any:
        return self.call("answerCallbackQuery", callback_query_id=callback_id, text=text[:190])


class FakeTelegramAPI:
    """In-memory stand-in used by tests (and by the app when no token is set)."""

    def __init__(self):
        self.sent: list[dict] = []
        self.callbacks: list[dict] = []

    def get_me(self) -> dict:
        return {"id": 1, "username": "test_bot", "first_name": "Test"}

    def get_updates(self, offset: int | None = None, timeout: int = 30) -> list[dict]:
        return []

    def send_message(self, chat_id: int, text: str, buttons: list[list[dict]] | None = None) -> dict:
        self.sent.append({"chat_id": chat_id, "text": text, "buttons": buttons})
        return {"message_id": len(self.sent)}

    def answer_callback(self, callback_id: str, text: str = "") -> Any:
        self.callbacks.append({"id": callback_id, "text": text})
        return True
