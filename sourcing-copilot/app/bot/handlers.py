"""Telegram bot logic (pure, testable — the transport is injected).

Roles: owner (me) gets everything; person (my contact in Turkmenistan) gets
shipments, stock, sales logging and status buttons. Anyone else is ignored
(after a one-time "pending approval" note so I can approve them in Settings).
"""
from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import services
from ..config import env, parse_chat_ids
from ..db import Order, Product, Shipment, TelegramChat, utcnow
from ..sales_parser import match_product, parse_sale
from ..settings_store import get_setting

log = logging.getLogger(__name__)

HELP = """🤖 Sourcing Copilot
/top — this week's top picks
/stock — stock on hand
/profit — profit last 30 days
/orders — open orders
/shipments — shipments in progress
/help — this help

Log a sale: sold 2 hamster 250
(2 units at 250 TMT each; add "total" for a total price)"""

PERSON_HELP = """🤖 Sourcing Copilot
/shipments — shipments + status buttons
/stock — stock on hand
Log a sale: sold 2 hamster 250"""

STATUS_BUTTONS = [("📦 Arrived", "arrived"), ("🚚 Delivered", "delivered"), ("💵 Cash received", "cash_received")]
STATUS_LABEL = {"arrived": "arrived in Ashgabat", "delivered": "delivered", "cash_received": "cash received",
                "in_transit": "in transit", "china_warehouse": "at China warehouse", "sold_out": "sold out"}


def role_of(db: Session, chat_id: int) -> str | None:
    if chat_id in parse_chat_ids(env("TELEGRAM_ALLOWED_CHAT_IDS")):
        row = db.get(TelegramChat, chat_id)
        if row and row.role == "person":
            return "person"
        person = get_setting(db, "telegram").get("person_chat_id")
        return "person" if person and int(person) == chat_id else "owner"
    row = db.get(TelegramChat, chat_id)
    if row and row.role in ("owner", "person"):
        return row.role
    person = get_setting(db, "telegram").get("person_chat_id")
    if person and int(person) == chat_id:
        return "person"
    return None


def owner_chats(db: Session) -> list[int]:
    ids = {c.chat_id for c in db.scalars(select(TelegramChat).where(TelegramChat.role == "owner"))}
    person = get_setting(db, "telegram").get("person_chat_id")
    for cid in parse_chat_ids(env("TELEGRAM_ALLOWED_CHAT_IDS")):
        if not person or int(person) != cid:
            ids.add(cid)
    return sorted(ids)


def person_chat(db: Session) -> int | None:
    person = get_setting(db, "telegram").get("person_chat_id")
    if person:
        return int(person)
    row = db.scalar(select(TelegramChat).where(TelegramChat.role == "person"))
    return row.chat_id if row else None


class BotHandler:
    def __init__(self, api: Any):
        self.api = api

    # ------------------------------------------------------------------ entry point
    def handle_update(self, db: Session, update: dict) -> None:
        if "callback_query" in update:
            return self._callback(db, update["callback_query"])
        msg = update.get("message") or {}
        chat = msg.get("chat") or {}
        chat_id = chat.get("id")
        text = (msg.get("text") or "").strip()
        if chat_id is None or not text:
            return
        role = role_of(db, chat_id)
        if role is None:
            return self._unknown(db, chat, text)
        cmd = text.split()[0].split("@")[0].lower()
        if cmd == "/start" or cmd == "/help":
            self.api.send_message(chat_id, HELP if role == "owner" else PERSON_HELP)
        elif cmd == "/top" and role == "owner":
            self.api.send_message(chat_id, self.top_text(db))
        elif cmd == "/stock":
            self.api.send_message(chat_id, self.stock_text(db))
        elif cmd == "/profit" and role == "owner":
            self.api.send_message(chat_id, self.profit_text(db))
        elif cmd == "/orders" and role == "owner":
            self.api.send_message(chat_id, self.orders_text(db))
        elif cmd == "/shipments":
            self.shipments(db, chat_id)
        elif cmd == "/digest" and role == "owner":
            self.api.send_message(chat_id, self.digest_text(db))
        elif cmd.startswith("/"):
            self.api.send_message(chat_id, "Unknown command. " + ("Try /help" if role == "owner" else "Try /shipments"))
        else:
            self.sale_message(db, chat_id, text, role)

    def _unknown(self, db: Session, chat: dict, text: str) -> None:
        chat_id = chat["id"]
        row = db.get(TelegramChat, chat_id)
        if row is None:
            name = " ".join(x for x in (chat.get("first_name"), chat.get("last_name")) if x) or chat.get("title") or ""
            db.add(TelegramChat(chat_id=chat_id, name=name, username=chat.get("username"), role="pending"))
            db.commit()
            if text.startswith("/start"):
                self.api.send_message(chat_id, f"👋 Hi! Your chat ID is {chat_id}. Waiting for approval in the app "
                                               f"(Settings → Telegram bot).")
        # pending/blocked chats are otherwise ignored completely

    # ------------------------------------------------------------------ texts
    def top_text(self, db: Session, limit: int = 7) -> str:
        cards = services.recommendations(db, limit=limit)
        if not cards:
            return "No recommendations yet: import trends first."
        lines = ["🔥 What to order this week"]
        for i, c in enumerate(cards, 1):
            st = {"rising": "↗", "peaking": "→", "fading": "↘"}.get(c["trend_state"], "·")
            line = f"\n{i}. {c['emoji']} {c['name']} — trend {c['trend_score']:.0f} {st}"
            if c.get("landing_tmt"):
                line += f"\n   land {c['landing_tmt']:.0f} → sell {c['sell_tmt']:.0f} TMT (+{c['profit_tmt']:.0f})"
            else:
                line += "\n   no offers yet — find one on Taobao/1688"
            if c.get("counterfeit_risk"):
                line += "\n   ⚠ branded: customs risk"
            line += f"\n   {c.get('explanation', '')}"
            lines.append(line)
        return "\n".join(lines)

    def digest_text(self, db: Session) -> str:
        rep = services.weekly_report(db)
        t = rep["totals"]
        parts = [self.top_text(db, limit=5), "",
                 f"📊 Last 7 days: {t['units']} sold, {t['revenue']:.0f} TMT revenue, {t['profit']:.0f} TMT profit ({t['margin_pct']:.0f}%)"]
        if rep["reorder"]:
            parts.append("🔁 Reorder: " + ", ".join(f"{r['name']} ({r['suggested_qty']})" for r in rep["reorder"][:5]))
        if rep["stop"]:
            parts.append("⛔ Stop buying: " + ", ".join(r["name"] for r in rep["stop"][:5]))
        return "\n".join(parts)

    def stock_text(self, db: Session) -> str:
        rows = services.stock_overview(db)
        if not rows:
            return "No stock recorded yet."
        lines = ["📦 Stock"]
        for r in rows:
            extra = f" (+{r['incoming']} on the way)" if r["incoming"] else ""
            lines.append(f"{r['emoji']} {r['name']}: {r['stock']}{extra} · sold {r['sold_30d']} in 30d")
        return "\n".join(lines)

    def profit_text(self, db: Session) -> str:
        p30, p7 = services.profit_summary(db, 30), services.profit_summary(db, 7)
        return (f"💰 Profit\n7 days: {p7['profit_tmt']:.0f} TMT on {p7['revenue_tmt']:.0f} revenue ({p7['units']} units)\n"
                f"30 days: {p30['profit_tmt']:.0f} TMT on {p30['revenue_tmt']:.0f} revenue ({p30['units']} units, "
                f"margin {p30['margin_pct']:.0f}%)")

    def orders_text(self, db: Session) -> str:
        orders = db.scalars(select(Order).order_by(Order.id.desc()).limit(6)).all()
        if not orders:
            return "No orders yet."
        lines = ["🧾 Orders"]
        for o in orders:
            s = services.order_summary(db, o)
            t = s["totals"]
            lines.append(f"#{o.id} {o.status}: {t['units']} pcs, ¥{t['goods_cny']:.0f}, land {t['landing_tmt']:.0f} TMT, "
                         f"ETA {s['arrival']['earliest']}–{s['arrival']['latest']}")
        return "\n".join(lines)

    def shipments(self, db: Session, chat_id: int) -> None:
        ships = db.scalars(select(Shipment).where(Shipment.status != "sold_out").order_by(Shipment.id.desc()).limit(8)).all()
        if not ships:
            self.api.send_message(chat_id, "No active shipments.")
            return
        for s in ships:
            d = services.shipment_dict(db, s)
            items = ", ".join(f"{i['product']} ×{i['qty']}" for i in d["items"]) or "—"
            flags = []
            if s.delivered_at:
                flags.append("delivered ✓")
            if s.cash_received_at:
                flags.append("cash ✓")
            eta = f"\nETA {d['eta']['earliest']}–{d['eta']['latest']}" if d.get("eta") else ""
            text = f"🚚 {s.code or '#' + str(s.id)} — {STATUS_LABEL.get(s.status, s.status)}{eta}\n{items}" + \
                   (f"\n{' · '.join(flags)}" if flags else "")
            self.api.send_message(chat_id, text, buttons=[[{"text": t, "callback_data": f"ship:{s.id}:{st}"}
                                                           for t, st in STATUS_BUTTONS]])

    # ------------------------------------------------------------------ sales
    def sale_message(self, db: Session, chat_id: int, text: str, role: str) -> None:
        parsed = parse_sale(text)
        if not parsed:
            self.api.send_message(chat_id, "I didn't understand. To log a sale: sold 2 hamster 250\n/help for commands")
            return
        products = db.scalars(select(Product)).all()
        prod, candidates = match_product(parsed.name, products)
        if not prod:
            if candidates:
                names = ", ".join(p.name for p in candidates)
                self.api.send_message(chat_id, f"Which product did you mean by “{parsed.name}”? {names}\n"
                                               f"Reply e.g.: sold {parsed.qty} {candidates[0].name} {parsed.unit_price:.0f}")
            else:
                self.api.send_message(chat_id, f"No product matches “{parsed.name}”.")
            return
        res = services.record_sale(db, prod, parsed.qty, parsed.unit_price, source="bot", note=f"tg:{chat_id}")
        profit = f"profit {res['profit_tmt']:.0f} TMT" if res["profit_tmt"] is not None else "profit unknown (no cost yet)"
        reply = (f"✅ Logged {parsed.qty} × {prod.name} at {parsed.unit_price:.0f} TMT = {parsed.total:.0f} TMT\n"
                 f"{profit} · stock left: {res['stock']}")
        if res.get("warning"):
            reply += f"\n⚠ {res['warning']}"
        self.api.send_message(chat_id, reply)
        if role == "person":
            for oc in owner_chats(db):
                self.api.send_message(oc, f"🧾 Sale by your person: {reply}")

    # ------------------------------------------------------------------ buttons
    def _callback(self, db: Session, cq: dict) -> None:
        chat_id = (cq.get("message") or {}).get("chat", {}).get("id") or cq.get("from", {}).get("id")
        role = role_of(db, chat_id) if chat_id is not None else None
        if role is None:
            return
        data = cq.get("data") or ""
        parts = data.split(":")
        if len(parts) == 3 and parts[0] == "ship":
            ship = db.get(Shipment, int(parts[1])) if parts[1].isdigit() else None
            if not ship:
                self.api.answer_callback(cq["id"], "Shipment not found")
                return
            try:
                services.set_shipment_status(db, ship, parts[2])
            except ValueError:
                self.api.answer_callback(cq["id"], "Unknown status")
                return
            label = STATUS_LABEL.get(parts[2], parts[2])
            self.api.answer_callback(cq["id"], f"Marked {label}")
            self.api.send_message(chat_id, f"✅ {ship.code or ship.id}: {label}")
            if role == "person":
                for oc in owner_chats(db):
                    self.api.send_message(oc, f"📣 Your person marked shipment {ship.code or ship.id}: {label}")
        else:
            self.api.answer_callback(cq["id"], "")


# ---------------------------------------------------------------------- outbound helpers

def send_digest(db: Session, api: Any) -> int:
    handler = BotHandler(api)
    text = handler.digest_text(db)
    chats = owner_chats(db)
    for c in chats:
        api.send_message(c, text)
    return len(chats)


def send_alerts(db: Session, api: Any, alerts: list[dict]) -> int:
    n = 0
    for a in alerts:
        for c in owner_chats(db):
            api.send_message(c, a["message"])
            n += 1
    return n


def send_order(db: Session, api: Any, order: Order, lang: str = "ru") -> int | None:
    chat = person_chat(db)
    if not chat:
        return None
    summary = services.order_summary(db, order)
    api.send_message(chat, services.export_text(summary, lang))
    ship = db.scalar(select(Shipment).where(Shipment.order_id == order.id))
    if ship:
        api.send_message(chat, f"Shipment {ship.code}: tap when it changes", buttons=[[
            {"text": t, "callback_data": f"ship:{ship.id}:{st}"} for t, st in STATUS_BUTTONS]])
    return chat


def recent_pending(db: Session) -> list[TelegramChat]:
    return db.scalars(select(TelegramChat).where(TelegramChat.role == "pending",
                                                 TelegramChat.first_seen >= utcnow() - timedelta(days=30))).all()
