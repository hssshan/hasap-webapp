"""vidIQ (via the Claude MCP connector) — import-only adapter.

The app cannot call MCP tools itself. A weekly Claude scheduled task (see
`scheduled_task_prompt.md`) runs the vidIQ searches and POSTs the results to
`POST /api/import/trends` (header `X-Import-Secret: $TREND_IMPORT_SECRET`), or
outputs JSON that you paste into the Import screen.

Free plan: 150 credits/month, 5 per call → ~30 calls. The scheduled task makes
4 calls a week and we log credits so the Setup page can show what's left.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import services
from ..db import UsageLog
from .base import Adapter, mark_job

CREDITS_PER_CALL = 5
MONTHLY_CREDITS = 150


def normalise_item(it: dict) -> dict:
    """Accepts the shape our scheduled task produces and common raw vidIQ field names."""
    views = it.get("views") or it.get("viewCount") or it.get("view_count") or it.get("plays") or 0
    outlier = it.get("outlier_score") or it.get("outlierScore") or it.get("outlier") or it.get("multiplier")
    if not outlier and it.get("creator_avg_views"):
        try:
            outlier = float(views) / max(float(it["creator_avg_views"]), 1)
        except (TypeError, ValueError):
            outlier = None
    url = it.get("url") or it.get("link") or ""
    return {
        "external_id": it.get("id") or it.get("video_id") or it.get("videoId") or url,
        "url": url or None,
        "platform": (it.get("platform") or "").lower() or ("tiktok" if "tiktok" in url else
                                                         "instagram" if "instagram" in url else "youtube"),
        "title": it.get("title") or it.get("caption") or "",
        "description": it.get("description") or it.get("summary") or "",
        "hashtags": it.get("hashtags") or [],
        "language": it.get("language") or it.get("lang") or "",
        "creator": it.get("creator") or it.get("author") or it.get("username") or it.get("channel"),
        "views": int(float(views or 0)),
        "outlier_score": float(outlier) if outlier else None,
        "published_at": it.get("published_at") or it.get("publishedAt") or it.get("date"),
        "product_name": it.get("product_name"),
        "classification": it.get("classification"),
        "category": it.get("category"),
        "counterfeit": it.get("counterfeit"),
        "thumbnail_url": it.get("thumbnail_url") or it.get("thumbnailUrl") or it.get("thumbnail") or it.get("cover")
                         or it.get("image"),
    }


def import_payload(db: Session, payload: dict | list, *, is_mock: bool = False) -> dict[str, Any]:
    items = payload if isinstance(payload, list) else payload.get("items") or payload.get("results") or []
    source = "vidiq" if isinstance(payload, list) else (payload.get("source") or "vidiq")
    if source not in ("vidiq", "paste", "youtube"):
        source = "vidiq"
    calls = 0 if isinstance(payload, list) else int(payload.get("vidiq_calls") or 0)
    stats = services.ingest_signals(db, source, [normalise_item(i) for i in items if isinstance(i, dict)], is_mock=is_mock)
    if calls:
        db.add(UsageLog(provider="vidiq", units=calls * CREDITS_PER_CALL, detail=f"{calls} calls via scheduled task"))
        db.commit()
    alerts = services.recompute_trends(db)
    mark_job(db, "vidiq_import", "ok", f"imported {stats['received']} items: {stats['product']} product, {stats['noise']} noise")
    return {**stats, "alerts": alerts}


def credits_used_this_month(db: Session) -> float:
    start = datetime.now(timezone.utc).replace(tzinfo=None).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return float(db.scalar(select(func.sum(UsageLog.units)).where(UsageLog.provider == "vidiq", UsageLog.created_at >= start)) or 0)


class VidiqImportAdapter(Adapter):
    name = "vidiq_import"
    label = "vidIQ (TikTok + Instagram outliers, via Claude scheduled task)"
    needs = ["TREND_IMPORT_SECRET"]
    automatic = False
    free_note = "Free plan: 150 credits/month (5 per call ≈ 30 calls). The weekly task uses 4 calls."
    unlocks = "Main trend signal: Turkish TikTok/Reels that massively outperform the creator's usual views."

    def status(self, db: Session) -> dict[str, Any]:
        st = super().status(db)
        used = credits_used_this_month(db)
        st["credits_used"] = used
        st["credits_left"] = max(0, MONTHLY_CREDITS - used)
        st["manual_import_available"] = True
        return st

    def run(self, db: Session, **kwargs: Any) -> dict[str, Any]:
        return {"status": "ok", "message": "Import-only: runs from the Claude scheduled task or the Import screen."}
