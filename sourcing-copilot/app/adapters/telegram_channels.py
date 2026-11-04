"""Turkmen public Telegram channels — what local sellers offer and at what price.

Two readers:
1. **Web preview** (default, free, no key): public channels have a page at
   https://t.me/s/<channel>. We read the latest ~20 posts from it.
2. **Telethon** (optional, free API ID/hash from my.telegram.org): reads more
   history and channels whose web preview is disabled. Needs a one-time login
   (`python -m app.cli telegram-login`) which stores a session string in `.env`
   as TELEGRAM_SESSION. You never give the app your password; Telegram sends a
   login code to your app, and you type it into your own terminal.

Prices are extracted with regexes for TMT / manat / ман formats and matched to
my products by keyword.
"""
from __future__ import annotations

import html
import re
from datetime import datetime
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import services
from ..config import env
from ..db import LocalPrice, Product, TrendSignal, utcnow
from ..settings_store import get_setting
from .base import Adapter

PRICE_RE = re.compile(
    r"(?:(?:baha(?:sy)?|цена|price|bahasy|fiyat)\s*[:\-–]?\s*)?(\d{1,5}(?:[.,]\d{1,2})?)\s*(?:tmt|manat|man\b|m\b|ман(?:ат)?|тмт)"
    r"|(?:baha(?:sy)?|цена|price)\s*[:\-–]?\s*(\d{1,5})", re.IGNORECASE)


def extract_prices(text: str) -> list[float]:
    out = []
    for m in PRICE_RE.finditer(text or ""):
        v = m.group(1) or m.group(2)
        try:
            val = float(v.replace(",", "."))
        except (TypeError, ValueError):
            continue
        if 1 <= val <= 50000:
            out.append(val)
    return out


_POST_RE = re.compile(r'<div class="tgme_widget_message_wrap.*?data-post="([^"]+)".*?(?=<div class="tgme_widget_message_wrap|\Z)', re.S)
_TEXT_RE = re.compile(r'<div class="tgme_widget_message_text[^"]*"[^>]*>(.*?)</div>', re.S)
_DATE_RE = re.compile(r'<time datetime="([^"]+)"')
_VIEWS_RE = re.compile(r'<span class="tgme_widget_message_views">([^<]+)</span>')


def _views(v: str) -> int:
    v = v.strip().upper()
    mult = 1000 if v.endswith("K") else 1_000_000 if v.endswith("M") else 1
    try:
        return int(float(v.rstrip("KM")) * mult)
    except ValueError:
        return 0


def parse_preview(page: str, channel: str) -> list[dict]:
    posts = []
    for m in _POST_RE.finditer(page):
        block = m.group(0)
        tm = _TEXT_RE.search(block)
        if not tm:
            continue
        text = html.unescape(re.sub(r"<br\s*/?>", "\n", tm.group(1)))
        text = re.sub(r"<[^>]+>", "", text).strip()
        dm = _DATE_RE.search(block)
        vm = _VIEWS_RE.search(block)
        posts.append({"id": m.group(1), "url": f"https://t.me/{m.group(1)}", "text": text, "channel": channel,
                      "date": dm.group(1) if dm else None, "views": _views(vm.group(1)) if vm else 0})
    return posts


class TelegramChannelsAdapter(Adapter):
    name = "telegram_channels"
    label = "Turkmen Telegram channels (local prices)"
    needs = []
    optional = ["TELEGRAM_API_ID", "TELEGRAM_API_HASH", "TELEGRAM_SESSION"]
    kind = "local_market"
    free_note = "Free. Web preview needs nothing; Telethon (optional) needs a free API ID + hash from my.telegram.org."
    unlocks = "Ashgabat market: what local shops sell and at what price → local price check + demand signal."
    retry_minutes = 180

    def __init__(self, client: httpx.Client | None = None, telethon_fetch=None):
        self.client = client
        self.telethon_fetch = telethon_fetch

    def telethon_ready(self) -> bool:
        return all(env(n) for n in self.optional)

    def status(self, db: Session) -> dict[str, Any]:
        st = super().status(db)
        st["channels"] = get_setting(db, "telegram")["channels"]
        st["mode"] = "telethon" if self.telethon_ready() else "web preview"
        st["configured"] = bool(st["channels"])
        if not st["channels"]:
            st["message"] = "Add public channel usernames in Settings → Telegram channels"
        return st

    def fetch_channel(self, channel: str) -> list[dict]:
        channel = channel.strip().lstrip("@").replace("https://t.me/", "").strip("/")
        if self.telethon_fetch:
            return self.telethon_fetch(channel)
        if self.telethon_ready():
            try:
                return _telethon_fetch(channel)
            except Exception:  # fall back to the web preview
                pass
        client = self.client or httpx.Client(timeout=20, follow_redirects=True,
                                             headers={"User-Agent": "Mozilla/5.0 (SourcingCopilot)"})
        r = client.get(f"https://t.me/s/{channel}")
        r.raise_for_status()
        return parse_preview(r.text, channel)

    def run(self, db: Session, **kwargs: Any) -> dict[str, Any]:
        channels = get_setting(db, "telegram")["channels"]
        if not channels:
            return {"status": "skipped", "message": "No channels configured"}
        products = db.scalars(select(Product)).all()
        posts_n = prices_n = 0
        errors = []
        for ch in channels:
            try:
                posts = self.fetch_channel(ch)
            except Exception as e:  # one bad channel must not stop the others
                errors.append(f"{ch}: {type(e).__name__}")
                continue
            for post in posts:
                posts_n += 1
                ext = post["id"]
                if db.scalar(select(TrendSignal.id).where(TrendSignal.source == "telegram", TrendSignal.external_id == ext)):
                    continue
                prod = services.match_local_post(post["text"], products)
                prices = extract_prices(post["text"])
                when = services._parse_dt(post.get("date")) or utcnow()
                db.add(TrendSignal(source="telegram", platform="telegram", external_id=ext, url=post["url"],
                                   title=post["text"][:1000], creator=post["channel"], views=post.get("views", 0),
                                   published_at=when, product_id=prod.id if prod else None,
                                   classification="product" if prod else "unknown",
                                   extracted_product=prod.name if prod else None,
                                   value=prices[0] if prices else None, language="tk",
                                   raw={"prices": prices}))
                if prod and prices:
                    db.add(LocalPrice(product_id=prod.id, price_tmt=min(prices), source="telegram",
                                      channel=post["channel"], url=post["url"], text=post["text"][:500], seen_at=when))
                    prices_n += 1
        db.commit()
        if errors and not posts_n:
            raise RuntimeError("; ".join(errors))
        return {"channels": len(channels), "posts": posts_n, "prices": prices_n,
                **({"message": f"{posts_n} posts, {prices_n} prices; errors: {'; '.join(errors)}"} if errors else {})}


def _telethon_fetch(channel: str, limit: int = 50) -> list[dict]:
    """Read a public channel with Telethon using a saved StringSession."""
    import asyncio

    from telethon import TelegramClient
    from telethon.sessions import StringSession

    async def go():
        async with TelegramClient(StringSession(env("TELEGRAM_SESSION")), int(env("TELEGRAM_API_ID")),
                                  env("TELEGRAM_API_HASH")) as client:
            out = []
            async for msg in client.iter_messages(channel, limit=limit):
                if not msg.message:
                    continue
                out.append({"id": f"{channel}/{msg.id}", "url": f"https://t.me/{channel}/{msg.id}", "text": msg.message,
                            "channel": channel, "date": msg.date.replace(tzinfo=None).isoformat() if msg.date else None,
                            "views": msg.views or 0})
            return out

    return asyncio.run(go())


def telegram_login() -> None:  # pragma: no cover - interactive
    """One-time interactive login that prints a TELEGRAM_SESSION string to paste into .env."""
    from telethon.sync import TelegramClient
    from telethon.sessions import StringSession

    api_id, api_hash = env("TELEGRAM_API_ID"), env("TELEGRAM_API_HASH")
    if not api_id or not api_hash:
        print("Set TELEGRAM_API_ID and TELEGRAM_API_HASH in .env first (free from https://my.telegram.org).")
        return
    print("Telegram will send a login code to your Telegram app. Type it below (it is not stored).")
    with TelegramClient(StringSession(), int(api_id), api_hash) as client:
        print("\nPaste this line into your .env file:\n")
        print(f"TELEGRAM_SESSION={client.session.save()}\n")
        print(f"Logged in as {client.get_me().first_name}. Keep this string secret.")


def _parse(dt: str | None) -> datetime | None:
    return services._parse_dt(dt)
