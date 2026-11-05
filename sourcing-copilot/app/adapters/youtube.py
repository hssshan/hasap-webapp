"""YouTube Data API v3 — Turkish and Russian Shorts about toys, hauls, unboxings.

Free quota: 10,000 units/day. search.list = 100 units, videos.list = 1 unit.
We run once a day with ~10 queries ≈ 1,010 units, and stop at a daily cap
(YOUTUBE_DAILY_UNIT_CAP, default 3000) to stay far below the free limit.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import services
from ..config import env, env_int
from ..db import UsageLog
from ..settings_store import get_setting
from .base import Adapter

API = "https://www.googleapis.com/youtube/v3"


def units_today(db: Session) -> float:
    start = datetime.now(timezone.utc).replace(tzinfo=None).replace(hour=0, minute=0, second=0, microsecond=0)
    return float(db.scalar(select(func.sum(UsageLog.units)).where(UsageLog.provider == "youtube", UsageLog.created_at >= start)) or 0)


class YouTubeAdapter(Adapter):
    name = "youtube"
    label = "YouTube Shorts (TR + RU)"
    needs = ["YOUTUBE_API_KEY"]
    free_note = "Free Google API key; 10,000 quota units/day. We use ~1,000/day."
    unlocks = "Daily scan of Turkish & Russian Shorts: unboxings, hauls, 'Çin'den aldım', gift ideas."
    retry_minutes = 720

    def __init__(self, client: httpx.Client | None = None):
        self.client = client

    def run(self, db: Session, **kwargs: Any) -> dict[str, Any]:
        key = env("YOUTUBE_API_KEY")
        cap = env_int("YOUTUBE_DAILY_UNIT_CAP", 3000)
        tset = get_setting(db, "trends")
        queries = [(q, "tr", "TR") for q in tset["youtube_queries_tr"]] + [(q, "ru", "RU") for q in tset["youtube_queries_ru"]]
        client = self.client or httpx.Client(timeout=20)
        after = (datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=14)).strftime("%Y-%m-%dT%H:%M:%SZ")
        found: dict[str, dict] = {}
        used = 0
        for q, lang, region in queries:
            if units_today(db) + used + 100 > cap:
                break
            r = client.get(f"{API}/search", params={
                "key": key, "part": "snippet", "q": q, "type": "video", "videoDuration": "short", "maxResults": 25,
                "order": "viewCount", "publishedAfter": after, "regionCode": region, "relevanceLanguage": lang})
            used += 100
            if r.status_code == 403:
                raise RuntimeError(f"YouTube quota/permission error: {r.text[:200]}")
            r.raise_for_status()
            for item in r.json().get("items", []):
                vid = item.get("id", {}).get("videoId")
                if vid and vid not in found:
                    sn = item.get("snippet", {})
                    found[vid] = {"external_id": vid, "url": f"https://www.youtube.com/shorts/{vid}", "platform": "youtube",
                                  "title": sn.get("title", ""), "description": sn.get("description", ""), "language": lang,
                                  "creator": sn.get("channelTitle"), "published_at": sn.get("publishedAt"),
                                  "raw": {"query": q, "channel_id": sn.get("channelId"),
                                          "thumbnail_url": ((sn.get("thumbnails") or {}).get("high") or {}).get("url")}}
        ids = list(found)
        for i in range(0, len(ids), 50):
            r = client.get(f"{API}/videos", params={"key": key, "part": "statistics", "id": ",".join(ids[i:i + 50])})
            used += 1
            r.raise_for_status()
            for v in r.json().get("items", []):
                found[v["id"]]["views"] = int(v.get("statistics", {}).get("viewCount", 0))
        db.add(UsageLog(provider="youtube", units=used, detail=f"{len(queries)} queries"))
        db.commit()
        stats = services.ingest_signals(db, "youtube", list(found.values()))
        return {"videos": len(found), "product": stats["product"], "noise": stats["noise"], "units": used}
