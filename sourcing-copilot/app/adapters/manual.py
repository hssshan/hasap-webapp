"""Manual / internal sources: paste box (TikTok Creative Center, Pinterest Trends),
screenshot import (AI vision), and my own sales (computed internally)."""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from .. import ai, services
from .base import Adapter, mark_job


class PasteBoxAdapter(Adapter):
    name = "paste_box"
    label = "Paste box (TikTok Creative Center, Pinterest Trends)"
    automatic = False
    free_note = "Free websites with no API: copy hashtags/products/links and paste them."
    unlocks = "Quick manual trend import; AI cleans it when the Claude key is set."

    def run(self, db: Session, text: str = "", **kwargs: Any) -> dict[str, Any]:
        return import_paste(db, text)


def import_paste(db: Session, text: str, *, language: str = "tr") -> dict[str, Any]:
    items = []
    for it in ai.clean_paste(db, text):
        ext = it["url"] or "paste:" + hashlib.sha1(it["text"].lower().encode()).hexdigest()[:16]
        items.append({"external_id": ext, "url": it["url"], "platform": it["platform"],
                      "title": it["text"], "hashtags": it["hashtags"], "views": it["views"],
                      "language": language, "creator": it["platform"] + ":" + (it["url"] or it["text"])[:60]})
    stats = services.ingest_signals(db, "paste", items)
    alerts = services.recompute_trends(db)
    mark_job(db, "paste_box", "ok", f"{len(items)} lines: {stats['product']} product, {stats['noise']} noise")
    return {**stats, "alerts": alerts}


class ScreenshotAdapter(Adapter):
    name = "screenshot_import"
    label = "Offer screenshots (AI vision)"
    kind = "offer"
    needs = ["ANTHROPIC_API_KEY"]
    automatic = False
    free_note = "Uses your Claude API key (~$0.01 per offer). Without it the form is filled manually."
    unlocks = "Paste a Taobao/1688 link + screenshots → title, price tiers, sales, ratings, store info, review praises/complaints."

    def run(self, db: Session, paths: list[Path] | None = None, url: str = "", **kwargs: Any) -> dict[str, Any]:
        return ai.extract_offer(db, paths or [], url)


class OwnSalesAdapter(Adapter):
    name = "own_sales"
    label = "My own sales"
    automatic = False
    free_note = "Built in."
    unlocks = "Strongest trend signal (25 of 100 points), real margins, reorder advice."

    def run(self, db: Session, **kwargs: Any) -> dict[str, Any]:
        return {"status": "ok"}
