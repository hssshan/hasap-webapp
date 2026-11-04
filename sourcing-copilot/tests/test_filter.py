"""Product/noise filter with the real vidIQ test run (Turkish content, Oct 2026)."""
import pytest

from app import product_filter as pf

PRODUCTS = [
    ("Kilo ile peluş oyuncak aldım! Tartıda tartıp satıyorlar 😱 nereden aldın diyenler link bio'da", "plush toys sold by the kilo"),
    ("Gen Z'nin yeni favorisi: örgü hamster peluş 🐹 hediye için bunu almalısın", "crocheted hamster plush"),
    ("Ucuz mini dijital kamera Çin'den sipariş ettim, kutu açılımı 📷", "mini digital camera"),
    ("Pazarda squishy oyuncak buldum 🫧 tanesi 20 TL", "squishy toys"),
    ("Sevgiliye hediye: bebek anahtarlık 🔑 çantama taktım, link bio'da", "doll style keychains"),
    ("Заказала мини камеру из Китая, распаковка 📷", "mini digital camera"),
    ("Мягкие игрушки на вес! Купил килограмм игрушек", "plush toys sold by the kilo"),
]
NOISE = [
    "Lip sync to Tarkan şarkı 🎤 #akım #lipsync",
    "İskender girl 😂 yemek şakası #komik",
    "Dans ederken yeni çantamı gösteriyorum 💃 #dans #trend",
    "Sevgilimle komik skeç 😂 #caps",
]


@pytest.mark.parametrize("text,name", PRODUCTS)
def test_product_content_is_kept_and_named(text, name):
    c = pf.classify(text)
    assert c.label == "product", c.reason
    assert c.product_name == name


@pytest.mark.parametrize("text", NOISE)
def test_noise_is_thrown_away(text):
    c = pf.classify(text)
    assert c.label == "noise", c.reason
    assert c.product_name is None


def test_handbag_while_dancing_is_noise_even_with_a_product_word():
    assert pf.classify("Dancing while showing my new handbag #outfit #dance").label == "noise"


@pytest.mark.parametrize("text,brand", [("Labubu peluş kutu açılımı", "labubu"), ("Disney Stitch plush haul", "disney"),
                                        ("Sanrio Kuromi anahtarlık", "sanrio"), ("Pokémon peluş aldım", "pokémon")])
def test_counterfeit_flag(text, brand):
    c = pf.classify(text)
    assert c.counterfeit
    assert "customs" in c.counterfeit_reason
    assert pf.detect_brand(text) == brand


def test_no_false_counterfeit():
    assert not pf.classify("örgü hamster peluş hediye").counterfeit


def test_grouping_same_product_across_platforms():
    assert pf.same_product("crocheted hamster plush", "Crocheted Hamster Plush")
    assert pf.same_product("mini digital camera", "cheap mini digital camera")
    assert not pf.same_product("mini digital camera", "squishy toys")
    assert not pf.same_product("plush toys sold by the kilo", "crocheted hamster plush")


def test_two_independent_signals_required(db):
    """One viral video is not a trend: score capped until a second independent signal appears."""
    from app import services
    from app.db import Product
    services.ingest_signals(db, "vidiq", [{"external_id": "a", "platform": "tiktok", "creator": "one", "views": 2_200_000,
                                           "language": "tr", "outlier_score": 30,
                                           "title": "Kilo ile peluş oyuncak aldım tartıda, link bio'da"}])
    services.recompute_trends(db)
    p = db.query(Product).one()
    assert p.trend_breakdown["independent_signals"] == 1
    assert p.trend_breakdown["is_trend"] is False
    assert p.trend_score <= 35
    services.ingest_signals(db, "vidiq", [{"external_id": "b", "platform": "instagram", "creator": "two", "views": 480_000,
                                           "language": "tr", "title": "Kiloyla peluş oyuncak satan mağaza, tartıya koy"}])
    services.recompute_trends(db)
    db.refresh(p)
    assert db.query(Product).count() == 1  # grouped, not a second product
    assert p.trend_breakdown["independent_signals"] == 2
    assert p.trend_breakdown["is_trend"] is True


def test_seed_import_filters_noise(seeded):
    from app.db import TrendSignal
    noise = seeded.query(TrendSignal).filter(TrendSignal.classification == "noise").all()
    assert len(noise) == 3
    assert all(s.product_id is None for s in noise)
