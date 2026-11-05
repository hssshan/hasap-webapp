from datetime import timedelta
from types import SimpleNamespace as NS

from app import delivery, scoring
from tests.conftest import NOW


def seller(**kw):
    base = dict(store_age_years=None, total_sales=None, rating=None, response_rate=None, dispute_rate=None, is_factory=None)
    base.update(kw)
    return NS(**base)


def test_seller_score_good_factory_vs_new_shop():
    good = scoring.seller_score(seller(store_age_years=8, total_sales=260_000, rating=4.8, response_rate=96, dispute_rate=0.6, is_factory=True))
    new = scoring.seller_score(seller(store_age_years=0.5, total_sales=900, rating=4.5, response_rate=80))
    assert good["score"] >= 85 and good["colour"] == "green"
    assert new["score"] < good["score"] - 25
    reasons = " ".join(c["reason"] for c in new["components"])
    assert "new store" in reasons and "below 4.5" not in reasons


def test_seller_score_always_has_reasons_and_unknowns_are_neutral():
    s = scoring.seller_score(seller())
    assert 30 <= s["score"] <= 55
    assert all(c["reason"] for c in s["components"])


def test_my_experience_lowers_seller_score():
    base = seller(store_age_years=5, total_sales=50_000, rating=4.7, response_rate=92, dispute_rate=1, is_factory=False)
    before = scoring.seller_score(base)["score"]
    after = scoring.seller_score(base, [NS(kind="damage"), NS(kind="wrong_item")])
    assert after["score"] == before - 22
    assert any(c["key"] == "experience" for c in after["components"])


def offer(id, **kw):
    base = dict(rating=None, review_count=None, buyer_photos=None, repeat_purchase_rate=None, sales_count=None, complaints=[], seller_id=None)
    base.update(kw)
    return NS(id=id, **base)


def test_value_score_ranks_and_explains_winner():
    a = offer(1, rating=4.9, review_count=3400, buyer_photos=620, repeat_purchase_rate=18, sales_count=12000, seller_id=10)
    b = offer(2, rating=4.5, review_count=60, sales_count=300, complaints=["slow dispatch"], seller_id=11)
    c = offer(3, rating=4.6, review_count=900, complaints=["smell when opened", "loose threads", "colour different"], seller_id=12)
    vals = scoring.offer_value_scores([a, b, c], {1: 70, 2: 66, 3: 52}, {10: 90, 11: 45, 12: 92})
    ranked = sorted([{"offer_id": k, "value": v, "landing_tmt": {1: 70, 2: 66, 3: 52}[k]} for k, v in vals.items()],
                    key=lambda x: x["value"]["score"], reverse=True)
    assert ranked[0]["offer_id"] in (1, 3)
    line = scoring.why_winner(ranked)
    assert line.startswith("#1 wins on")
    assert vals[2]["score"] < vals[1]["score"]
    assert scoring.why_winner(ranked[:1]).startswith("Only offer")


def sig(source="vidiq", platform="tiktok", creator="c", views=1_000_000, days=3, lang="tr", outlier=10, cls="product", value=None, raw=None):
    return NS(source=source, platform=platform, creator=creator, external_id=creator, views=views, outlier_score=outlier,
              language=lang, classification=cls, value=value, raw=raw or {}, url=f"https://x/{creator}", title=creator,
              published_at=NOW - timedelta(days=days), collected_at=NOW - timedelta(days=days))


def test_trend_score_components_and_cap():
    one = scoring.trend_score([sig(views=3_000_000, outlier=40)], now=NOW)
    assert one["score"] <= 35 and not one["is_trend"]
    # a single huge signal plus Google interest and local listings would exceed 35 → capped
    big = scoring.trend_score([sig(views=9_000_000, outlier=60), sig(source="google_trends", platform="google", value=100, raw={"slope": 3})],
                              local_listings=4, now=NOW)
    assert big["capped"] and big["score"] == 35 and "not a trend" in big["cap_reason"]
    many = scoring.trend_score([sig(creator="a"), sig(creator="b", platform="instagram"), sig(source="telegram", platform="telegram", creator="ch", views=1000),
                                sig(source="google_trends", platform="google", value=70, raw={"slope": 2, "geo": "TR"})],
                               sales_30d=20, sales_prev_30d=10, now=NOW)
    keys = {c["key"]: c for c in many["components"]}
    assert set(keys) == {"reach", "outlier", "breadth", "search", "local", "sales"}
    assert keys["sales"]["points"] > 0 and keys["search"]["points"] > 0
    assert many["score"] > one["score"]
    assert keys["reach"]["links"][0]["url"].startswith("https://")
    assert 0 <= many["score"] <= 100


def test_own_sales_weighted_highest():
    no_sales = scoring.trend_score([sig(creator="a"), sig(creator="b")], now=NOW)
    sales = scoring.trend_score([sig(creator="a"), sig(creator="b")], sales_30d=15, now=NOW)
    assert sales["score"] - no_sales["score"] >= 20


def test_lifecycle_rising_and_fading():
    rising = scoring.trend_score([sig(creator="a", days=2), sig(creator="b", days=4)], now=NOW)
    assert rising["state"] == "rising"
    fading = scoring.trend_score([sig(creator="a", days=25), sig(creator="b", days=20),
                                  sig(source="google_trends", platform="google", value=30, raw={"slope": -2})],
                                 snapshots=[(NOW - timedelta(days=28), 70), (NOW - timedelta(days=21), 60), (NOW - timedelta(days=7), 45)],
                                 now=NOW)
    assert fading["state"] == "fading"
    assert "down" in fading["state_reason"] or "falling" in fading["state_reason"]


def test_delivery_defaults_and_learning():
    defaults = {"seller_dispatch_days": [1, 2, 4], "china_domestic_days": [2, 3, 5], "cargo_transit_days": [12, 15, 20]}
    d = delivery.estimate(defaults)
    assert (d["min_days"], d["typical_days"], d["max_days"]) == (15, 20, 29)
    assert d["label"] == "15 to 29 days"
    learned = delivery.estimate(defaults, [19, 23, 21, 22, 20])
    assert "learned from 5" in learned["basis"]
    assert 18 <= learned["min_days"] <= 20 and learned["max_days"] <= 24
    slow = delivery.estimate(defaults, seller_delay_days=4)
    assert slow["typical_days"] == 24
