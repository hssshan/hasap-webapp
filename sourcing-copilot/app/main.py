"""FastAPI app: JSON API + the mobile PWA (served from app/static)."""
from __future__ import annotations

import hashlib
import hmac
import logging
import secrets
import time
import uuid
from contextlib import asynccontextmanager
from datetime import timedelta
from pathlib import Path
from typing import Any

from fastapi import Body, Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from . import ai, pricing, scheduler, scoring, services
from .adapters.fx import fx_view
from .adapters.manual import import_paste
from .adapters.registry import REGISTRY
from .adapters.vidiq_import import import_payload
from .bot import handlers as bot_handlers
from .bot.runner import RUNNER
from .config import BASE_DIR, data_dir, env, env_bool, parse_chat_ids
from .db import (
    LocalPrice, Offer, Order, OrderItem, Product, Sale, Seller, SellerEvent, Shipment, StockMovement, TelegramChat,
    TrendSignal, TrendSnapshot, get_db, init_engine, row_dict, SessionLocal,
)
from .settings_store import DEFAULTS, all_settings, get_setting, set_setting

log = logging.getLogger("copilot")
logging.basicConfig(level=env("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(name)s: %(message)s")
STATIC = Path(__file__).parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_engine()
    if env_bool("SEED_MOCK_DATA", True):
        from .seed import seed
        db = SessionLocal()
        try:
            res = seed(db)
            if res.get("seeded"):
                log.info("Seeded demo data")
        finally:
            db.close()
        db = SessionLocal()
        try:
            from .seed import add_demo_videos
            add_demo_videos(db)
        finally:
            db.close()
    scheduler.start()
    RUNNER.start()
    if env_bool("IMAGE_FETCH_ENABLED", True):
        from .images import refresh_in_background
        refresh_in_background()
    yield
    RUNNER.stop()
    scheduler.shutdown()


app = FastAPI(title="Sourcing Copilot", lifespan=lifespan)


# ------------------------------------------------------------------ simple single-user auth
COOKIE = "copilot_session"


def _secret() -> bytes:
    return (env("APP_SECRET_KEY") or env("APP_PASSWORD") or "dev").encode()


def make_token() -> str:
    ts = str(int(time.time()))
    sig = hmac.new(_secret(), ts.encode(), hashlib.sha256).hexdigest()
    return f"{ts}.{sig}"


def token_ok(tok: str | None) -> bool:
    if not tok or "." not in tok:
        return False
    ts, sig = tok.split(".", 1)
    good = hmac.new(_secret(), ts.encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(sig, good) and ts.isdigit() and time.time() - int(ts) < 90 * 86400


OPEN_PATHS = {"/api/auth/login", "/api/auth/status", "/api/import/trends", "/api/health"}


@app.middleware("http")
async def auth_mw(request: Request, call_next):
    path = request.url.path
    if env("APP_PASSWORD") and (path.startswith("/api/") or path.startswith("/uploads/")) and path not in OPEN_PATHS \
            and not path.startswith("/api/placeholder/"):
        if not token_ok(request.cookies.get(COOKIE)):
            return JSONResponse({"detail": "login required"}, status_code=401)
    return await call_next(request)


@app.get("/api/health")
def health():
    return {"ok": True}


@app.get("/api/auth/status")
def auth_status(request: Request):
    need = bool(env("APP_PASSWORD"))
    return {"password_required": need, "logged_in": (not need) or token_ok(request.cookies.get(COOKIE))}


@app.post("/api/auth/login")
def login(payload: dict = Body(...)):
    pw = env("APP_PASSWORD")
    if pw and not hmac.compare_digest(str(payload.get("password", "")), pw):
        raise HTTPException(401, "wrong password")
    resp = JSONResponse({"ok": True})
    resp.set_cookie(COOKIE, make_token(), max_age=90 * 86400, httponly=True, samesite="lax",
                    secure=env_bool("COOKIE_SECURE", False))
    return resp


@app.post("/api/auth/logout")
def logout():
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(COOKIE)
    return resp


# ------------------------------------------------------------------ helpers
def get_or_404(db: Session, model, id_: int):
    obj = db.get(model, id_)
    if obj is None:
        raise HTTPException(404, f"{model.__name__} {id_} not found")
    return obj


def _num(v: Any, cast=float):
    if v in (None, "", "null"):
        return None
    try:
        return cast(v)
    except (TypeError, ValueError):
        return None


# ------------------------------------------------------------------ home / recommendations
@app.get("/api/home")
def home(db: Session = Depends(get_db), limit: int = 10):
    recs = services.recommendations(db, limit=limit)
    return {
        "recommendations": recs,
        "profit_30d": services.profit_summary(db, 30),
        "basket_count": sum(i.qty for i in services.draft_order(db).items),
        "shipments_active": len(db.scalars(select(Shipment).where(Shipment.status.in_(["ordered", "china_warehouse", "in_transit"]))).all()),
        "mock_data": bool(db.scalar(select(Product.id).where(Product.is_mock.is_(True)).limit(1))),
        "ai": ai.available(),
    }


# ------------------------------------------------------------------ products
@app.get("/api/products")
def list_products(db: Session = Depends(get_db), show: str = "active"):
    q = select(Product).options(selectinload(Product.offers))
    if show == "active":
        q = q.where(Product.dismissed.is_(False))
    elif show == "watched":
        q = q.where(Product.watched.is_(True))
    elif show == "dismissed":
        q = q.where(Product.dismissed.is_(True))
    cards = [services.product_card(db, p) for p in db.scalars(q).all()]
    cards.sort(key=lambda c: c["trend_score"], reverse=True)
    return cards


@app.post("/api/products")
def create_product(payload: dict = Body(...), db: Session = Depends(get_db)):
    name = (payload.get("name") or "").strip().lower()
    if not name:
        raise HTTPException(400, "name required")
    from .product_filter import Classification, canonical_product, detect_brand
    canon = canonical_product(name)
    brand = detect_brand(name)
    cls = Classification("product", canon[0] if canon else name, payload.get("category") or (canon[1] if canon else "other"),
                         canon[2] if canon else "", "manual", bool(brand),
                         f"Mentions '{brand}': branded/licensed goods can be seized by customs." if brand else None, 1)
    p = services.find_or_create_product(db, cls)
    db.commit()
    services.ensure_search_terms(db, p)
    services.recompute_trends(db)
    return {"id": p.id, "name": p.name}


@app.get("/api/products/{pid}")
def product_detail(pid: int, db: Session = Depends(get_db)):
    p = get_or_404(db, Product, pid)
    terms = services.ensure_search_terms(db, p)
    ranked = services.rank_offers(db, p)
    sigs = db.scalars(select(TrendSignal).where(TrendSignal.product_id == pid).order_by(TrendSignal.views.desc())).all()
    snaps = db.scalars(select(TrendSnapshot).where(TrendSnapshot.product_id == pid).order_by(TrendSnapshot.day)).all()
    lps = services.local_prices_for(db, pid)
    card = services.product_card(db, p)
    return {
        "product": row_dict(p), "card": card, "trend": p.trend_breakdown or {},
        "signals": [row_dict(s, exclude=("raw",)) for s in sigs],
        "history": [{"day": s.day.isoformat(), "score": s.score} for s in snaps],
        "offers": ranked, "top3": ranked[:3], "why_winner": scoring.why_winner(ranked),
        "search_terms": terms, "search_links": services.marketplace_links(terms.get("zh") or [p.name]),
        "local_prices": [row_dict(l) for l in lps],
        "captions": p.captions or None,
        "stock": services.stock_for(db, pid),
    }


@app.patch("/api/products/{pid}")
def update_product(pid: int, payload: dict = Body(...), db: Session = Depends(get_db)):
    p = get_or_404(db, Product, pid)
    for k in ("name", "category", "emoji", "image_url", "notes", "watched", "dismissed", "est_weight_kg",
              "counterfeit_risk", "counterfeit_reason", "keywords", "search_terms"):
        if k in payload:
            setattr(p, k, payload[k])
    db.commit()
    return row_dict(p)


@app.delete("/api/products/{pid}")
def delete_product(pid: int, db: Session = Depends(get_db)):
    db.delete(get_or_404(db, Product, pid))
    db.commit()
    return {"ok": True}


@app.post("/api/products/{pid}/captions")
def make_captions(pid: int, db: Session = Depends(get_db)):
    p = get_or_404(db, Product, pid)
    card = services.product_card(db, p)
    best = services.rank_offers(db, p)
    feats = (best[0]["offer"].get("praises") or []) if best else []
    caps = ai.captions(db, p.name, card.get("sell_tmt") or 0, feats)
    p.captions = caps
    db.commit()
    return caps


@app.post("/api/products/{pid}/local-prices")
def add_local_price(pid: int, payload: dict = Body(...), db: Session = Depends(get_db)):
    get_or_404(db, Product, pid)
    price = _num(payload.get("price_tmt"))
    if not price:
        raise HTTPException(400, "price_tmt required")
    lp = LocalPrice(product_id=pid, price_tmt=price, source="manual", channel=payload.get("where") or "manual",
                    text=payload.get("note"))
    db.add(lp)
    db.commit()
    return row_dict(lp)


@app.delete("/api/local-prices/{lid}")
def delete_local_price(lid: int, db: Session = Depends(get_db)):
    db.delete(get_or_404(db, LocalPrice, lid))
    db.commit()
    return {"ok": True}


# ------------------------------------------------------------------ signals (review filter decisions)
@app.get("/api/signals")
def list_signals(db: Session = Depends(get_db), classification: str | None = None, limit: int = 100):
    q = select(TrendSignal).order_by(TrendSignal.collected_at.desc()).limit(limit)
    if classification:
        q = q.where(TrendSignal.classification == classification)
    return [row_dict(s, exclude=("raw",)) for s in db.scalars(q)]


@app.patch("/api/signals/{sid}")
def reclassify_signal(sid: int, payload: dict = Body(...), db: Session = Depends(get_db)):
    """Manually override the filter: mark as noise, or assign to a product (by name)."""
    s = get_or_404(db, TrendSignal, sid)
    label = payload.get("classification")
    if label == "noise":
        s.classification, s.product_id, s.class_reason = "noise", None, "manual: noise"
    elif label == "product":
        from .product_filter import Classification, canonical_product
        name = (payload.get("product_name") or s.extracted_product or "").strip().lower()
        if not name:
            raise HTTPException(400, "product_name required")
        canon = canonical_product(name)
        cls = Classification("product", canon[0] if canon else name, canon[1] if canon else "other",
                             canon[2] if canon else "", "manual", False, None, 1)
        p = services.find_or_create_product(db, cls)
        s.classification, s.product_id, s.extracted_product, s.class_reason = "product", p.id, p.name, "manual"
    db.commit()
    services.recompute_trends(db)
    return row_dict(s, exclude=("raw",))


# ------------------------------------------------------------------ offers
ALLOWED_IMG = {".png", ".jpg", ".jpeg", ".webp", ".gif"}


@app.post("/api/offers/extract")
async def extract_offer(url: str = Form(""), files: list[UploadFile] = File(default=[]), db: Session = Depends(get_db)):
    saved: list[Path] = []
    for f in files[:6]:
        ext = Path(f.filename or "x.jpg").suffix.lower()
        if ext not in ALLOWED_IMG:
            continue
        data = await f.read()
        if len(data) > 12 * 1024 * 1024:
            raise HTTPException(413, "screenshot too large (max 12 MB)")
        path = data_dir() / "uploads" / f"{uuid.uuid4().hex}{ext}"
        path.write_bytes(data)
        saved.append(path)
    res = ai.extract_offer(db, saved, url)
    res["screenshots"] = [f"/uploads/{p.name}" for p in saved]
    res["platform"] = "1688" if "1688" in url else "tmall" if "tmall" in url else "taobao" if url else None
    return res


SELLER_FIELDS = {"store_name": "name", "store_age_years": "store_age_years", "store_rating": "rating",
                 "store_total_sales": "total_sales", "response_rate": "response_rate", "dispute_rate": "dispute_rate",
                 "is_factory": "is_factory"}
OFFER_NUM = {"price_cny": float, "domestic_shipping_cny": float, "sales_count": int, "rating": float, "review_count": int,
             "buyer_photos": int, "repeat_purchase_rate": float, "weight_kg": float}


def _apply_offer(db: Session, o: Offer, payload: dict) -> None:
    for k in ("url", "title", "title_en", "size", "variants", "image_url", "platform"):
        if k in payload:
            setattr(o, k, payload[k] or None)
    for k, cast in OFFER_NUM.items():
        if k in payload:
            setattr(o, k, _num(payload[k], cast))
    o.price_cny = o.price_cny or 0
    o.domestic_shipping_cny = o.domestic_shipping_cny or 0
    for k in ("price_tiers", "praises", "complaints", "screenshots"):
        if k in payload and isinstance(payload[k], list):
            setattr(o, k, payload[k])
    if "ai_filled" in payload:
        o.ai_filled = bool(payload["ai_filled"])
    # seller: existing id, or create/update from store fields
    if payload.get("seller_id"):
        o.seller_id = int(payload["seller_id"])
    elif payload.get("store_name"):
        name = payload["store_name"].strip()
        seller = db.scalar(select(Seller).where(Seller.name == name)) or Seller(name=name, platform=o.platform or "taobao")
        db.add(seller)
        for src, dst in SELLER_FIELDS.items():
            if src in payload and payload[src] not in (None, "") and src != "store_name":
                val = payload[src]
                if dst == "is_factory":
                    val = val if isinstance(val, bool) else str(val).lower() in ("1", "true", "yes", "factory")
                elif dst == "total_sales":
                    val = _num(val, int)
                else:
                    val = _num(val)
                setattr(seller, dst, val)
        db.flush()
        o.seller_id = seller.id


@app.post("/api/offers")
def create_offer(payload: dict = Body(...), db: Session = Depends(get_db)):
    pid = payload.get("product_id")
    get_or_404(db, Product, int(pid or 0))
    o = Offer(product_id=int(pid))
    db.add(o)
    _apply_offer(db, o, payload)
    db.commit()
    return services.offer_dict(o)


@app.patch("/api/offers/{oid}")
def update_offer(oid: int, payload: dict = Body(...), db: Session = Depends(get_db)):
    o = get_or_404(db, Offer, oid)
    _apply_offer(db, o, payload)
    db.commit()
    return services.offer_dict(o)


@app.get("/api/offers/{oid}")
def get_offer(oid: int, db: Session = Depends(get_db)):
    o = get_or_404(db, Offer, oid)
    d = services.offer_dict(o)
    d["pricing"] = pricing.price_offer(o, services.rules(db)).as_dict()
    return d


@app.delete("/api/offers/{oid}")
def delete_offer(oid: int, db: Session = Depends(get_db)):
    db.delete(get_or_404(db, Offer, oid))
    db.commit()
    return {"ok": True}


# ------------------------------------------------------------------ sellers
@app.get("/api/sellers")
def list_sellers(db: Session = Depends(get_db)):
    out = []
    for s in db.scalars(select(Seller).options(selectinload(Seller.events), selectinload(Seller.offers))):
        out.append({**row_dict(s), "score": services.seller_score(db, s), "offers": len(s.offers), "events": len(s.events)})
    out.sort(key=lambda x: x["score"]["score"], reverse=True)
    return out


@app.get("/api/sellers/{sid}")
def seller_detail(sid: int, db: Session = Depends(get_db)):
    s = get_or_404(db, Seller, sid)
    return {**row_dict(s), "score": services.seller_score(db, s), "events": [row_dict(e) for e in s.events],
            "offers": [services.offer_dict(o) | {"product": o.product.name} for o in s.offers],
            "delivery": services.delivery_estimate(db, s.id)}


@app.patch("/api/sellers/{sid}")
def update_seller(sid: int, payload: dict = Body(...), db: Session = Depends(get_db)):
    s = get_or_404(db, Seller, sid)
    for k in ("name", "platform", "url", "location", "notes"):
        if k in payload:
            setattr(s, k, payload[k])
    for k in ("store_age_years", "rating", "response_rate", "dispute_rate"):
        if k in payload:
            setattr(s, k, _num(payload[k]))
    if "total_sales" in payload:
        s.total_sales = _num(payload["total_sales"], int)
    if "is_factory" in payload:
        s.is_factory = payload["is_factory"]
    db.commit()
    return row_dict(s)


@app.post("/api/sellers/{sid}/events")
def add_seller_event(sid: int, payload: dict = Body(...), db: Session = Depends(get_db)):
    get_or_404(db, Seller, sid)
    kind = payload.get("kind")
    if kind not in scoring.EVENT_PENALTY:
        raise HTTPException(400, f"kind must be one of {list(scoring.EVENT_PENALTY)}")
    e = SellerEvent(seller_id=sid, kind=kind, days_late=_num(payload.get("days_late"), int), note=payload.get("note"),
                    shipment_id=_num(payload.get("shipment_id"), int))
    db.add(e)
    db.commit()
    return row_dict(e)


@app.delete("/api/seller-events/{eid}")
def delete_seller_event(eid: int, db: Session = Depends(get_db)):
    db.delete(get_or_404(db, SellerEvent, eid))
    db.commit()
    return {"ok": True}


# ------------------------------------------------------------------ pricing calculator
@app.get("/api/pricing/calc")
def calc(price_cny: float, shipping_cny: float = 0, weight_kg: float | None = None, name: str = "",
         markup: float | None = None, db: Session = Depends(get_db)):
    return pricing.calculate(price_cny, shipping_cny, weight_kg, services.rules(db), name=name, markup=markup).as_dict()


# ------------------------------------------------------------------ basket & orders
@app.get("/api/basket")
def basket(db: Session = Depends(get_db)):
    return services.order_summary(db, services.draft_order(db))


@app.post("/api/basket/items")
def basket_add(payload: dict = Body(...), db: Session = Depends(get_db)):
    offer_id = payload.get("offer_id")
    if not offer_id and payload.get("product_id"):
        p = get_or_404(db, Product, int(payload["product_id"]))
        ranked = services.rank_offers(db, p)
        if not ranked:
            raise HTTPException(400, "This product has no offers yet: add one first (Find offers).")
        offer_id = ranked[0]["offer_id"]
    o = get_or_404(db, Offer, int(offer_id or 0))
    qty = max(1, int(payload.get("qty") or 10))
    services.add_to_basket(db, o, qty, payload.get("variant"))
    return services.order_summary(db, services.draft_order(db))


@app.patch("/api/basket/items/{iid}")
def basket_update(iid: int, payload: dict = Body(...), db: Session = Depends(get_db)):
    it = get_or_404(db, OrderItem, iid)
    if it.order.status != "draft":
        raise HTTPException(400, "order already placed")
    if "qty" in payload:
        it.qty = max(1, int(payload["qty"]))
    if "variant" in payload:
        it.variant = payload["variant"]
    db.commit()
    return services.order_summary(db, it.order)


@app.delete("/api/basket/items/{iid}")
def basket_remove(iid: int, db: Session = Depends(get_db)):
    it = get_or_404(db, OrderItem, iid)
    order = it.order
    db.delete(it)
    db.commit()
    db.refresh(order)
    return services.order_summary(db, order)


@app.get("/api/orders/{oid}/export", response_class=PlainTextResponse)
def export_order(oid: int, lang: str = "en", db: Session = Depends(get_db)):
    return services.export_text(services.order_summary(db, get_or_404(db, Order, oid)), lang)


@app.post("/api/basket/place")
def place(db: Session = Depends(get_db)):
    order = services.draft_order(db)
    if not order.items:
        raise HTTPException(400, "basket is empty")
    ship = services.place_order(db, order)
    return {"order_id": order.id, "shipment_id": ship.id, "code": ship.code}


@app.post("/api/orders/{oid}/send")
def send_order(oid: int, payload: dict = Body(default={}), db: Session = Depends(get_db)):
    order = get_or_404(db, Order, oid)
    if not RUNNER.active:
        raise HTTPException(400, "Telegram bot not connected: copy the list and send it manually.")
    chat = bot_handlers.send_order(db, RUNNER.api, order, payload.get("lang", "ru"))
    if not chat:
        raise HTTPException(400, "Set your person's chat ID in Settings → Telegram bot first.")
    return {"ok": True, "chat_id": chat}


@app.get("/api/orders")
def list_orders(db: Session = Depends(get_db)):
    orders = db.scalars(select(Order).where(Order.status != "draft").order_by(Order.id.desc())).all()
    return [services.order_summary(db, o) for o in orders]


@app.get("/api/orders/{oid}")
def get_order(oid: int, db: Session = Depends(get_db)):
    return services.order_summary(db, get_or_404(db, Order, oid))


# ------------------------------------------------------------------ shipments
@app.get("/api/shipments")
def list_shipments(db: Session = Depends(get_db)):
    ships = db.scalars(select(Shipment).order_by(Shipment.id.desc())).all()
    return {"shipments": [services.shipment_dict(db, s) for s in ships], "delivery": services.delivery_estimate(db),
            "statuses": ["ordered", "china_warehouse", "in_transit", "arrived", "sold_out"]}


@app.post("/api/shipments/{sid}/status")
def ship_status(sid: int, payload: dict = Body(...), db: Session = Depends(get_db)):
    s = get_or_404(db, Shipment, sid)
    try:
        services.set_shipment_status(db, s, payload.get("status", ""))
    except ValueError as e:
        raise HTTPException(400, str(e))
    return services.shipment_dict(db, s)


@app.patch("/api/shipments/{sid}")
def ship_update(sid: int, payload: dict = Body(...), db: Session = Depends(get_db)):
    s = get_or_404(db, Shipment, sid)
    for k in ("code", "notes"):
        if k in payload:
            setattr(s, k, payload[k])
    for k in ("weight_kg", "cargo_cost_tmt", "goods_cost_cny"):
        if k in payload:
            setattr(s, k, _num(payload[k]))
    for k in ("ordered_at", "dispatched_at", "china_warehouse_at", "in_transit_at", "arrived_at", "delivered_at",
              "cash_received_at", "sold_out_at"):
        if k in payload:
            setattr(s, k, services._parse_dt(payload[k]))
    if "seller_id" in payload:
        s.seller_id = _num(payload["seller_id"], int)
    db.commit()
    return services.shipment_dict(db, s)


# ------------------------------------------------------------------ sales & stock
@app.post("/api/sales")
def add_sale(payload: dict = Body(...), db: Session = Depends(get_db)):
    p = get_or_404(db, Product, int(payload.get("product_id") or 0))
    qty = max(1, int(payload.get("qty") or 1))
    price = _num(payload.get("unit_price_tmt"))
    if not price:
        raise HTTPException(400, "unit_price_tmt required")
    return services.record_sale(db, p, qty, price, channel=payload.get("channel"), note=payload.get("note"))


@app.post("/api/sales/quick")
def quick_sale(payload: dict = Body(...), db: Session = Depends(get_db)):
    from .sales_parser import match_product, parse_sale
    text = payload.get("text", "")
    if not text.lower().lstrip("/").startswith(("sold", "sell", "sale", "прод", "sat")):
        text = "sold " + text
    parsed = parse_sale(text)
    if not parsed:
        raise HTTPException(400, "Format: sold 2 hamster 250")
    prod, cands = match_product(parsed.name, db.scalars(select(Product)).all())
    if not prod:
        return JSONResponse({"detail": f"Which product is “{parsed.name}”?",
                             "candidates": [{"id": c.id, "name": c.name} for c in cands], "parsed": parsed.__dict__},
                            status_code=409)
    res = services.record_sale(db, prod, parsed.qty, parsed.unit_price)
    return {**res, "product": prod.name, "parsed": parsed.__dict__}


@app.get("/api/sales")
def list_sales(db: Session = Depends(get_db), days: int = 30):
    from .db import utcnow
    rows = db.scalars(select(Sale).where(Sale.sold_at >= utcnow() - timedelta(days=days)).order_by(Sale.sold_at.desc())).all()
    return {"sales": [{**row_dict(s), "product": s.product.name, "emoji": s.product.emoji} for s in rows],
            "summary": services.profit_summary(db, days)}


@app.delete("/api/sales/{sid}")
def remove_sale(sid: int, db: Session = Depends(get_db)):
    services.delete_sale(db, get_or_404(db, Sale, sid))
    return {"ok": True}


@app.get("/api/stock")
def stock(db: Session = Depends(get_db)):
    return services.stock_overview(db)


@app.post("/api/stock/adjust")
def stock_adjust(payload: dict = Body(...), db: Session = Depends(get_db)):
    p = get_or_404(db, Product, int(payload.get("product_id") or 0))
    qty = int(payload.get("qty") or 0)
    db.add(StockMovement(product_id=p.id, qty=qty, kind="adjust", unit_cost_tmt=_num(payload.get("unit_cost_tmt"))))
    db.commit()
    return {"stock": services.stock_for(db, p.id)}


@app.get("/api/report/weekly")
def report(db: Session = Depends(get_db)):
    return services.weekly_report(db)


# ------------------------------------------------------------------ imports
def _check_import_secret(request: Request) -> None:
    secret = env("TREND_IMPORT_SECRET")
    if not secret:
        raise HTTPException(503, "TREND_IMPORT_SECRET is not set in .env: import endpoint disabled")
    given = request.headers.get("x-import-secret") or ""
    auth = request.headers.get("authorization") or ""
    if auth.lower().startswith("bearer "):
        given = given or auth[7:]
    if not hmac.compare_digest(given.strip(), secret):
        raise HTTPException(401, "bad import secret")


@app.post("/api/import/trends")
def import_trends(request: Request, payload: Any = Body(...), db: Session = Depends(get_db)):
    """Authenticated endpoint for the weekly Claude scheduled task (vidIQ results)."""
    _check_import_secret(request)
    res = import_payload(db, payload)
    if res.get("alerts") and RUNNER.active:
        bot_handlers.send_alerts(db, RUNNER.api, res["alerts"])
    _refresh_images()
    return res


def _refresh_images() -> None:
    if env_bool("IMAGE_FETCH_ENABLED", True):
        from .images import refresh_in_background
        refresh_in_background()


@app.post("/api/import/trends/manual")
def import_trends_manual(payload: Any = Body(...), db: Session = Depends(get_db)):
    """Same as above but from the app's Import screen (paste the JSON the scheduled task printed)."""
    if isinstance(payload, dict) and isinstance(payload.get("json"), str):
        import json
        try:
            payload = json.loads(payload["json"])
        except json.JSONDecodeError as e:
            raise HTTPException(400, f"Not valid JSON: {e}")
    res = import_payload(db, payload)
    _refresh_images()
    return res


@app.post("/api/import/paste")
def import_paste_box(payload: dict = Body(...), db: Session = Depends(get_db)):
    text = payload.get("text", "")
    if not text.strip():
        raise HTTPException(400, "nothing pasted")
    res = import_paste(db, text, language=payload.get("language", "tr"))
    _refresh_images()
    return res


# ------------------------------------------------------------------ settings & setup
@app.get("/api/settings")
def get_settings(db: Session = Depends(get_db)):
    return {"settings": all_settings(db), "defaults": DEFAULTS, "fx": fx_view(db)}


@app.put("/api/settings/{key}")
def put_setting(key: str, payload: Any = Body(...), db: Session = Depends(get_db)):
    if key not in DEFAULTS:
        raise HTTPException(404, "unknown settings group")
    value = set_setting(db, key, payload)
    if key in ("pricing", "trends"):
        services.recompute_trends(db)
    return value


INTEGRATIONS = [
    {"key": "telegram_bot", "label": "Telegram bot", "env": ["TELEGRAM_BOT_TOKEN"], "optional": ["TELEGRAM_ALLOWED_CHAT_IDS"],
     "unlocks": "Weekly digest, trend alerts, quick sales logging (\"sold 2 hamster 250\"), /top /stock /profit /orders "
                "/shipments, sending order lists to your person with status buttons.",
     "without": "Everything works in the app; copy order lists manually.",
     "steps": ["Open Telegram, search for @BotFather, send /newbot.", "Pick a name and a username ending in 'bot'.",
               "Copy the token BotFather gives you.", "Paste it into .env as TELEGRAM_BOT_TOKEN=... and restart.",
               "Send /start to your bot, then come back here and tap 'Approve as me'.",
               "Ask your person to send /start too, and tap 'Approve as my person'."]},
    {"key": "telegram_reader", "label": "Telegram channel reader (Telethon, optional)",
     "env": ["TELEGRAM_API_ID", "TELEGRAM_API_HASH", "TELEGRAM_SESSION"],
     "unlocks": "Reads more history and channels without a web preview. Without it, public channels are still read free "
                "via their t.me/s/ web preview.",
     "without": "Web preview reader (works for most public channels).",
     "steps": ["Go to https://my.telegram.org and log in with your phone number (code arrives in Telegram).",
               "Open 'API development tools', create an app (any name).", "Copy api_id and api_hash into .env as "
               "TELEGRAM_API_ID and TELEGRAM_API_HASH.", "On your computer/server run: python -m app.cli telegram-login "
               "(type the login code Telegram sends you; the app never sees a password).",
               "Paste the printed TELEGRAM_SESSION=... line into .env and restart."]},
    {"key": "claude", "label": "Claude API (AI)", "env": ["ANTHROPIC_API_KEY"],
     "unlocks": "Screenshot reading (vision), review praise/complaint summaries, smarter product/noise filter, Chinese "
                "search terms for new products, one-line explanations, Turkmen/Russian captions.",
     "without": "Rule-based filter, template captions, manual offer form.",
     "steps": ["Go to https://console.anthropic.com and sign up.", "Add a small credit (e.g. $5). Typical use is $1–4/month.",
               "Settings → API keys → Create key.", "Paste into .env as ANTHROPIC_API_KEY=... and restart.",
               "Set a monthly budget below (AI stops when it is reached)."]},
    {"key": "youtube", "label": "YouTube Data API", "env": ["YOUTUBE_API_KEY"],
     "unlocks": "Daily scan of Turkish and Russian Shorts about toys, hauls and gift ideas.",
     "without": "vidIQ import + paste box only.",
     "steps": ["Go to https://console.cloud.google.com and create a project (free).", "APIs & Services → Library → "
               "enable 'YouTube Data API v3'.", "APIs & Services → Credentials → Create credentials → API key.",
               "Paste into .env as YOUTUBE_API_KEY=... and restart. Free quota: 10,000 units/day; we use ~1,000."]},
    {"key": "import_secret", "label": "Trend import secret (vidIQ scheduled task)", "env": ["TREND_IMPORT_SECRET"],
     "unlocks": "Lets the weekly Claude scheduled task POST vidIQ results straight into the app.",
     "without": "Paste the task's JSON output into Import → vidIQ JSON.",
     "steps": ["Make up a long random string (or run: python -c \"import secrets;print(secrets.token_urlsafe(32))\").",
               "Paste into .env as TREND_IMPORT_SECRET=... and restart.",
               "Put the same secret and your app's public URL into scheduled_task_prompt.md, then create the scheduled "
               "task in Claude (see README)."]},
    {"key": "app_password", "label": "App password", "env": ["APP_PASSWORD"],
     "unlocks": "Protects the app when it is online (required before deploying publicly).",
     "without": "Anyone with the URL can open the app; fine on your own computer only.",
     "steps": ["Choose a password and put it in .env as APP_PASSWORD=...", "Also set APP_SECRET_KEY to a random string.",
               "Restart; the app asks for the password once per device (90 days)."]},
]


@app.get("/api/setup")
def setup(db: Session = Depends(get_db)):
    integ = []
    for it in INTEGRATIONS:
        missing = [n for n in it["env"] if not env(n)]
        status = "connected" if not missing else "not connected"
        extra: dict[str, Any] = {}
        if it["key"] == "telegram_bot":
            extra = RUNNER.status()
            if env("TELEGRAM_BOT_TOKEN") and not RUNNER.active:
                status = "error"
        if it["key"] == "claude":
            extra = ai.status(db)
        integ.append({**it, "status": status, "missing": missing, **extra})
    return {
        "integrations": integ,
        "adapters": [a.status(db) for a in REGISTRY.values()],
        "telegram": {"bot": RUNNER.status(), "chats": [row_dict(c) for c in db.scalars(select(TelegramChat))],
                     "env_allowed": sorted(parse_chat_ids(env("TELEGRAM_ALLOWED_CHAT_IDS"))),
                     "person_chat_id": get_setting(db, "telegram").get("person_chat_id")},
        "public_url": env("PUBLIC_URL"),
    }


@app.post("/api/adapters/{name}/run")
def run_adapter(name: str, payload: dict = Body(default={}), db: Session = Depends(get_db)):
    if name not in REGISTRY:
        raise HTTPException(404, "unknown adapter")
    res = REGISTRY[name].safe_run(db, force=True)
    if res.get("status") == "ok":
        alerts = services.recompute_trends(db)
        if alerts and RUNNER.active:
            bot_handlers.send_alerts(db, RUNNER.api, alerts)
        _refresh_images()
    return res


@app.post("/api/trends/recompute")
def recompute(db: Session = Depends(get_db)):
    alerts = services.recompute_trends(db)
    if alerts and RUNNER.active:
        bot_handlers.send_alerts(db, RUNNER.api, alerts)
    return {"alerts": alerts}


@app.post("/api/telegram/chats/{chat_id}/approve")
def approve_chat(chat_id: int, payload: dict = Body(default={}), db: Session = Depends(get_db)):
    role = payload.get("role", "owner")
    if role not in ("owner", "person", "blocked"):
        raise HTTPException(400, "role must be owner, person or blocked")
    row = db.get(TelegramChat, chat_id) or TelegramChat(chat_id=chat_id)
    db.add(row)
    row.role = role
    if role == "person":
        set_setting(db, "telegram", {"person_chat_id": chat_id})
    elif get_setting(db, "telegram").get("person_chat_id") == chat_id:
        set_setting(db, "telegram", {"person_chat_id": None})
    db.commit()
    if RUNNER.active and role in ("owner", "person"):
        try:
            RUNNER.api.send_message(chat_id, "Approved. " + (bot_handlers.HELP if role == "owner" else bot_handlers.PERSON_HELP))
        except Exception:  # noqa: BLE001
            pass
    return row_dict(row)


@app.delete("/api/telegram/chats/{chat_id}")
def remove_chat(chat_id: int, db: Session = Depends(get_db)):
    row = db.get(TelegramChat, chat_id)
    if row:
        db.delete(row)
        db.commit()
    return {"ok": True}


@app.post("/api/telegram/digest")
def digest_now(db: Session = Depends(get_db)):
    if not RUNNER.active:
        raise HTTPException(400, "Telegram bot not connected")
    return {"sent": bot_handlers.send_digest(db, RUNNER.api)}


@app.post("/api/telegram/restart")
def restart_bot():
    from .config import load_dotenv  # re-read .env without restarting the server
    load_dotenv(BASE_DIR / ".env", override=True)
    RUNNER.stop()
    started = RUNNER.start()
    return {"started": started, **RUNNER.status()}


@app.post("/api/mock/clear")
def clear_mock(db: Session = Depends(get_db)):
    from .seed import clear_mock as _clear
    return {"deleted": _clear(db)}


@app.post("/api/mock/seed")
def seed_mock(db: Session = Depends(get_db)):
    from .seed import seed
    return seed(db)


@app.get("/api/fx")
def fx(db: Session = Depends(get_db)):
    return fx_view(db)


@app.get("/api/ai/status")
def ai_status(db: Session = Depends(get_db)):
    return ai.status(db)


# ------------------------------------------------------------------ images & static
PALETTE = [("#eef0f3", "#5b6472"), ("#f1ede7", "#6b5d4b"), ("#e9eff0", "#4f6366"), ("#efeaf1", "#625369")]


@app.get("/api/placeholder/{pid}.svg")
def placeholder(pid: int, db: Session = Depends(get_db)):
    """Neutral monogram tile, shown only until a real photo is found."""
    import html as _html
    p = db.get(Product, pid)
    words = [w for w in (p.name if p else "?").split() if w.isalpha()][:2]
    initials = _html.escape("".join(w[0] for w in words).upper() or "?")
    bg, fg = PALETTE[pid % len(PALETTE)]
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" width="240" height="240" viewBox="0 0 240 240">'
           f'<rect width="240" height="240" fill="{bg}"/>'
           f'<text x="120" y="136" font-family="Inter,Segoe UI,Arial,sans-serif" font-size="64" font-weight="600" '
           f'fill="{fg}" text-anchor="middle" letter-spacing="2">{initials}</text></svg>')
    return Response(svg, media_type="image/svg+xml", headers={"Cache-Control": "no-cache"})


@app.get("/media/{name}")
def media(name: str):
    from .images import images_dir
    path = images_dir() / Path(name).name
    if not path.exists():
        raise HTTPException(404)
    return FileResponse(path, headers={"Cache-Control": "max-age=604800"})


@app.post("/api/products/{pid}/image")
async def set_product_image(pid: int, file: UploadFile | None = File(default=None), url: str = Form(""),
                            signal_id: int | None = Form(default=None), reset: bool = Form(default=False),
                            db: Session = Depends(get_db)):
    """Set the product photo: upload a file, paste an image link, pick a trending video, or reset to automatic."""
    from . import images
    p = get_or_404(db, Product, pid)
    if reset:
        p.image_url, p.image_source, p.image_credit = None, None, None
        db.commit()
        if env_bool("IMAGE_FETCH_ENABLED", True):
            images.refresh_product_images(db, limit=1)
        return {"image_url": p.image_url, "image_credit": p.image_credit}
    local, credit = None, None
    if file is not None and file.filename:
        data = await file.read()
        if len(data) > 12 * 1024 * 1024:
            raise HTTPException(413, "photo too large (max 12 MB)")
        local, credit = images.save_upload(data), "Your photo"
    elif signal_id:
        sig = get_or_404(db, TrendSignal, signal_id)
        remote = images.signal_image_url(sig)
        local = images.cache_image(remote) if remote else None
        credit = f"From {sig.platform}: {(sig.title or '')[:80]} ({sig.url or ''})"
    elif url.strip():
        local, credit = images.cache_image(url.strip()), f"From {url.strip()[:120]}"
    if not local:
        raise HTTPException(400, "Couldn't get an image from that. Try uploading the photo instead.")
    p.image_url, p.image_source, p.image_credit = local, "manual", credit
    db.commit()
    return {"image_url": local, "image_credit": credit}


@app.post("/api/images/refresh")
def refresh_images(db: Session = Depends(get_db)):
    from . import images
    res = images.refresh_product_images(db)
    res["thumbnails"] = images.cache_signal_thumbnails(db)
    return res


@app.get("/uploads/{name}")
def uploads(name: str):
    path = (data_dir() / "uploads" / Path(name).name)
    if not path.exists():
        raise HTTPException(404)
    return FileResponse(path)


@app.get("/sw.js")
def service_worker():
    return FileResponse(STATIC / "sw.js", media_type="application/javascript", headers={"Cache-Control": "no-cache"})


@app.get("/manifest.webmanifest")
def manifest():
    return FileResponse(STATIC / "manifest.webmanifest", media_type="application/manifest+json")


app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.get("/")
@app.get("/{path:path}")
def spa(path: str = ""):
    if path.startswith("api/"):
        raise HTTPException(404)
    return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-cache"})


_ = (secrets, Seller)  # keep imports used by type checkers
