"""Adapters must degrade gracefully: missing keys → skipped, failures → recorded + retry later."""
import json

import httpx
import pytest

from app import ai
from app.adapters.fx import FxAdapter
from app.adapters.google_trends import GoogleTrendsAdapter
from app.adapters.registry import build_registry
from app.adapters.telegram_channels import TelegramChannelsAdapter, extract_prices, parse_preview
from app.adapters.vidiq_import import import_payload, normalise_item
from app.adapters.youtube import YouTubeAdapter
from app.db import JobRun, LocalPrice, TrendSignal, UsageLog
from app.settings_store import get_setting, set_setting


def test_every_adapter_survives_missing_keys(db):
    for name, adapter in build_registry().items():
        if name in ("fx", "google_trends", "telegram_channels"):
            continue  # keyless; network sources tested below with mocks
        res = adapter.safe_run(db)
        assert res["status"] in ("skipped", "ok"), (name, res)
        st = adapter.status(db)
        assert "configured" in st and "label" in st


def test_youtube_skipped_without_key(db):
    res = YouTubeAdapter().safe_run(db)
    assert res["status"] == "skipped" and "YOUTUBE_API_KEY" in res["message"]
    assert db.get(JobRun, "youtube").status == "skipped"


def test_youtube_with_mocked_api(db, monkeypatch):
    monkeypatch.setenv("YOUTUBE_API_KEY", "k")
    set_setting(db, "trends", {"youtube_queries_tr": ["oyuncak kutu açılımı"], "youtube_queries_ru": []})

    def handler(req):
        if req.url.path.endswith("/search"):
            return httpx.Response(200, json={"items": [
                {"id": {"videoId": "v1"}, "snippet": {"title": "Örgü hamster peluş kutu açılımı, hediye", "channelTitle": "a", "publishedAt": "2026-11-01T10:00:00Z"}},
                {"id": {"videoId": "v2"}, "snippet": {"title": "Lip sync şarkı #akım", "channelTitle": "b", "publishedAt": "2026-11-01T10:00:00Z"}}]})
        return httpx.Response(200, json={"items": [{"id": "v1", "statistics": {"viewCount": "120000"}},
                                                   {"id": "v2", "statistics": {"viewCount": "900000"}}]})
    a = YouTubeAdapter(client=httpx.Client(transport=httpx.MockTransport(handler)))
    res = a.safe_run(db)
    assert res["status"] == "ok" and res["videos"] == 2 and res["product"] == 1 and res["noise"] == 1
    s = db.query(TrendSignal).filter(TrendSignal.external_id == "v1").one()
    assert s.views == 120000 and s.product.name == "crocheted hamster plush"
    assert db.query(UsageLog).filter(UsageLog.provider == "youtube").one().units == 101


def test_youtube_quota_error_is_recorded_with_retry(db, monkeypatch):
    monkeypatch.setenv("YOUTUBE_API_KEY", "k")
    a = YouTubeAdapter(client=httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(403, text="quotaExceeded"))))
    res = a.safe_run(db)
    assert res["status"] == "error" and "quota" in res["message"]
    job = db.get(JobRun, "youtube")
    assert job.status == "error" and job.retry_after is not None
    assert a.safe_run(db)["status"] == "waiting"  # backs off instead of hammering the API


class FakeDF(dict):
    empty = False


class FakeTrendReq:
    def __init__(self, fail=False):
        self.fail, self.kw = fail, None

    def build_payload(self, kws, timeframe, geo):
        if self.fail:
            raise Exception("The request failed: Google returned a response with code 429")
        self.kw = kws[0]

    def interest_over_time(self):
        return FakeDF({self.kw: [10, 12, 15, 20, 26, 30, 35, 41, 50, 55, 60, 62, 70]})


def test_google_trends_with_fake_pytrends(seeded):
    a = GoogleTrendsAdapter(trend_req_factory=FakeTrendReq, sleep=lambda s: None)
    res = a.safe_run(seeded, force=True)
    assert res["status"] == "ok" and res["series"] > 0
    sig = seeded.query(TrendSignal).filter(TrendSignal.source == "google_trends").order_by(TrendSignal.collected_at.desc()).first()
    assert sig.raw["slope"] > 0


def test_google_trends_failure_is_graceful(seeded):
    a = GoogleTrendsAdapter(trend_req_factory=lambda: FakeTrendReq(fail=True), sleep=lambda s: None)
    res = a.safe_run(seeded, force=True)
    assert res["status"] == "error" and "429" in res["message"]


PREVIEW = """
<div class="tgme_widget_message_wrap js-widget_message_wrap"><div class="tgme_widget_message" data-post="tm_shop/101">
<div class="tgme_widget_message_text js-message_text" dir="auto">Örme hamster oýnawaç 🐹<br/>Bahasy: 140 TMT</div>
<span class="tgme_widget_message_views">1.2K</span><time datetime="2026-11-02T09:00:00+00:00" class="time">09:00</time></div></div>
<div class="tgme_widget_message_wrap js-widget_message_wrap"><div class="tgme_widget_message" data-post="tm_shop/102">
<div class="tgme_widget_message_text js-message_text" dir="auto">Мини камера, цена 390 ман</div>
<span class="tgme_widget_message_views">800</span><time datetime="2026-11-03T09:00:00+00:00" class="time">09:00</time></div></div>
"""


def test_telegram_preview_parser_and_prices():
    posts = parse_preview(PREVIEW, "tm_shop")
    assert [p["id"] for p in posts] == ["tm_shop/101", "tm_shop/102"]
    assert "Bahasy: 140 TMT" in posts[0]["text"] and posts[0]["views"] == 1200
    assert extract_prices("Bahasy: 140 TMT") == [140]
    assert extract_prices("цена 390 ман") == [390]
    assert extract_prices("250 manat, 300m") == [250, 300]
    assert extract_prices("no price here 2026") == []


def test_telegram_channels_end_to_end(seeded):
    set_setting(seeded, "telegram", {"channels": ["tm_shop"]})
    a = TelegramChannelsAdapter(client=httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, text=PREVIEW))))
    res = a.safe_run(seeded)
    assert res["status"] == "ok" and res["posts"] == 2 and res["prices"] == 2
    prices = {lp.url: lp for lp in seeded.query(LocalPrice).filter(LocalPrice.channel == "tm_shop")}
    assert prices["https://t.me/tm_shop/101"].price_tmt == 140
    assert seeded.get(type(prices["https://t.me/tm_shop/101"]), prices["https://t.me/tm_shop/101"].id).product_id
    # second run doesn't duplicate
    a.safe_run(seeded)
    assert seeded.query(LocalPrice).filter(LocalPrice.channel == "tm_shop").count() == 2


def test_telegram_channels_without_channels_is_skipped(db):
    assert TelegramChannelsAdapter().safe_run(db)["status"] == "skipped"


def test_fx_adapter(db):
    body = {"rates": {"CNY": 7.12, "TMT": 3.5}, "time_last_update_utc": "x"}
    a = FxAdapter(client=httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, json=body))))
    assert a.safe_run(db)["status"] == "ok"
    assert get_setting(db, "pricing")["usd_cny_rate"] == 7.12
    from app.adapters.fx import fx_view
    v = fx_view(db)
    assert v["official_tmt_per_cny"] == pytest.approx(3.5 / 7.12, abs=1e-3) and "drift_pct_vs_official" in v


def test_fx_network_failure(db):
    def boom(r):
        raise httpx.ConnectError("offline")
    res = FxAdapter(client=httpx.Client(transport=httpx.MockTransport(boom))).safe_run(db)
    assert res["status"] == "error"


def test_vidiq_import_normalises_raw_fields(db):
    item = normalise_item({"videoId": "x1", "link": "https://www.tiktok.com/@a/video/1", "caption": "Squishy haul 🫧",
                           "viewCount": "1300000", "creator_avg_views": 100000, "author": "a"})
    assert item["platform"] == "tiktok" and item["views"] == 1_300_000 and item["outlier_score"] == 13
    res = import_payload(db, {"source": "vidiq", "vidiq_calls": 2, "items": [
        {"id": "1", "url": "https://tiktok.com/1", "title": "Squishy oyuncak haul", "views": 1_300_000, "creator": "a"},
        {"id": "2", "url": "https://tiktok.com/2", "title": "Lip sync #akım", "views": 3_000_000, "creator": "b"}]})
    assert res["product"] == 1 and res["noise"] == 1
    assert db.query(UsageLog).filter(UsageLog.provider == "vidiq").one().units == 10


def test_ai_functions_without_key_fall_back(db):
    assert not ai.available()
    cls = ai.classify_items(db, ["Kilo ile peluş oyuncak, tartıda, link bio'da", "Lip sync #akım"])
    assert [c.label for c in cls] == ["product", "noise"]
    terms = ai.search_terms(db, "crocheted hamster plush")
    assert terms["zh"] and terms["tk"]
    unknown = ai.search_terms(db, "glow sticks")
    assert unknown.get("fallback")
    caps = ai.captions(db, "crocheted hamster plush", 130)
    assert "130 TMT" in caps["tk"]["caption"] and "130 TMT" in caps["ru"]["caption"]
    res = ai.extract_offer(db, [], "")
    assert res["ai"] is False


def test_ai_with_mocked_client(db, monkeypatch, tmp_path):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")

    class Usage:
        input_tokens, output_tokens = 1000, 200

    class Block:
        type = "text"

        def __init__(self, text):
            self.text = text

    class Resp:
        def __init__(self, text):
            self.content, self.usage, self.stop_reason = [Block(text)], Usage(), "end_turn"

    calls = []

    class Messages:
        def create(self, **kw):
            calls.append(kw)
            if kw["model"] == ai.model_vision():
                return Resp(json.dumps({"title_en": "Crochet hamster", "price_cny": 14.5, "rating": 4.9,
                                        "praises": ["soft"], "complaints": ["smaller than listed"], "store_name": "Shop"}))
            return Resp('```json\n[{"i":0,"label":"product","product":"glow in the dark stars","category":"home","counterfeit":false,"reason":"store find"}]\n```')

    class Client:
        messages = Messages()

    monkeypatch.setattr(ai, "_client", lambda: Client())
    out = ai.classify_items(db, ["Bunu almalısın! Gece parlayan yıldızlar, link bio'da, ucuz ürün"])
    assert out[0].label == "product" and out[0].product_name == "glow in the dark stars"
    ai.classify_items(db, ["Bunu almalısın! Gece parlayan yıldızlar, link bio'da, ucuz ürün"])
    assert len(calls) == 1  # cached
    img = tmp_path / "s.png"
    img.write_bytes(b"\x89PNG fake")
    res = ai.extract_offer(db, [img], "https://item.taobao.com/x")
    assert res["ai"] and res["fields"]["price_cny"] == 14.5
    assert calls[-1]["messages"][0]["content"][0]["type"] == "image"
    assert ai.month_spend(db) > 0


def test_ai_budget_cap_stops_calls(db, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    set_setting(db, "ai", {"monthly_budget_usd": 0.001})
    db.add(UsageLog(provider="claude", units=0.01))
    db.commit()
    monkeypatch.setattr(ai, "_client", lambda: (_ for _ in ()).throw(AssertionError("must not call")))
    assert ai.captions(db, "squishy toys", 20)["tk"]["template"]
