"""Realistic demo data so every screen works immediately.

Trend items come from a real vidIQ run (Turkish content, October 2026) and go
through the same import pipeline as live data. Everything is flagged `is_mock`
and removed by `python -m app.cli clear-mock` / Settings → "Clear demo data".
"""
from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from . import services
from .adapters.vidiq_import import import_payload
from .db import (
    MOCK_TABLES, LocalPrice, Offer, Order, OrderItem, Product, Seller, SellerEvent, Shipment, TrendSignal,
    TrendSnapshot, utcnow,
)


def vidiq_mock_items(now: datetime) -> list[dict]:
    d = lambda days: (now - timedelta(days=days)).isoformat()  # noqa: E731
    return [
        # --- the real vidIQ run (TR, Oct 2026)
        {"id": "tt-7431001", "platform": "tiktok", "url": "https://www.tiktok.com/@oyuncakdunyasi/video/7431001",
         "title": "Kilo ile peluş oyuncak aldım! Tartıda tartıp satıyorlar 😱 nereden aldın diyenler link bio'da",
         "language": "tr", "creator": "oyuncakdunyasi", "views": 2_200_000, "outlier_score": 24, "published_at": d(9)},
        {"id": "ig-C9hamster1", "platform": "instagram", "url": "https://www.instagram.com/reel/C9hamster1/",
         "title": "Gen Z'nin yeni favorisi: örgü hamster peluş 🐹 hediye için bunu almalısın",
         "language": "tr", "creator": "orgu.atolyesi", "views": 1_100_000, "outlier_score": 31, "published_at": d(6)},
        {"id": "tt-7431077", "platform": "tiktok", "url": "https://www.tiktok.com/@teknoucuz/video/7431077",
         "title": "Ucuz mini dijital kamera Çin'den sipariş ettim, kutu açılımı 📷 retro fotoğraflar",
         "language": "tr", "creator": "teknoucuz", "views": 1_700_000, "outlier_score": 18, "published_at": d(4)},
        {"id": "tt-7430555", "platform": "tiktok", "url": "https://www.tiktok.com/@pazarturu/video/7430555",
         "title": "Pazarda squishy oyuncak buldum 🫧 tanesi 20 TL, stres oyuncağı alışverişi",
         "language": "tr", "creator": "pazarturu", "views": 1_300_000, "outlier_score": 12, "published_at": d(24)},
        {"id": "ig-C9keych01", "platform": "instagram", "url": "https://www.instagram.com/reel/C9keych01/",
         "title": "Sevgiliye hediye: bebek anahtarlık 🔑 çantama taktım, link bio'da",
         "language": "tr", "creator": "hediyelik.tr", "views": 790_000, "outlier_score": 9, "published_at": d(5)},
        # --- noise from the same run: must be filtered out
        {"id": "tt-noise-1", "platform": "tiktok", "url": "https://www.tiktok.com/@ayse/video/1",
         "title": "Lip sync to Tarkan şarkı 🎤 #akım #lipsync", "language": "tr", "creator": "ayse", "views": 3_400_000,
         "outlier_score": 40, "published_at": d(3)},
        {"id": "tt-noise-2", "platform": "tiktok", "url": "https://www.tiktok.com/@komikvideo/video/2",
         "title": "İskender girl 😂 yemek şakası #komik", "language": "tr", "creator": "komikvideo", "views": 2_900_000,
         "outlier_score": 22, "published_at": d(2)},
        {"id": "ig-noise-3", "platform": "instagram", "url": "https://www.instagram.com/reel/noise3/",
         "title": "Dans ederken yeni çantamı gösteriyorum 💃 #dans #trend", "language": "tr", "creator": "melis.dance",
         "views": 1_500_000, "outlier_score": 15, "published_at": d(1)},
        # --- extra independent signals (other creators / platforms / Russian run)
        {"id": "ig-C9kilo02", "platform": "instagram", "url": "https://www.instagram.com/reel/C9kilo02/",
         "title": "Kiloyla peluş oyuncak satan mağaza 🧸 tartıya koy, öde, al!", "language": "tr",
         "creator": "istanbul.kesif", "views": 480_000, "outlier_score": 7, "published_at": d(3)},
        {"id": "tt-7431200", "platform": "tiktok", "url": "https://www.tiktok.com/@bebekoyun/video/7431200",
         "title": "Örgü hamster peluş koleksiyonum 🐹 amigurumi hediye fikirleri", "language": "tr",
         "creator": "bebekoyun", "views": 260_000, "outlier_score": 6, "published_at": d(2)},
        {"id": "tt-ru-cam", "platform": "tiktok", "url": "https://www.tiktok.com/@gadget.ru/video/88001",
         "title": "Заказала мини камеру из Китая, распаковка 📷 ретро фото", "language": "ru",
         "creator": "gadget.ru", "views": 650_000, "outlier_score": 11, "published_at": d(7)},
        {"id": "tt-squish-old", "platform": "tiktok", "url": "https://www.tiktok.com/@squishmania/video/7400001",
         "title": "Squishy haul 🫧 50 tane squishy oyuncak aldım", "language": "tr", "creator": "squishmania",
         "views": 900_000, "outlier_score": 8, "published_at": d(26)},
        {"id": "tt-labubu", "platform": "tiktok", "url": "https://www.tiktok.com/@trendbul/video/7431300",
         "title": "Labubu peluş kutu açılımı, çakması mı orijinal mi? 😱", "language": "tr", "creator": "trendbul",
         "views": 3_000_000, "outlier_score": 20, "published_at": d(5)},
        {"id": "ig-labubu2", "platform": "instagram", "url": "https://www.instagram.com/reel/labubu2/",
         "title": "Labubu anahtarlık alışverişi 🛍️ nereden aldın diyenler", "language": "tr", "creator": "kesfet.alisveris",
         "views": 1_200_000, "outlier_score": 10, "published_at": d(4)},
    ]


def seed(db: Session, now: datetime | None = None) -> dict:
    if db.scalar(select(Product.id).limit(1)):
        return {"seeded": False, "reason": "database already has products"}
    now = now or utcnow()
    stats = import_payload(db, {"source": "vidiq", "items": vidiq_mock_items(now)}, is_mock=True)
    services.ingest_signals(db, "youtube", [
        {"external_id": "yt-kilo-ru", "url": "https://www.youtube.com/shorts/kiloRu01", "platform": "youtube",
         "title": "Мягкие игрушки на вес! Купил килограмм игрушек 🧸", "language": "ru", "creator": "ИгрушкиТВ",
         "views": 310_000, "published_at": (now - timedelta(days=5)).isoformat()},
    ], is_mock=True)
    services.ingest_signals(db, "paste", [
        {"external_id": "paste:cc-squishy", "platform": "tiktok", "title": "#squishy toys — TikTok Creative Center TR top hashtag",
         "language": "tr", "creator": "creative_center", "views": 0},
    ], is_mock=True)

    P = {p.name: p for p in db.scalars(select(Product))}
    kilo, hamster = P["plush toys sold by the kilo"], P["crocheted hamster plush"]
    camera, squishy = P["mini digital camera"], P["squishy toys"]
    keych = P["doll style keychains"]
    for p in P.values():
        p.is_mock = True
    hamster.watched = True
    camera.watched = True

    sellers = {
        "yiwu": Seller(name="义乌萌趣玩具厂 (Yiwu Mengqu Toy Factory)", platform="1688", store_age_years=8, total_sales=260_000,
                       rating=4.8, response_rate=96, dispute_rate=0.6, is_factory=True, location="Yiwu, Zhejiang", is_mock=True,
                       url="https://shop1688mengqu.1688.com"),
        "crochet": Seller(name="手作小屋 Handmade Hut", platform="taobao", store_age_years=3, total_sales=18_000, rating=4.9,
                          response_rate=99, dispute_rate=0.4, is_factory=False, location="Hangzhou", is_mock=True),
        "camera": Seller(name="深圳数码优选 Shenzhen Digital Select", platform="taobao", store_age_years=5, total_sales=52_000,
                         rating=4.7, response_rate=92, dispute_rate=1.8, is_factory=False, location="Shenzhen", is_mock=True),
        "camfac": Seller(name="宝安影像工厂 Bao'an Imaging Factory", platform="1688", store_age_years=6, total_sales=31_000,
                         rating=4.6, response_rate=88, dispute_rate=2.5, is_factory=True, location="Shenzhen", is_mock=True),
        "newshop": Seller(name="潮玩新店 Trendy Toys New Shop", platform="taobao", store_age_years=0.5, total_sales=900,
                          rating=4.5, response_rate=80, dispute_rate=None, is_factory=None, location="Guangzhou", is_mock=True),
    }
    db.add_all(sellers.values())
    db.flush()

    def offer(product, seller, **kw):
        o = Offer(product_id=product.id, seller_id=sellers[seller].id, platform=sellers[seller].platform, is_mock=True, **kw)
        db.add(o)
        return o

    o_ham1 = offer(hamster, "crochet", title="手工钩针仓鼠玩偶 毛线编织 可爱公仔", title_en="Handmade crochet hamster doll, knitted cute plush",
                   price_cny=14.5, price_tiers=[{"min_qty": 10, "price_cny": 13.0}, {"min_qty": 50, "price_cny": 11.5}],
                   domestic_shipping_cny=1.0, sales_count=12_000, rating=4.9, review_count=3_400, buyer_photos=620,
                   repeat_purchase_rate=18, weight_kg=0.11, size="12 cm", variants="beige, grey, orange",
                   praises=["very soft yarn", "looks exactly like photos", "neat stitching"], complaints=["slightly smaller than expected"],
                   url="https://item.taobao.com/item.htm?id=761234567890")
    offer(hamster, "yiwu", title="钩针仓鼠 挂件 批发", title_en="Crochet hamster pendant, wholesale", price_cny=9.8,
          price_tiers=[{"min_qty": 100, "price_cny": 8.6}], domestic_shipping_cny=0, sales_count=45_000, rating=4.6,
          review_count=900, buyer_photos=80, repeat_purchase_rate=32, weight_kg=0.09, size="10 cm",
          praises=["cheap in bulk"], complaints=["loose threads on some", "colour different from photo", "smell when opened"],
          url="https://detail.1688.com/offer/712345678901.html")
    offer(hamster, "newshop", title="网红仓鼠针织玩偶", title_en="Viral knitted hamster doll", price_cny=12.0,
          domestic_shipping_cny=3.0, sales_count=300, rating=4.5, review_count=60, buyer_photos=4, weight_kg=None,
          praises=["cute"], complaints=["slow dispatch"], url="https://item.taobao.com/item.htm?id=769999000111")

    o_kilo = offer(kilo, "yiwu", title="娃娃机公仔 按斤卖 混款毛绒玩具 20-25cm", title_en="Claw machine plush mix, sold per jin, 20-25 cm",
                   price_cny=32.0, price_tiers=[{"min_qty": 20, "price_cny": 28.0}], domestic_shipping_cny=0, sales_count=88_000,
                   rating=4.7, review_count=5_100, buyer_photos=900, repeat_purchase_rate=41, weight_kg=1.0,
                   size="per 1 kg ≈ 5-6 toys", variants="random mix", praises=["good mix", "clean, no smell"],
                   complaints=["random styles, some repeats"], url="https://detail.1688.com/offer/700011122233.html")
    offer(kilo, "newshop", title="毛绒玩具散货称斤", title_en="Loose plush toys by weight", price_cny=26.0, domestic_shipping_cny=6.0,
          sales_count=1_200, rating=4.4, review_count=140, buyer_photos=12, weight_kg=1.0,
          complaints=["some toys dirty", "shedding"], url="https://item.taobao.com/item.htm?id=760000111222")

    o_cam = offer(camera, "camera", title="迷你数码相机 学生 复古CCD 4K", title_en="Mini digital camera, retro CCD style, students",
                  price_cny=48.0, price_tiers=[{"min_qty": 5, "price_cny": 45.0}], domestic_shipping_cny=0, sales_count=26_000,
                  rating=4.7, review_count=7_800, buyer_photos=1_500, repeat_purchase_rate=9, weight_kg=0.22,
                  size="9×6 cm", variants="pink, white, black", praises=["cute retro look", "works out of box"],
                  complaints=["photo quality average", "battery drains fast"], url="https://item.taobao.com/item.htm?id=755556667778")
    offer(camera, "camfac", title="儿童数码相机 工厂直销", title_en="Kids digital camera, factory direct", price_cny=36.0,
          price_tiers=[{"min_qty": 50, "price_cny": 31.0}], domestic_shipping_cny=4.0, sales_count=9_000, rating=4.5,
          review_count=1_100, buyer_photos=150, repeat_purchase_rate=14, weight_kg=0.28,
          complaints=["some arrived broken", "no memory card", "smaller than listed"],
          url="https://detail.1688.com/offer/733344455566.html")

    o_sq = offer(squishy, "yiwu", title="捏捏乐 慢回弹 解压玩具 混款", title_en="Slow-rise squishy stress toys, mixed",
                 price_cny=2.8, price_tiers=[{"min_qty": 100, "price_cny": 2.2}], domestic_shipping_cny=0, sales_count=210_000,
                 rating=4.8, review_count=12_000, buyer_photos=2_100, repeat_purchase_rate=37, weight_kg=0.05,
                 praises=["soft", "slow rise", "kids love them"], complaints=["slight smell at first"],
                 url="https://detail.1688.com/offer/799988877766.html")
    offer(keych, "crochet", title="娃娃钥匙扣 包包挂件", title_en="Doll keychain bag charm", price_cny=6.5, domestic_shipping_cny=1.0,
          sales_count=4_000, rating=4.8, review_count=800, buyer_photos=120, weight_kg=0.04,
          praises=["cute", "good gift"], complaints=["ring a bit weak"], url="https://item.taobao.com/item.htm?id=768887776665")
    db.flush()

    # past orders + shipments (for learned delivery times, stock and real margins)
    def past_order(items, ordered_days_ago, transit_days, status="arrived", code=""):
        o = Order(status="draft", is_mock=True)
        db.add(o)
        db.flush()
        for off, qty in items:
            db.add(OrderItem(order_id=o.id, offer_id=off.id, product_id=off.product_id, qty=qty, is_mock=True))
        db.flush()
        db.refresh(o)
        ship = services.place_order(db, o)
        o.placed_at = now - timedelta(days=ordered_days_ago)
        ship.is_mock, ship.code = True, code or ship.code
        ship.ordered_at = now - timedelta(days=ordered_days_ago)
        ship.china_warehouse_at = ship.ordered_at + timedelta(days=4)
        ship.in_transit_at = ship.ordered_at + timedelta(days=6)
        if status in ("arrived", "sold_out"):
            services.set_shipment_status(db, ship, "arrived")
            ship.arrived_at = ship.ordered_at + timedelta(days=transit_days)
            ship.delivered_at = ship.arrived_at + timedelta(days=1)
            ship.cash_received_at = ship.arrived_at + timedelta(days=3)
            ship.status = status
        else:
            ship.status = status
            ship.arrived_at = None
            ship.china_warehouse_at = ship.china_warehouse_at if status != "ordered" else None
        db.commit()
        return ship

    s1 = past_order([(o_sq, 100), (o_kilo, 10)], 70, 19, "sold_out", "AG-0915")
    s2 = past_order([(o_ham1, 40), (o_sq, 60)], 45, 23, "arrived", "AG-1003")
    s3 = past_order([(o_kilo, 15), (o_ham1, 20)], 32, 21, "arrived", "AG-1012")
    s4 = past_order([(o_cam, 10), (o_ham1, 30)], 9, 0, "in_transit", "AG-1027")
    for st in (s1, s2, s3, s4):
        for it in st.order.items:
            it.is_mock = True
        st.order.is_mock = True
    db.add(SellerEvent(seller_id=sellers["newshop"].id, kind="delay", days_late=4, note="dispatched 4 days late", is_mock=True))
    db.add(SellerEvent(seller_id=sellers["camfac"].id, kind="damage", note="2 cameras arrived with cracked screens", is_mock=True))
    db.add(SellerEvent(seller_id=sellers["yiwu"].id, kind="good", note="packed very well", is_mock=True))
    db.commit()
    for m in db.query(services.StockMovement).all():
        m.is_mock = True

    # sales history (last ~60 days)
    plan = [  # (product, qty, price, days_ago)
        (squishy, 6, 25, 58), (squishy, 10, 25, 52), (squishy, 8, 20, 47), (kilo, 1, 650, 50), (kilo, 2, 600, 44),
        (squishy, 12, 25, 40), (squishy, 9, 20, 34), (hamster, 4, 130, 20), (hamster, 6, 140, 16), (squishy, 7, 20, 26),
        (kilo, 3, 600, 18), (hamster, 5, 130, 12), (hamster, 7, 130, 8), (kilo, 2, 650, 6), (squishy, 4, 20, 9),
        (hamster, 8, 140, 4), (squishy, 3, 20, 3), (hamster, 6, 130, 2), (kilo, 2, 600, 1), (squishy, 2, 15, 1),
    ]
    for prod, qty, price, ago in plan:
        services.record_sale(db, prod, qty, price, source="app", sold_at=now - timedelta(days=ago, hours=3), is_mock=True)

    # local market (Turkmen Telegram channels)
    local_posts = [
        (hamster, 150, "ashgabat_oyunjak", "Örme hamster oýnawaç 🐹 bahasy 150 TMT, eltip bermek bar", 4),
        (hamster, 120, "tm_sowgatlar", "Hamster ýumşak oýnawaç täze geldi! Bahasy: 120 manat", 6),
        (hamster, 160, "aşgabat_market", "Вязаный хомяк игрушка, цена 160 ман", 10),
        (camera, 380, "tm_gadgets", "Mini sanly kamera, retro surat. Bahasy 380 TMT", 3),
        (camera, 420, "tm_gadgets", "Мини камера для фото, цена 420 тмт, доставка", 12),
        (squishy, 20, "ashgabat_oyunjak", "Squishy antistress oýnawaçlar 20 TMT", 20),
        (squishy, 15, "tm_sowgatlar", "Сквиш антистресс 15 ман, много видов", 15),
        (kilo, 550, "tm_sowgatlar", "Kilo bilen ýumşak oýnawaç! 1 kg (5-6 sany) bahasy 550 TMT", 9),
        (kilo, 480, "ashgabat_oyunjak", "Мягкие игрушки на вес, 1 кг — 480 ман", 13),
    ]
    for prod, price, ch, text, ago in local_posts:
        when = now - timedelta(days=ago)
        db.add(LocalPrice(product_id=prod.id, price_tmt=price, source="telegram", channel=ch,
                          url=f"https://t.me/{ch}/{1000 + ago}", text=text, seen_at=when, is_mock=True))
        db.add(TrendSignal(source="telegram", platform="telegram", external_id=f"{ch}/{1000 + ago}-{prod.id}",
                           url=f"https://t.me/{ch}/{1000 + ago}", title=text, creator=ch, views=1500 + ago * 40,
                           published_at=when, product_id=prod.id, classification="product", value=price,
                           language="tk", is_mock=True))
    # Google Trends mock series
    gt = [(hamster, "TR", 64, 1.8), (camera, "TR", 71, 2.6), (camera, "RU", 55, 1.1), (squishy, "TR", 38, -1.9), (kilo, "TR", 47, 0.9)]
    for prod, geo, val, slope in gt:
        db.add(TrendSignal(source="google_trends", platform="google", external_id=f"{prod.id}:{geo}", product_id=prod.id,
                           title=f"Google Trends {geo}: {prod.search_terms.get('tr', prod.name)}", value=val,
                           url=f"https://trends.google.com/trends/explore?geo={geo}", classification="product",
                           raw={"geo": geo, "slope": slope, "series": []}, collected_at=now - timedelta(days=1), is_mock=True))
    # score history for lifecycle (6 weeks)
    hist = {hamster: [30, 38, 47, 55, 62], camera: [18, 25, 33, 44, 52], squishy: [72, 70, 64, 57, 50], kilo: [40, 46, 52, 55, 56]}
    for prod, scores in hist.items():
        for i, sc in enumerate(scores):
            db.add(TrendSnapshot(product_id=prod.id, day=(now - timedelta(days=35 - i * 7)).date(), score=sc, is_mock=True))
    db.commit()

    # a draft basket so the Order screen isn't empty
    services.add_to_basket(db, o_ham1, 30, "beige")
    services.add_to_basket(db, o_cam, 10, "pink")
    draft = services.draft_order(db)
    draft.is_mock = True
    for it in draft.items:
        it.is_mock = True
    db.commit()
    for p in db.scalars(select(Product)):
        services.ensure_search_terms(db, p)
    services.recompute_trends(db, now)
    return {"seeded": True, "vidiq": {k: stats[k] for k in ("received", "product", "noise")}}


def clear_mock(db: Session) -> dict:
    counts = {}
    for model in MOCK_TABLES:
        res = db.execute(delete(model).where(model.is_mock.is_(True)))
        counts[model.__tablename__] = res.rowcount
    db.commit()
    return counts
