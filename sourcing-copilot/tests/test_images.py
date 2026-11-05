"""Product photos (free sources, cached locally), monogram placeholder, auto-migration, no emojis."""
import io
import re
from pathlib import Path

import httpx
from PIL import Image

from app import images
from app.db import Product, TrendSignal

EMOJI = re.compile("[\U0001F000-\U0001FFFF☀-➿]")


def jpeg(w=480, h=360, bars=True) -> bytes:
    im = Image.new("RGB", (w, h), (0, 0, 0))
    inner = Image.new("RGB", (w // 2, h), (230, 120, 160))  # pink content between black bars
    im.paste(inner, (w // 4, 0))
    if not bars:
        im = inner
    for x in range(0, inner.width, 7):  # some texture so the JPEG isn't tiny
        for y in range(0, h, 7):
            im.putpixel((x + (w // 4 if bars else 0), y), (x % 255, y % 255, 90))
    out = io.BytesIO()
    im.save(out, "JPEG", quality=90)
    return out.getvalue()


def test_black_letterbox_bars_are_trimmed():
    data, _ = images._trim_and_resize(jpeg())
    im = Image.open(io.BytesIO(data))
    assert im.width < 300 and im.height == 360


def test_youtube_prefers_sharp_thumbnail_and_falls_back(db):
    seen = []

    def handler(req):
        seen.append(req.url.path)
        if "maxresdefault" in req.url.path:
            return httpx.Response(404)
        return httpx.Response(200, content=jpeg(), headers={"content-type": "image/jpeg"})

    c = httpx.Client(transport=httpx.MockTransport(handler))
    local = images.cache_image("https://i.ytimg.com/vi/y-p5UAQnRkI/hqdefault.jpg", c)
    assert local.startswith("/media/") and (images.images_dir() / local.split("/")[-1]).exists()
    assert seen[:2] == ["/vi/y-p5UAQnRkI/maxresdefault.jpg", "/vi/y-p5UAQnRkI/sddefault.jpg"]
    assert images.cache_image("https://i.ytimg.com/vi/y-p5UAQnRkI/hqdefault.jpg", c) == local  # cached, no refetch


def test_non_image_and_failures_return_none(db):
    c = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, text="<html>", headers={"content-type": "text/html"})))
    assert images.cache_image("https://example.com/x.jpg", c) is None

    def boom(r):
        raise httpx.ConnectError("offline")
    assert images.cache_image("https://example.com/y.jpg", httpx.Client(transport=httpx.MockTransport(boom))) is None


def test_product_gets_photo_from_its_most_viewed_video(db):
    p = Product(name="squishy toys", category="toys")
    db.add(p)
    db.flush()
    db.add_all([
        TrendSignal(source="vidiq", platform="instagram", external_id="ig1", url="https://instagram.com/reel/x", title="a",
                    views=9_000_000, classification="product", product_id=p.id),
        TrendSignal(source="youtube", platform="youtube", external_id="iRuF4FoEtOY", url="https://www.youtube.com/watch?v=iRuF4FoEtOY",
                    title="squishy haul", views=2_000_000, classification="product", product_id=p.id),
    ])
    db.commit()

    def handler(req):
        if req.url.host == "i.ytimg.com":
            return httpx.Response(200, content=jpeg(bars=False), headers={"content-type": "image/jpeg"})
        return httpx.Response(404)

    res = images.refresh_product_images(db, client=httpx.Client(transport=httpx.MockTransport(handler)))
    db.refresh(p)
    assert res["updated"] == 1 and p.image_url.startswith("/media/") and p.image_source == "auto"
    assert "YouTube" in p.image_credit or "youtube" in p.image_credit


def test_tiktok_thumbnail_via_free_oembed():
    sig = TrendSignal(source="vidiq", platform="tiktok", external_id="1", url="https://www.tiktok.com/@a/video/1", raw={})

    def handler(req):
        assert req.url.path == "/oembed"
        return httpx.Response(200, json={"thumbnail_url": "https://p16.tiktokcdn.com/thumb.jpg"})
    assert images.signal_image_url(sig, httpx.Client(transport=httpx.MockTransport(handler))) == "https://p16.tiktokcdn.com/thumb.jpg"


def test_set_photo_by_upload_and_reset(client):
    pid = client.post("/api/products", json={"name": "capybara plush"}).json()["id"]
    r = client.post(f"/api/products/{pid}/image", files={"file": ("p.jpg", jpeg(bars=False), "image/jpeg")})
    assert r.status_code == 200 and r.json()["image_url"].startswith("/media/")
    assert client.get(r.json()["image_url"]).status_code == 200
    card = next(c for c in client.get("/api/products").json() if c["product_id"] == pid)
    assert card["image_url"] == r.json()["image_url"]
    assert client.post(f"/api/products/{pid}/image", data={"url": ""}).status_code == 400
    assert client.post(f"/api/products/{pid}/image", data={"reset": "true"}).json()["image_url"] is None


def test_placeholder_is_a_monogram_not_an_emoji(client):
    pid = client.post("/api/products", json={"name": "mini digital camera"}).json()["id"]
    svg = client.get(f"/api/placeholder/{pid}.svg").text
    assert ">MD<" in svg and not EMOJI.search(svg)


def test_auto_migration_adds_new_columns(tmp_path):
    import sqlite3
    path = tmp_path / "old.db"
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE products (id INTEGER PRIMARY KEY, name VARCHAR(200))")
    con.execute("INSERT INTO products (name) VALUES ('old product')")
    con.commit()
    con.close()
    from app import db as dbmod
    dbmod.init_engine(f"sqlite:///{path}")
    cols = {r[1] for r in sqlite3.connect(path).execute("PRAGMA table_info(products)")}
    assert {"image_source", "image_credit", "trend_score"} <= cols


def test_no_emojis_in_ui_or_bot_text():
    root = Path(__file__).resolve().parent.parent / "app"
    for f in ["static/app.js", "static/index.html", "bot/handlers.py", "services.py", "ai.py"]:
        text = (root / f).read_text(encoding="utf8")
        assert not EMOJI.search(text), f"emoji found in {f}: {EMOJI.findall(text)[:5]}"
