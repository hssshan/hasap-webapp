"""Database models (SQLAlchemy 2, SQLite by default, Postgres-ready).

Every table that can contain seeded demo rows has an `is_mock` flag so that
`python -m app.cli clear-mock` (or the button in Settings) removes only demo data.
"""
from __future__ import annotations

import json
from datetime import datetime, date, timezone
from typing import Any, Iterator

from sqlalchemy import (
    JSON, Boolean, Date, DateTime, Float, ForeignKey, Integer, String, Text,
    UniqueConstraint, create_engine, event,
)
from sqlalchemy.orm import (
    DeclarativeBase, Mapped, Session, mapped_column, relationship, sessionmaker,
)

from .config import database_url


class Base(DeclarativeBase):
    pass


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None).replace(microsecond=0)


class Setting(Base):
    __tablename__ = "settings"
    key: Mapped[str] = mapped_column(String(80), primary_key=True)
    value: Mapped[Any] = mapped_column(JSON)


class Product(Base):
    __tablename__ = "products"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200), index=True)
    category: Mapped[str] = mapped_column(String(60), default="plush")
    keywords: Mapped[list] = mapped_column(JSON, default=list)  # synonyms tr/ru/tk/en for matching
    search_terms: Mapped[dict] = mapped_column(JSON, default=dict)  # {"zh": [...], "tr": "...", "ru": "...", "tk": "..."}
    image_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    image_source: Mapped[str | None] = mapped_column(String(20), nullable=True)  # manual | auto
    image_credit: Mapped[str | None] = mapped_column(String(400), nullable=True)
    emoji: Mapped[str] = mapped_column(String(8), default="")
    est_weight_kg: Mapped[float | None] = mapped_column(Float, nullable=True)
    counterfeit_risk: Mapped[bool] = mapped_column(Boolean, default=False)
    counterfeit_reason: Mapped[str | None] = mapped_column(String(300), nullable=True)
    watched: Mapped[bool] = mapped_column(Boolean, default=False)
    dismissed: Mapped[bool] = mapped_column(Boolean, default=False)
    trend_score: Mapped[float] = mapped_column(Float, default=0)
    trend_state: Mapped[str] = mapped_column(String(20), default="unknown")
    trend_breakdown: Mapped[dict] = mapped_column(JSON, default=dict)
    last_alert_key: Mapped[str | None] = mapped_column(String(60), nullable=True)
    captions: Mapped[dict] = mapped_column(JSON, default=dict)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    is_mock: Mapped[bool] = mapped_column(Boolean, default=False)

    offers: Mapped[list["Offer"]] = relationship(back_populates="product", cascade="all, delete-orphan")
    signals: Mapped[list["TrendSignal"]] = relationship(back_populates="product")


class TrendSignal(Base):
    __tablename__ = "trend_signals"
    __table_args__ = (UniqueConstraint("source", "external_id", name="uq_signal_source_ext"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int | None] = mapped_column(ForeignKey("products.id", ondelete="SET NULL"), nullable=True, index=True)
    source: Mapped[str] = mapped_column(String(30))  # vidiq | youtube | google_trends | telegram | paste | sales
    platform: Mapped[str] = mapped_column(String(30), default="")  # tiktok | instagram | youtube | telegram | google | pinterest
    external_id: Mapped[str] = mapped_column(String(300))
    url: Mapped[str | None] = mapped_column(String(800), nullable=True)
    title: Mapped[str] = mapped_column(Text, default="")
    language: Mapped[str] = mapped_column(String(8), default="")
    creator: Mapped[str | None] = mapped_column(String(200), nullable=True)
    views: Mapped[int] = mapped_column(Integer, default=0)
    outlier_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    value: Mapped[float | None] = mapped_column(Float, nullable=True)  # e.g. Google Trends interest, local price
    published_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    collected_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    classification: Mapped[str] = mapped_column(String(10), default="unknown")  # product | noise | unknown
    extracted_product: Mapped[str | None] = mapped_column(String(200), nullable=True)
    class_reason: Mapped[str | None] = mapped_column(String(300), nullable=True)
    thumbnail: Mapped[str | None] = mapped_column(String(300), nullable=True)  # local cached image, "" = none found
    raw: Mapped[dict] = mapped_column(JSON, default=dict)
    is_mock: Mapped[bool] = mapped_column(Boolean, default=False)

    product: Mapped[Product | None] = relationship(back_populates="signals")


class TrendSnapshot(Base):
    __tablename__ = "trend_snapshots"
    __table_args__ = (UniqueConstraint("product_id", "day", name="uq_snapshot_day"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id", ondelete="CASCADE"), index=True)
    day: Mapped[date] = mapped_column(Date)
    score: Mapped[float] = mapped_column(Float)
    activity: Mapped[float] = mapped_column(Float, default=0)
    is_mock: Mapped[bool] = mapped_column(Boolean, default=False)


class Seller(Base):
    __tablename__ = "sellers"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    platform: Mapped[str] = mapped_column(String(20), default="taobao")
    url: Mapped[str | None] = mapped_column(String(800), nullable=True)
    store_age_years: Mapped[float | None] = mapped_column(Float, nullable=True)
    total_sales: Mapped[int | None] = mapped_column(Integer, nullable=True)
    rating: Mapped[float | None] = mapped_column(Float, nullable=True)  # 0-5
    response_rate: Mapped[float | None] = mapped_column(Float, nullable=True)  # 0-100 %
    dispute_rate: Mapped[float | None] = mapped_column(Float, nullable=True)  # % of orders with refund/dispute
    is_factory: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    location: Mapped[str | None] = mapped_column(String(100), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    is_mock: Mapped[bool] = mapped_column(Boolean, default=False)

    offers: Mapped[list["Offer"]] = relationship(back_populates="seller")
    events: Mapped[list["SellerEvent"]] = relationship(back_populates="seller", cascade="all, delete-orphan")


class SellerEvent(Base):
    __tablename__ = "seller_events"
    id: Mapped[int] = mapped_column(primary_key=True)
    seller_id: Mapped[int] = mapped_column(ForeignKey("sellers.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(20))  # delay | damage | wrong_item | missing | good
    days_late: Mapped[int | None] = mapped_column(Integer, nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    shipment_id: Mapped[int | None] = mapped_column(ForeignKey("shipments.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    is_mock: Mapped[bool] = mapped_column(Boolean, default=False)

    seller: Mapped[Seller] = relationship(back_populates="events")


class Offer(Base):
    __tablename__ = "offers"
    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id", ondelete="CASCADE"), index=True)
    seller_id: Mapped[int | None] = mapped_column(ForeignKey("sellers.id", ondelete="SET NULL"), nullable=True)
    platform: Mapped[str] = mapped_column(String(20), default="taobao")
    url: Mapped[str | None] = mapped_column(String(800), nullable=True)
    title: Mapped[str | None] = mapped_column(Text, nullable=True)
    title_en: Mapped[str | None] = mapped_column(Text, nullable=True)
    price_cny: Mapped[float] = mapped_column(Float, default=0)
    price_tiers: Mapped[list] = mapped_column(JSON, default=list)  # [{"min_qty": 10, "price_cny": 8.5}]
    domestic_shipping_cny: Mapped[float] = mapped_column(Float, default=0)
    sales_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    rating: Mapped[float | None] = mapped_column(Float, nullable=True)
    review_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    buyer_photos: Mapped[int | None] = mapped_column(Integer, nullable=True)
    repeat_purchase_rate: Mapped[float | None] = mapped_column(Float, nullable=True)  # %
    weight_kg: Mapped[float | None] = mapped_column(Float, nullable=True)
    size: Mapped[str | None] = mapped_column(String(100), nullable=True)
    variants: Mapped[str | None] = mapped_column(Text, nullable=True)
    image_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    praises: Mapped[list] = mapped_column(JSON, default=list)
    complaints: Mapped[list] = mapped_column(JSON, default=list)
    screenshots: Mapped[list] = mapped_column(JSON, default=list)
    ai_filled: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    is_mock: Mapped[bool] = mapped_column(Boolean, default=False)

    product: Mapped[Product] = relationship(back_populates="offers")
    seller: Mapped[Seller | None] = relationship(back_populates="offers")


class Order(Base):
    __tablename__ = "orders"
    id: Mapped[int] = mapped_column(primary_key=True)
    status: Mapped[str] = mapped_column(String(20), default="draft")  # draft | placed
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    placed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    is_mock: Mapped[bool] = mapped_column(Boolean, default=False)

    items: Mapped[list["OrderItem"]] = relationship(back_populates="order", cascade="all, delete-orphan")
    shipments: Mapped[list["Shipment"]] = relationship(back_populates="order")


class OrderItem(Base):
    __tablename__ = "order_items"
    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id", ondelete="CASCADE"), index=True)
    offer_id: Mapped[int | None] = mapped_column(ForeignKey("offers.id", ondelete="SET NULL"), nullable=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id", ondelete="CASCADE"))
    qty: Mapped[int] = mapped_column(Integer, default=1)
    variant: Mapped[str | None] = mapped_column(String(200), nullable=True)
    unit_price_cny: Mapped[float | None] = mapped_column(Float, nullable=True)  # frozen at placing
    unit_landing_tmt: Mapped[float | None] = mapped_column(Float, nullable=True)  # frozen at placing
    weight_kg: Mapped[float | None] = mapped_column(Float, nullable=True)
    is_mock: Mapped[bool] = mapped_column(Boolean, default=False)

    order: Mapped[Order] = relationship(back_populates="items")
    offer: Mapped[Offer | None] = relationship()
    product: Mapped[Product] = relationship()


SHIPMENT_STATUSES = ["ordered", "china_warehouse", "in_transit", "arrived", "sold_out"]


class Shipment(Base):
    __tablename__ = "shipments"
    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int | None] = mapped_column(ForeignKey("orders.id", ondelete="SET NULL"), nullable=True)
    seller_id: Mapped[int | None] = mapped_column(ForeignKey("sellers.id", ondelete="SET NULL"), nullable=True)
    code: Mapped[str] = mapped_column(String(60), default="")
    status: Mapped[str] = mapped_column(String(20), default="ordered")
    ordered_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    dispatched_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    china_warehouse_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    in_transit_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    arrived_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    cash_received_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    sold_out_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    weight_kg: Mapped[float | None] = mapped_column(Float, nullable=True)
    goods_cost_cny: Mapped[float | None] = mapped_column(Float, nullable=True)
    cargo_cost_tmt: Mapped[float | None] = mapped_column(Float, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_mock: Mapped[bool] = mapped_column(Boolean, default=False)

    order: Mapped[Order | None] = relationship(back_populates="shipments")
    seller: Mapped[Seller | None] = relationship()


class StockMovement(Base):
    __tablename__ = "stock_movements"
    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id", ondelete="CASCADE"), index=True)
    qty: Mapped[int] = mapped_column(Integer)  # + arrival/adjust, - sale
    kind: Mapped[str] = mapped_column(String(20))  # arrival | sale | adjust
    unit_cost_tmt: Mapped[float | None] = mapped_column(Float, nullable=True)
    shipment_id: Mapped[int | None] = mapped_column(ForeignKey("shipments.id", ondelete="SET NULL"), nullable=True)
    sale_id: Mapped[int | None] = mapped_column(ForeignKey("sales.id", ondelete="CASCADE"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    is_mock: Mapped[bool] = mapped_column(Boolean, default=False)


class Sale(Base):
    __tablename__ = "sales"
    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id", ondelete="CASCADE"), index=True)
    qty: Mapped[int] = mapped_column(Integer, default=1)
    unit_price_tmt: Mapped[float] = mapped_column(Float)
    unit_cost_tmt: Mapped[float | None] = mapped_column(Float, nullable=True)
    channel: Mapped[str | None] = mapped_column(String(40), nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    source: Mapped[str] = mapped_column(String(20), default="app")  # app | bot
    sold_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    is_mock: Mapped[bool] = mapped_column(Boolean, default=False)

    product: Mapped[Product] = relationship()


class LocalPrice(Base):
    __tablename__ = "local_prices"
    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int | None] = mapped_column(ForeignKey("products.id", ondelete="CASCADE"), nullable=True, index=True)
    price_tmt: Mapped[float] = mapped_column(Float)
    source: Mapped[str] = mapped_column(String(20), default="manual")  # telegram | manual
    channel: Mapped[str | None] = mapped_column(String(120), nullable=True)
    url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    text: Mapped[str | None] = mapped_column(Text, nullable=True)
    seen_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    is_mock: Mapped[bool] = mapped_column(Boolean, default=False)


class TelegramChat(Base):
    __tablename__ = "telegram_chats"
    chat_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    name: Mapped[str] = mapped_column(String(200), default="")
    username: Mapped[str | None] = mapped_column(String(100), nullable=True)
    role: Mapped[str] = mapped_column(String(20), default="pending")  # owner | person | pending | blocked
    first_seen: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class AICache(Base):
    __tablename__ = "ai_cache"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    kind: Mapped[str] = mapped_column(String(40))
    value: Mapped[Any] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class UsageLog(Base):
    __tablename__ = "usage_log"
    id: Mapped[int] = mapped_column(primary_key=True)
    provider: Mapped[str] = mapped_column(String(30))  # claude | youtube | vidiq | google_trends | telegram
    units: Mapped[float] = mapped_column(Float, default=0)  # USD for claude, quota units for youtube, credits for vidiq
    detail: Mapped[str | None] = mapped_column(String(300), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class JobRun(Base):
    __tablename__ = "job_runs"
    name: Mapped[str] = mapped_column(String(60), primary_key=True)
    last_run: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_ok: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="never")  # ok | error | skipped | never
    message: Mapped[str | None] = mapped_column(Text, nullable=True)
    retry_after: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


MOCK_TABLES = [StockMovement, Sale, SellerEvent, OrderItem, Shipment, Order, LocalPrice,
               TrendSnapshot, TrendSignal, Offer, Seller, Product]

_engine = None
_SessionLocal: sessionmaker | None = None


def init_engine(url: str | None = None):
    global _engine, _SessionLocal
    url = url or database_url()
    kwargs: dict[str, Any] = {"future": True}
    if url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False}
    _engine = create_engine(url, **kwargs)
    if url.startswith("sqlite"):
        @event.listens_for(_engine, "connect")
        def _fk_on(dbapi_conn, _):  # noqa: ANN001
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA foreign_keys=ON")
            cur.execute("PRAGMA journal_mode=WAL")
            cur.close()
    Base.metadata.create_all(_engine)
    _add_missing_columns(_engine)
    _SessionLocal = sessionmaker(bind=_engine, expire_on_commit=False, future=True)
    return _engine


def _add_missing_columns(engine) -> None:
    """Tiny auto-migration: add columns introduced in newer versions to an existing database."""
    from sqlalchemy import inspect, text

    insp = inspect(engine)
    with engine.begin() as conn:
        for table in Base.metadata.sorted_tables:
            if not insp.has_table(table.name):
                continue
            have = {c["name"] for c in insp.get_columns(table.name)}
            for col in table.columns:
                if col.name not in have:
                    ddl = col.type.compile(dialect=engine.dialect)
                    conn.execute(text(f'ALTER TABLE {table.name} ADD COLUMN "{col.name}" {ddl}'))


def get_engine():
    if _engine is None:
        init_engine()
    return _engine


def SessionLocal() -> Session:  # noqa: N802
    if _SessionLocal is None:
        init_engine()
    assert _SessionLocal is not None
    return _SessionLocal()


def get_db() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def to_jsonable(v: Any) -> Any:
    if isinstance(v, (datetime, date)):
        return v.isoformat()
    return v


def row_dict(obj: Any, exclude: tuple[str, ...] = ()) -> dict:
    return {c.key: to_jsonable(getattr(obj, c.key)) for c in obj.__table__.columns if c.key not in exclude}


def dumps(v: Any) -> str:
    return json.dumps(v, ensure_ascii=False, default=to_jsonable)
