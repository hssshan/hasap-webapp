"""Google Trends via pytrends (free, unofficial, brittle).

Honest limits: Google rate-limits pytrends (HTTP 429) often, and Turkmenistan
has very little search volume so TM is usually empty. We query a few products
per run, back off for 12 h on errors, and treat it as a minor score component.
"""
from __future__ import annotations

import time
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import Product, TrendSignal, utcnow
from ..settings_store import get_setting
from .base import Adapter


def _slope(values: list[float]) -> float:
    n = len(values)
    if n < 2:
        return 0.0
    mx = (n - 1) / 2
    my = sum(values) / n
    num = sum((i - mx) * (v - my) for i, v in enumerate(values))
    den = sum((i - mx) ** 2 for i in range(n)) or 1
    return num / den


class GoogleTrendsAdapter(Adapter):
    name = "google_trends"
    label = "Google Trends (TR / RU / TM)"
    needs = []
    free_note = "Free, no key. Unofficial and often rate-limited: retries automatically later."
    unlocks = "Search-interest curve per product idea in Turkey, Russia and Turkmenistan."
    retry_minutes = 720

    def __init__(self, trend_req_factory=None, sleep=time.sleep):
        self.factory = trend_req_factory
        self.sleep = sleep

    def configured(self) -> bool:
        if self.factory:
            return True
        try:
            import pytrends  # noqa: F401
            return True
        except ImportError:
            return False

    def status(self, db: Session) -> dict[str, Any]:
        st = super().status(db)
        if not self.configured():
            st["message"] = "pytrends not installed (pip install pytrends)"
        return st

    def run(self, db: Session, max_products: int = 5, **kwargs: Any) -> dict[str, Any]:
        if self.factory:
            req = self.factory()
        else:
            from pytrends.request import TrendReq
            # retries=0: pytrends' own retry breaks on urllib3 2.x; our scheduler retries after 12 h instead
            req = TrendReq(hl="tr-TR", tz=180, timeout=(10, 25), retries=0, backoff_factor=0)
        geos = get_setting(db, "trends")["google_trends_geos"]
        products = db.scalars(select(Product).where(Product.dismissed.is_(False))
                              .order_by(Product.trend_score.desc())).all()
        # rotate: products with the oldest Google Trends data first
        def last_gt(p):
            s = db.scalar(select(TrendSignal.collected_at).where(TrendSignal.product_id == p.id,
                                                                  TrendSignal.source == "google_trends")
                          .order_by(TrendSignal.collected_at.desc()))
            return s or utcnow().replace(year=2000)
        products = sorted(products, key=last_gt)[:max_products]
        written = 0
        for p in products:
            terms = p.search_terms or {}
            for geo in geos:
                kw = terms.get("ru") if geo == "RU" else terms.get("tr") if geo == "TR" else (terms.get("ru") or p.name)
                kw = (kw or p.name)[:90]
                req.build_payload([kw], timeframe="today 3-m", geo=geo)
                df = req.interest_over_time()
                self.sleep(2)
                if df is None or getattr(df, "empty", True) or kw not in df:
                    continue
                values = [float(v) for v in list(df[kw])]
                recent = values[-4:]
                latest = sum(recent) / len(recent) if recent else 0
                ext = f"{p.id}:{geo}"
                sig = db.scalar(select(TrendSignal).where(TrendSignal.source == "google_trends", TrendSignal.external_id == ext))
                if not sig:
                    sig = TrendSignal(source="google_trends", external_id=ext, platform="google", product_id=p.id)
                    db.add(sig)
                sig.title = f"Google Trends {geo}: {kw}"
                sig.url = f"https://trends.google.com/trends/explore?geo={geo}&q={kw}"
                sig.value = latest
                sig.classification = "product"
                sig.collected_at = utcnow()
                sig.raw = {"geo": geo, "keyword": kw, "slope": round(_slope(values[-8:]), 3), "series": values[-13:]}
                written += 1
        db.commit()
        return {"products": len(products), "series": written}
