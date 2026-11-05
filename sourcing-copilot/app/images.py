"""Real product photos, from free sources, cached locally so they never expire.

Where a product's photo comes from, in order:
1. A photo you set yourself (upload, image link, or "use this" on a video).
2. The best offer's image link (when you pasted one).
3. Automatic: the thumbnail of the most-viewed trending video about the product
   (YouTube thumbnail, TikTok via its free oEmbed endpoint, vidIQ's thumbnail
   field, Telegram post photo).
4. Automatic fallback: a Creative Commons photo from Openverse (free, no key).
5. A plain monogram tile (no emoji).

Downloads are trimmed of black letterbox bars, resized to max 800 px and saved
under DATA_DIR/images. Everything fails quietly: no image is never an error.
"""
from __future__ import annotations

import hashlib
import io
import logging
import re
import threading
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import data_dir
from .db import Product, TrendSignal

log = logging.getLogger(__name__)
UA = {"User-Agent": "Mozilla/5.0 (SourcingCopilot; personal use)"}
MAX_BYTES = 6 * 1024 * 1024
_lock = threading.Lock()


def images_dir():
    d = data_dir() / "images"
    d.mkdir(exist_ok=True)
    return d


def youtube_id(url: str | None) -> str | None:
    if not url:
        return None
    m = re.search(r"(?:youtube\.com/(?:shorts/|watch\?v=|embed/)|youtu\.be/)([\w-]{11})", url)
    return m.group(1) if m else None


def _trim_and_resize(data: bytes) -> tuple[bytes, str]:
    """Remove black letterbox bars (common on Shorts thumbnails) and shrink. Falls back to raw bytes."""
    try:
        from PIL import Image, ImageChops
    except ImportError:
        return data, "jpg"
    try:
        im = Image.open(io.BytesIO(data)).convert("RGB")
        bg = Image.new("RGB", im.size, (0, 0, 0))
        diff = ImageChops.difference(im, bg).convert("L").point(lambda p: 255 if p > 28 else 0)
        box = diff.getbbox()
        if box and (box[2] - box[0]) > im.width * 0.3 and (box[3] - box[1]) > im.height * 0.3:
            im = im.crop(box)
        im.thumbnail((800, 800))
        out = io.BytesIO()
        im.save(out, "JPEG", quality=84, optimize=True)
        return out.getvalue(), "jpg"
    except Exception:  # not an image Pillow understands
        return data, "jpg"


def _candidates(url: str) -> list[str]:
    """For YouTube thumbnails try the sharper versions first (Shorts often lack maxres)."""
    m = re.match(r"https://i\.ytimg\.com/vi/([\w-]{11})/\w+\.jpg", url)
    if m:
        vid = m.group(1)
        return [f"https://i.ytimg.com/vi/{vid}/{n}.jpg" for n in ("maxresdefault", "sddefault", "hqdefault")]
    return [url]


def cache_image(url: str, client: httpx.Client | None = None) -> str | None:
    """Download an image once and return its local URL (/media/<hash>.jpg), or None."""
    if not url:
        return None
    if not (url.startswith("/media/") or url.startswith("/uploads/")):
        for cand in _candidates(url)[:-1]:
            got = _cache_one(cand, url, client)
            if got:
                return got
        return _cache_one(_candidates(url)[-1], url, client)
    return url


def _cache_one(url: str, key: str, client: httpx.Client | None = None) -> str | None:
    if not url:
        return None
    if url.startswith("/media/") or url.startswith("/uploads/"):
        return url
    name = hashlib.sha1(key.encode()).hexdigest()[:20] + ".jpg"
    path = images_dir() / name
    if path.exists():
        return f"/media/{name}"
    try:
        c = client or httpx.Client(timeout=20, follow_redirects=True, headers=UA)
        r = c.get(url)
        if r.status_code != 200 or not r.headers.get("content-type", "").startswith("image") or len(r.content) > MAX_BYTES:
            return None
        data, _ = _trim_and_resize(r.content)
        if len(data) < 1500:  # YouTube's grey "no thumbnail" placeholder and similar
            return None
        path.write_bytes(data)
        return f"/media/{name}"
    except Exception as e:
        log.info("image download failed for %s: %s", url[:80], e)
        return None


def save_upload(data: bytes) -> str | None:
    data, _ = _trim_and_resize(data)
    name = hashlib.sha1(data).hexdigest()[:20] + ".jpg"
    (images_dir() / name).write_bytes(data)
    return f"/media/{name}"


def signal_image_url(sig: TrendSignal, client: httpx.Client | None = None) -> str | None:
    """Remote thumbnail URL for a trend signal, if one can be found for free."""
    raw = sig.raw or {}
    for k in ("thumbnail_url", "thumbnail", "thumbnailUrl", "cover", "image", "photo"):
        if raw.get(k) and str(raw[k]).startswith("http"):
            return str(raw[k])
    yid = youtube_id(sig.url) or (sig.external_id if sig.source == "youtube" and len(sig.external_id or "") == 11 else None)
    if yid:
        return f"https://i.ytimg.com/vi/{yid}/hqdefault.jpg"
    if sig.url and "tiktok.com" in sig.url:
        try:
            c = client or httpx.Client(timeout=15, follow_redirects=True, headers=UA)
            r = c.get("https://www.tiktok.com/oembed", params={"url": sig.url})
            if r.status_code == 200:
                return r.json().get("thumbnail_url")
        except Exception:
            return None
    return None


def openverse_image(query: str, client: httpx.Client | None = None) -> tuple[str, str] | None:
    """(image url, credit) for a Creative Commons photo, or None. Free, no key; may be unavailable."""
    try:
        c = client or httpx.Client(timeout=20, headers=UA)
        r = c.get("https://api.openverse.org/v1/images/", params={"q": query, "page_size": 5, "mature": "false"})
        if r.status_code != 200 or not r.headers.get("content-type", "").startswith("application/json"):
            return None
        for x in r.json().get("results", []):
            url = x.get("thumbnail") or x.get("url")
            if url:
                credit = f"{x.get('title') or 'Photo'} by {x.get('creator') or 'unknown'} ({(x.get('license') or '').upper()} {x.get('license_version') or ''}) via Openverse"
                return url, credit.strip()
    except Exception:
        return None
    return None


def best_signal_image(db: Session, product: Product, client: httpx.Client | None = None) -> tuple[str, str] | None:
    sigs = db.scalars(select(TrendSignal).where(TrendSignal.product_id == product.id,
                                                TrendSignal.source.in_(["vidiq", "youtube", "paste", "telegram"]),
                                                TrendSignal.classification == "product")
                      .order_by(TrendSignal.views.desc())).all()
    for s in sigs[:6]:
        remote = signal_image_url(s, client)
        local = cache_image(remote, client) if remote else None
        if local:
            if not s.thumbnail:
                s.thumbnail = local
            return local, f"From {s.platform or s.source}: {(s.title or '')[:80]} ({s.url or ''})"
    return None


def refresh_product_images(db: Session, limit: int = 20, client: httpx.Client | None = None) -> dict[str, Any]:
    """Give automatic photos to products that don't have one (or only have an automatic one that vanished)."""
    if not _lock.acquire(blocking=False):
        return {"skipped": "already running"}
    try:
        done = 0
        for p in db.scalars(select(Product).where(Product.dismissed.is_(False))).all()[:200]:
            if done >= limit:
                break
            if p.image_url and p.image_url.startswith("http"):  # remote photo: download it once, keep it locally
                local = cache_image(p.image_url, client)
                if local:
                    p.image_url = local
                    done += 1
                    continue
            if p.image_url and (p.image_source == "manual" or _local_exists(p.image_url)):
                continue
            found = best_signal_image(db, p, client)
            if not found:
                ov = openverse_image(p.name, client)
                if ov:
                    local = cache_image(ov[0], client)
                    found = (local, ov[1]) if local else None
            if found:
                p.image_url, p.image_credit, p.image_source = found[0], found[1], "auto"
                done += 1
        db.commit()
        return {"updated": done}
    finally:
        _lock.release()


def cache_signal_thumbnails(db: Session, limit: int = 40, client: httpx.Client | None = None) -> int:
    """Store local thumbnails for recent product signals (shown in the product's video list)."""
    n = 0
    sigs = db.scalars(select(TrendSignal).where(TrendSignal.thumbnail.is_(None), TrendSignal.classification == "product",
                                                TrendSignal.source.in_(["vidiq", "youtube", "paste", "telegram"]))
                      .order_by(TrendSignal.views.desc()).limit(limit)).all()
    for s in sigs:
        remote = signal_image_url(s, client)
        local = cache_image(remote, client) if remote else None
        s.thumbnail = local or ""  # "" = tried, nothing found
        n += bool(local)
    db.commit()
    return n


def _local_exists(url: str) -> bool:
    if url.startswith("/media/"):
        return (images_dir() / url.rsplit("/", 1)[-1]).exists()
    return not url.startswith("http")


def refresh_in_background() -> None:
    """Fire-and-forget refresh (used at startup and after imports, so pages never wait for downloads)."""
    from .db import SessionLocal

    def run():
        db = SessionLocal()
        try:
            refresh_product_images(db)
            cache_signal_thumbnails(db)
        except Exception as e:
            log.info("image refresh failed: %s", e)
        finally:
            db.close()

    threading.Thread(target=run, name="image-refresh", daemon=True).start()
