"""Business logic shared by the API, the scheduler and the Telegram bot."""
from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import quote

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from . import ai, delivery, pricing, product_filter, scoring
from .db import (
    SHIPMENT_STATUSES, LocalPrice, Offer, Order, OrderItem, Product, Sale, Seller, SellerEvent, Shipment,
    StockMovement, TrendSignal, TrendSnapshot, row_dict, utcnow,
)
from .settings_store import get_setting

log = logging.getLogger(__name__)


def rules(db: Session) -> dict:
    return get_setting(db, "pricing")


# ============================================================ stock & sales

def stock_for(db: Session, product_id: int) -> int:
    return int(db.scalar(select(func.coalesce(func.sum(StockMovement.qty), 0)).where(StockMovement.product_id == product_id)) or 0)


def avg_cost(db: Session, product_id: int) -> float | None:
    rows = db.execute(select(StockMovement.qty, StockMovement.unit_cost_tmt).where(
        StockMovement.product_id == product_id, StockMovement.qty > 0, StockMovement.unit_cost_tmt.is_not(None))).all()
    units = sum(q for q, _ in rows)
    if not units:
        return None
    return sum(q * c for q, c in rows) / units


def estimated_unit_cost(db: Session, product: Product) -> float | None:
    cost = avg_cost(db, product.id)
    if cost is not None:
        return cost
    ranked = rank_offers(db, product)
    return ranked[0]["pricing"]["landing_tmt"] if ranked else None


def record_sale(db: Session, product: Product, qty: int, unit_price: float, *, source: str = "app",
                channel: str | None = None, note: str | None = None, sold_at: datetime | None = None,
                is_mock: bool = False) -> dict:
    cost = estimated_unit_cost(db, product)
    sale = Sale(product_id=product.id, qty=qty, unit_price_tmt=unit_price, unit_cost_tmt=cost, source=source,
                channel=channel, note=note, sold_at=sold_at or utcnow(), is_mock=is_mock)
    db.add(sale)
    db.flush()
    db.add(StockMovement(product_id=product.id, qty=-qty, kind="sale", sale_id=sale.id, unit_cost_tmt=cost,
                         created_at=sale.sold_at, is_mock=is_mock))
    db.commit()
    stock = stock_for(db, product.id)
    profit = (unit_price - (cost or 0)) * qty if cost is not None else None
    return {"sale": row_dict(sale), "stock": stock, "profit_tmt": profit, "unit_cost_tmt": cost,
            "warning": "Stock went negative: record the arrival or adjust stock." if stock < 0 else None}


def delete_sale(db: Session, sale: Sale) -> None:
    db.query(StockMovement).filter(StockMovement.sale_id == sale.id).delete()
    db.delete(sale)
    db.commit()


def sales_between(db: Session, product_id: int, start: datetime, end: datetime) -> int:
    return int(db.scalar(select(func.coalesce(func.sum(Sale.qty), 0)).where(
        Sale.product_id == product_id, Sale.sold_at >= start, Sale.sold_at < end)) or 0)


def stock_overview(db: Session) -> list[dict]:
    out = []
    for p in db.scalars(select(Product).order_by(Product.name)):
        stock = stock_for(db, p.id)
        sold_30 = sales_between(db, p.id, utcnow() - timedelta(days=30), utcnow() + timedelta(days=1))
        incoming = int(db.scalar(select(func.coalesce(func.sum(OrderItem.qty), 0)).join(Order).join(
            Shipment, Shipment.order_id == Order.id).where(
            OrderItem.product_id == p.id, Shipment.status.in_(["ordered", "china_warehouse", "in_transit"]))) or 0)
        if stock or sold_30 or incoming:
            out.append({"product_id": p.id, "name": p.name, "emoji": p.emoji, "stock": stock, "incoming": incoming,
                        "sold_30d": sold_30, "avg_cost_tmt": avg_cost(db, p.id)})
    return out


def profit_summary(db: Session, days: int = 30) -> dict:
    start = utcnow() - timedelta(days=days)
    sales = db.scalars(select(Sale).where(Sale.sold_at >= start)).all()
    revenue = sum(s.qty * s.unit_price_tmt for s in sales)
    cost = sum(s.qty * (s.unit_cost_tmt or 0) for s in sales)
    return {"days": days, "units": sum(s.qty for s in sales), "revenue_tmt": round(revenue, 2),
            "cost_tmt": round(cost, 2), "profit_tmt": round(revenue - cost, 2),
            "margin_pct": round((revenue - cost) / revenue * 100, 1) if revenue else 0}


# ============================================================ sellers, offers, delivery

def seller_score(db: Session, seller: Seller) -> dict:
    return scoring.seller_score(seller, seller.events)


def shipment_history_days(db: Session, seller_id: int | None = None) -> list[float]:
    q = select(Shipment).where(Shipment.ordered_at.is_not(None), Shipment.arrived_at.is_not(None))
    if seller_id:
        q = q.where(Shipment.seller_id == seller_id)
    return [(s.arrived_at - s.ordered_at).total_seconds() / 86400 for s in db.scalars(q)]


def delivery_estimate(db: Session, seller_id: int | None = None) -> dict:
    delay = 0.0
    if seller_id:
        evs = db.scalars(select(SellerEvent).where(SellerEvent.seller_id == seller_id, SellerEvent.kind == "delay")).all()
        if evs:
            delay = sum((e.days_late or 3) for e in evs) / len(evs)
    return delivery.estimate(get_setting(db, "delivery"), shipment_history_days(db), delay)


def local_prices_for(db: Session, product_id: int) -> list[LocalPrice]:
    return db.scalars(select(LocalPrice).where(LocalPrice.product_id == product_id,
                                               LocalPrice.seen_at >= utcnow() - timedelta(days=90))
                      .order_by(LocalPrice.seen_at.desc())).all()


def rank_offers(db: Session, product: Product, qty: int = 1) -> list[dict]:
    offers = list(product.offers)
    if not offers:
        return []
    r = rules(db)
    priced = {o.id: pricing.price_offer(o, r, qty) for o in offers}
    sellers = {o.seller_id: seller_score(db, o.seller) for o in offers if o.seller}
    values = scoring.offer_value_scores(offers, {k: v.landing_tmt for k, v in priced.items()},
                                        {k: v["score"] for k, v in sellers.items()})
    local = [lp.price_tmt for lp in local_prices_for(db, product.id)]
    ranked = []
    for o in offers:
        p = priced[o.id]
        ranked.append({
            "offer": offer_dict(o),
            "offer_id": o.id,
            "title": o.title_en or o.title,
            "pricing": p.as_dict(),
            "landing_tmt": p.landing_tmt,
            "value": values[o.id],
            "seller": ({"id": o.seller.id, "name": o.seller.name, **sellers[o.seller_id]} if o.seller else None),
            "delivery": delivery_estimate(db, o.seller_id),
            "local_check": pricing.local_price_check(p.sell_tmt, local),
        })
    ranked.sort(key=lambda x: x["value"]["score"], reverse=True)
    return ranked


def offer_dict(o: Offer) -> dict:
    d = row_dict(o)
    d["seller_name"] = o.seller.name if o.seller else None
    return d


def marketplace_links(terms: list[str]) -> list[dict]:
    links = []
    for t in terms[:3]:
        links.append({"term": t,
                      "taobao": f"https://s.taobao.com/search?q={quote(t)}",
                      "1688": f"https://s.1688.com/selloffer/offer_search.htm?charset=utf8&keywords={quote(t)}"})
    return links


def ensure_search_terms(db: Session, product: Product) -> dict:
    if not product.search_terms or not product.search_terms.get("zh") or product.search_terms.get("fallback"):
        terms = ai.search_terms(db, product.name, product.category)
        if terms != product.search_terms:
            product.search_terms = terms
            kws = set(product.keywords or [])
            for k in ("tr", "ru", "tk"):
                if terms.get(k):
                    kws.add(str(terms[k]).lower())
            product.keywords = sorted(kws)
            db.commit()
    return product.search_terms


# ============================================================ trend ingestion

def find_or_create_product(db: Session, cls: product_filter.Classification, is_mock: bool = False) -> Product:
    name = cls.product_name or "unnamed product"
    for p in db.scalars(select(Product)):
        if product_filter.same_product(p.name, name):
            if cls.counterfeit and not p.counterfeit_risk:
                p.counterfeit_risk, p.counterfeit_reason = True, cls.counterfeit_reason
            return p
    kg, _ = pricing.estimate_weight(name, cls.category or "")
    p = Product(name=name, category=cls.category or "other", emoji=cls.emoji or "📦", est_weight_kg=kg,
                counterfeit_risk=cls.counterfeit, counterfeit_reason=cls.counterfeit_reason,
                keywords=sorted(product_filter.name_tokens(name)), is_mock=is_mock)
    terms = ai.FALLBACK_TERMS.get(name)
    if terms:
        p.search_terms = terms
        p.keywords = sorted(set(p.keywords) | {terms["tr"].lower(), terms["ru"].lower(), terms["tk"].lower()})
    db.add(p)
    db.flush()
    return p


def _parse_dt(v: Any) -> datetime | None:
    if not v:
        return None
    if isinstance(v, datetime):
        return v.replace(tzinfo=None)
    try:
        return datetime.fromisoformat(str(v).replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        return None


def ingest_signals(db: Session, source: str, items: list[dict], *, is_mock: bool = False) -> dict:
    """Generic ingestion used by every social adapter (vidIQ import, YouTube, paste box).

    Each item: {platform, external_id|id|url, url, title, description, language, creator, views,
                outlier_score, published_at, product_name?, classification?, raw?}
    """
    texts, prepared = [], []
    for it in items:
        ext = str(it.get("external_id") or it.get("id") or it.get("url") or it.get("title") or "")[:300]
        if not ext:
            continue
        text = " ".join(str(x) for x in (it.get("title"), it.get("description"), " ".join(it.get("hashtags") or [])) if x)
        prepared.append((ext, it, text))
        texts.append(text)
    classes = ai.classify_items(db, texts) if texts else []
    stats = {"received": len(items), "new": 0, "updated": 0, "product": 0, "noise": 0, "unknown": 0, "products": set()}
    for (ext, it, text), cls in zip(prepared, classes):
        # The importer (e.g. Claude scheduled task) may pre-classify; rules/AI still check noise.
        if it.get("classification") == "product" and it.get("product_name") and cls.label != "noise":
            canon = product_filter.canonical_product(it["product_name"])
            cls = product_filter.Classification(
                "product", canon[0] if canon else it["product_name"].strip().lower(),
                canon[1] if canon else it.get("category") or cls.category or "other",
                canon[2] if canon else cls.emoji or "📦", "pre-classified by importer",
                cls.counterfeit or bool(it.get("counterfeit")), cls.counterfeit_reason, 0.8)
        existing = db.scalar(select(TrendSignal).where(TrendSignal.source == source, TrendSignal.external_id == ext))
        sig = existing or TrendSignal(source=source, external_id=ext, is_mock=is_mock)
        sig.platform = it.get("platform") or sig.platform or source
        sig.url = it.get("url") or sig.url
        sig.title = text[:1000]
        sig.language = (it.get("language") or sig.language or "")[:8]
        sig.creator = it.get("creator") or sig.creator
        sig.views = int(it.get("views") or sig.views or 0)
        sig.outlier_score = it.get("outlier_score") or sig.outlier_score
        sig.published_at = _parse_dt(it.get("published_at")) or sig.published_at
        sig.collected_at = _parse_dt(it.get("collected_at")) or sig.collected_at or utcnow()
        sig.classification = cls.label
        sig.class_reason = cls.reason
        sig.extracted_product = cls.product_name
        sig.raw = it.get("raw") or {k: v for k, v in it.items() if k not in ("title", "description")}
        if cls.label == "product" and cls.product_name:
            p = find_or_create_product(db, cls, is_mock=is_mock)
            sig.product_id = p.id
            stats["products"].add(p.name)
        if existing is None:
            db.add(sig)
            stats["new"] += 1
        else:
            stats["updated"] += 1
        stats[cls.label] += 1
    db.commit()
    stats["products"] = sorted(stats["products"])
    return stats


# ============================================================ trend scoring

def compute_product_trend(db: Session, p: Product, now: datetime | None = None) -> dict:
    now = now or utcnow()
    tset = get_setting(db, "trends")
    sigs = db.scalars(select(TrendSignal).where(TrendSignal.product_id == p.id)).all()
    s30 = sales_between(db, p.id, now - timedelta(days=30), now + timedelta(days=1))
    sprev = sales_between(db, p.id, now - timedelta(days=60), now - timedelta(days=30))
    local_n = db.scalar(select(func.count(LocalPrice.id)).where(LocalPrice.product_id == p.id,
                                                               LocalPrice.seen_at >= now - timedelta(days=60))) or 0
    snaps = [(datetime.combine(s.day, datetime.min.time()), s.score) for s in
             db.scalars(select(TrendSnapshot).where(TrendSnapshot.product_id == p.id,
                                                    TrendSnapshot.day >= (now - timedelta(days=42)).date()))]
    return scoring.trend_score(sigs, sales_30d=s30, sales_prev_30d=sprev, local_listings=local_n, snapshots=snaps,
                               now=now, min_independent=int(tset["min_independent_signals"]),
                               single_cap=float(tset["single_signal_cap"]))


def recompute_trends(db: Session, now: datetime | None = None) -> list[dict]:
    """Recompute every product's Trend Score, store a daily snapshot, and return alerts for watched products."""
    now = now or utcnow()
    threshold = float(get_setting(db, "trends")["alert_threshold"])
    alerts = []
    for p in db.scalars(select(Product)).all():
        res = compute_product_trend(db, p, now)
        old_score = p.trend_score or 0
        p.trend_score, p.trend_state, p.trend_breakdown = res["score"], res["state"], res
        snap = db.scalar(select(TrendSnapshot).where(TrendSnapshot.product_id == p.id, TrendSnapshot.day == now.date()))
        if snap:
            snap.score = res["score"]
        else:
            db.add(TrendSnapshot(product_id=p.id, day=now.date(), score=res["score"], is_mock=p.is_mock))
        if p.watched and not p.dismissed:
            key = None
            if res["score"] >= threshold and old_score < threshold:
                key = f"crossed:{int(threshold)}"
                msg = f"📈 {p.name} crossed Trend Score {threshold:.0f} (now {res['score']})."
            elif res["state"] == "fading" and p.last_alert_key != "fading":
                key = "fading"
                msg = f"📉 {p.name} is fading ({res['state_reason']}). Don't reorder big."
            if key and key != p.last_alert_key:
                p.last_alert_key = key
                alerts.append({"product_id": p.id, "name": p.name, "message": msg, "kind": key})
    db.commit()
    return alerts


# ============================================================ recommendations

def product_card(db: Session, p: Product) -> dict:
    ranked = rank_offers(db, p)
    best = ranked[0] if ranked else None
    if best:
        pr = best["pricing"]
        deliv = best["delivery"]
        local = best["local_check"]
    else:
        pr = pricing.calculate(0, 0, p.est_weight_kg, rules(db), name=p.name, category=p.category).as_dict()
        deliv = delivery_estimate(db)
        local = pricing.local_price_check(0, [])
    tb = p.trend_breakdown or {}
    return {
        "product_id": p.id, "name": p.name, "emoji": p.emoji, "category": p.category,
        "image_url": best["offer"]["image_url"] if best and best["offer"].get("image_url") else p.image_url,
        "trend_score": p.trend_score, "trend_state": p.trend_state, "trend_colour": scoring.colour(p.trend_score),
        "signals": tb.get("independent_signals", 0), "is_trend": tb.get("is_trend", False), "trend_breakdown": tb,
        "counterfeit_risk": p.counterfeit_risk, "counterfeit_reason": p.counterfeit_reason,
        "watched": p.watched, "dismissed": p.dismissed,
        "best_offer": ({"offer_id": best["offer_id"], "title": best["title"], "value_score": best["value"]["score"],
                        "price_cny": best["offer"]["price_cny"], "url": best["offer"]["url"]} if best else None),
        "offers_count": len(ranked),
        "landing_tmt": pr["landing_tmt"] if best else None,
        "sell_tmt": pr["sell_tmt"] if best else None,
        "profit_tmt": pr["profit_tmt"] if best else None,
        "margin_pct": pr["margin_pct"] if best else None,
        "weight_is_estimate": pr["weight_is_estimate"],
        "seller_score": best["seller"]["score"] if best and best["seller"] else None,
        "seller_name": best["seller"]["name"] if best and best["seller"] else None,
        "delivery": deliv, "local_check": local,
        "stock": stock_for(db, p.id),
        "own_sales_30d": sales_between(db, p.id, utcnow() - timedelta(days=30), utcnow() + timedelta(days=1)),
    }


def recommendations(db: Session, limit: int = 10, explain: bool = True) -> list[dict]:
    """Rank products for 'What should I order this week?'"""
    cards = []
    for p in db.scalars(select(Product).where(Product.dismissed.is_(False)).options(selectinload(Product.offers))).all():
        c = product_card(db, p)
        score = c["trend_score"]
        state_mult = {"rising": 1.15, "peaking": 0.95, "fading": 0.6, "unknown": 0.9}.get(c["trend_state"], 0.9)
        rank = score * state_mult
        if c["margin_pct"]:
            rank += min(15, c["margin_pct"] / 4)
        if c["seller_score"]:
            rank += (c["seller_score"] - 50) / 10
        if c["counterfeit_risk"]:
            rank -= 15
        if not c["is_trend"]:
            rank -= 10
        if c["local_check"].get("status") == "above_market":
            rank -= 8
        if c["stock"] > 3 * max(1, c["own_sales_30d"]) and c["stock"] > 10:
            rank -= 10  # plenty of stock already
        c["rank_score"] = round(rank, 1)
        cards.append(c)
    cards.sort(key=lambda c: c["rank_score"], reverse=True)
    top = cards[:limit]
    if explain:
        expl = ai.explain_recommendations(db, top)
        for c in top:
            c["explanation"] = expl.get(c["product_id"], ai.template_explanation(c))
    return top


# ============================================================ orders / basket

def draft_order(db: Session) -> Order:
    order = db.scalar(select(Order).where(Order.status == "draft").order_by(Order.id.desc()))
    if not order:
        order = Order(status="draft")
        db.add(order)
        db.commit()
    return order


def add_to_basket(db: Session, offer: Offer, qty: int = 1, variant: str | None = None) -> Order:
    order = draft_order(db)
    for it in order.items:
        if it.offer_id == offer.id and (it.variant or "") == (variant or ""):
            it.qty += qty
            db.commit()
            return order
    order.items.append(OrderItem(offer_id=offer.id, product_id=offer.product_id, qty=qty, variant=variant))
    db.commit()
    return order


def order_summary(db: Session, order: Order) -> dict:
    r = rules(db)
    lines = []
    totals = {"goods_cny": 0.0, "goods_tmt": 0.0, "cargo_tmt": 0.0, "landing_tmt": 0.0, "sell_tmt": 0.0,
              "profit_tmt": 0.0, "weight_kg": 0.0, "units": 0, "weight_has_estimates": False}
    seller_ids = set()
    for it in order.items:
        o = it.offer
        if o is None:
            continue
        pr = pricing.price_offer(o, r, it.qty)
        if order.status == "placed" and it.unit_landing_tmt:
            unit_landing = it.unit_landing_tmt
        else:
            unit_landing = pr.landing_tmt
        unit_cny = pr.item_price_cny + pr.domestic_shipping_cny
        line = {
            "item_id": it.id, "offer_id": o.id, "product_id": it.product_id, "product": it.product.name,
            "emoji": it.product.emoji, "title": o.title_en or o.title, "url": o.url, "image_url": o.image_url,
            "variant": it.variant, "qty": it.qty, "unit_cny": round(unit_cny, 2), "weight_kg": pr.weight_kg,
            "weight_is_estimate": pr.weight_is_estimate, "unit_landing_tmt": round(unit_landing, 2),
            "unit_sell_tmt": pr.sell_tmt, "seller": o.seller.name if o.seller else None,
            "line_cny": round(unit_cny * it.qty, 2), "line_landing_tmt": round(unit_landing * it.qty, 2),
            "line_profit_tmt": round((pr.sell_tmt - unit_landing) * it.qty, 2),
        }
        lines.append(line)
        seller_ids.add(o.seller_id)
        totals["goods_cny"] += unit_cny * it.qty
        totals["goods_tmt"] += pr.goods_tmt * it.qty
        totals["cargo_tmt"] += pr.cargo_tmt * it.qty
        totals["landing_tmt"] += unit_landing * it.qty
        totals["sell_tmt"] += pr.sell_tmt * it.qty
        totals["profit_tmt"] += (pr.sell_tmt - unit_landing) * it.qty
        totals["weight_kg"] += pr.weight_kg * it.qty
        totals["units"] += it.qty
        totals["weight_has_estimates"] |= pr.weight_is_estimate
    for k, v in totals.items():
        if isinstance(v, float):
            totals[k] = round(v, 2)
    # Arrival: the slowest seller determines it
    ests = [delivery_estimate(db, sid) for sid in seller_ids] or [delivery_estimate(db)]
    worst = max(ests, key=lambda e: e["max_days"])
    start = order.placed_at or utcnow()
    return {"order": row_dict(order), "lines": lines, "totals": totals, "delivery": worst,
            "arrival": delivery.arrival_window(worst, start)}


def export_text(summary: dict, lang: str = "en") -> str:
    """Clean order list for WhatsApp / Telegram."""
    head = {"en": "🛒 Order", "ru": "🛒 Заказ", "tk": "🛒 Sargyt"}.get(lang, "🛒 Order")
    qty_w = {"en": "qty", "ru": "кол-во", "tk": "sany"}.get(lang, "qty")
    lines = [f"{head} #{summary['order']['id']} — {datetime.now(timezone.utc).replace(tzinfo=None):%d.%m.%Y}", ""]
    for i, l in enumerate(summary["lines"], 1):
        lines.append(f"{i}. {l['product']} — {qty_w}: {l['qty']}" + (f" — {l['variant']}" if l.get("variant") else ""))
        if l.get("title"):
            lines.append(f"   {l['title'][:80]}")
        lines.append(f"   ¥{l['unit_cny']:.2f} × {l['qty']} = ¥{l['line_cny']:.2f}")
        if l.get("url"):
            lines.append(f"   🔗 {l['url']}")
        if l.get("image_url") and str(l["image_url"]).startswith("http"):
            lines.append(f"   📷 {l['image_url']}")
    t = summary["totals"]
    lines += ["", f"Σ ¥{t['goods_cny']:.2f} ≈ {t['goods_tmt']:.0f} TMT · {t['units']} pcs · ~{t['weight_kg']:.1f} kg"
              f" · cargo ≈ {t['cargo_tmt']:.0f} TMT",
              f"ETA: {summary['arrival']['earliest']} – {summary['arrival']['latest']}"]
    return "\n".join(lines)


def place_order(db: Session, order: Order) -> Shipment:
    summary = order_summary(db, order)
    by_id = {l["item_id"]: l for l in summary["lines"]}
    for it in order.items:
        l = by_id.get(it.id)
        if l:
            it.unit_price_cny, it.unit_landing_tmt, it.weight_kg = l["unit_cny"], l["unit_landing_tmt"], l["weight_kg"]
    order.status = "placed"
    order.placed_at = utcnow()
    seller_ids = {it.offer.seller_id for it in order.items if it.offer and it.offer.seller_id}
    ship = Shipment(order_id=order.id, seller_id=next(iter(seller_ids)) if len(seller_ids) == 1 else None,
                    code=f"SC-{order.id:04d}", status="ordered", ordered_at=utcnow(),
                    weight_kg=summary["totals"]["weight_kg"], goods_cost_cny=summary["totals"]["goods_cny"],
                    cargo_cost_tmt=summary["totals"]["cargo_tmt"])
    db.add(ship)
    db.commit()
    return ship


STATUS_FIELD = {"ordered": "ordered_at", "china_warehouse": "china_warehouse_at", "in_transit": "in_transit_at",
                "arrived": "arrived_at", "sold_out": "sold_out_at", "delivered": "delivered_at",
                "cash_received": "cash_received_at", "dispatched": "dispatched_at"}


def set_shipment_status(db: Session, ship: Shipment, status: str) -> Shipment:
    """Advance a shipment. 'delivered' and 'cash_received' are milestones set by my person (don't change main status).
    Arrival automatically adds the order's items to stock."""
    if status not in STATUS_FIELD:
        raise ValueError(f"unknown status {status}")
    field = STATUS_FIELD[status]
    if getattr(ship, field) is None:
        setattr(ship, field, utcnow())
    if status in SHIPMENT_STATUSES:
        ship.status = status
    if status in ("arrived", "sold_out", "delivered", "cash_received") and ship.order:
        if ship.arrived_at is None:
            ship.arrived_at = utcnow()
        if ship.status not in ("arrived", "sold_out"):
            ship.status = "arrived"
        already = db.scalar(select(func.count(StockMovement.id)).where(StockMovement.shipment_id == ship.id,
                                                                       StockMovement.kind == "arrival"))
        if not already:
            for it in ship.order.items:
                db.add(StockMovement(product_id=it.product_id, qty=it.qty, kind="arrival", shipment_id=ship.id,
                                     unit_cost_tmt=it.unit_landing_tmt, is_mock=ship.is_mock))
    db.commit()
    return ship


def shipment_dict(db: Session, s: Shipment) -> dict:
    d = row_dict(s)
    d["seller_name"] = s.seller.name if s.seller else None
    d["items"] = [{"product": it.product.name, "emoji": it.product.emoji, "qty": it.qty}
                  for it in (s.order.items if s.order else [])]
    est = delivery_estimate(db, s.seller_id)
    if s.ordered_at and not s.arrived_at:
        d["eta"] = delivery.arrival_window(est, s.ordered_at)
        d["days_in_transit"] = (utcnow() - s.ordered_at).days
    if s.ordered_at and s.arrived_at:
        d["actual_days"] = round((s.arrived_at - s.ordered_at).total_seconds() / 86400, 1)
    return d


# ============================================================ weekly report

def weekly_report(db: Session, now: datetime | None = None) -> dict:
    now = now or utcnow()
    start = now - timedelta(days=7)
    sales = db.scalars(select(Sale).where(Sale.sold_at >= start, Sale.sold_at <= now + timedelta(days=1))).all()
    by_prod: dict[int, dict] = {}
    for s in sales:
        b = by_prod.setdefault(s.product_id, {"product_id": s.product_id, "name": s.product.name,
                                              "emoji": s.product.emoji, "units": 0, "revenue": 0.0, "cost": 0.0})
        b["units"] += s.qty
        b["revenue"] += s.qty * s.unit_price_tmt
        b["cost"] += s.qty * (s.unit_cost_tmt or 0)
    sold = []
    for b in by_prod.values():
        b["profit"] = round(b["revenue"] - b["cost"], 2)
        b["margin_pct"] = round(b["profit"] / b["revenue"] * 100, 1) if b["revenue"] else 0
        sold.append(b)
    sold.sort(key=lambda b: b["profit"], reverse=True)

    reorder, stop = [], []
    for p in db.scalars(select(Product)).all():
        stock = stock_for(db, p.id)
        s30 = sales_between(db, p.id, now - timedelta(days=30), now + timedelta(days=1))
        if s30 == 0 and stock <= 0:
            continue
        weekly_rate = s30 / 4.3
        est = delivery_estimate(db)
        weeks_cover = stock / weekly_rate if weekly_rate else 99
        lead_weeks = est["typical_days"] / 7
        margin = None
        ps = [s for s in sales if s.product_id == p.id]
        if ps:
            rev = sum(s.qty * s.unit_price_tmt for s in ps)
            margin = (rev - sum(s.qty * (s.unit_cost_tmt or 0) for s in ps)) / rev * 100 if rev else None
        if weekly_rate > 0 and weeks_cover < lead_weeks + 1 and p.trend_state != "fading":
            qty = max(5, round(weekly_rate * (lead_weeks + 3) - stock))
            reorder.append({"product_id": p.id, "name": p.name, "emoji": p.emoji, "stock": stock,
                            "weekly_rate": round(weekly_rate, 1), "suggested_qty": qty,
                            "reason": f"{stock} left ≈ {weeks_cover:.1f} weeks; delivery takes ~{lead_weeks:.1f} weeks"})
        elif (p.trend_state == "fading" and weekly_rate < 2) or (margin is not None and margin < 20) or \
                (stock > 0 and s30 == 0):
            why = ("trend fading and slow sales" if p.trend_state == "fading" else
                   f"real margin only {margin:.0f}%" if margin is not None and margin < 20 else
                   f"{stock} in stock, nothing sold in 30 days")
            stop.append({"product_id": p.id, "name": p.name, "emoji": p.emoji, "stock": stock, "reason": why})
    totals = {"units": sum(b["units"] for b in sold), "revenue": round(sum(b["revenue"] for b in sold), 2),
              "profit": round(sum(b["profit"] for b in sold), 2)}
    totals["margin_pct"] = round(totals["profit"] / totals["revenue"] * 100, 1) if totals["revenue"] else 0
    return {"from": start.date().isoformat(), "to": now.date().isoformat(), "sold": sold, "totals": totals,
            "reorder": reorder, "stop": stop}


def match_local_post(text: str, products: list[Product]) -> Product | None:
    """Match a Turkmen/Russian channel post to one of my products (word stems, any language in keywords)."""
    t = product_filter.norm(text)
    best, best_hits = None, 0
    for p in products:
        words = set(product_filter.name_tokens(p.name))
        for k in (p.keywords or []):
            words |= set(re.findall(r"\w+", product_filter.norm(k)))
        words = {w for w in words if len(w) > 3 and w not in {"mini", "мини", "toys", "oyuncak", "игрушки"}}
        hits = sum(1 for w in words if re.search(r"(?<!\w)" + re.escape(w[:max(4, len(w) - 2)]), t))
        if hits > best_hits:
            best, best_hits = p, hits
    return best
