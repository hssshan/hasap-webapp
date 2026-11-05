"""End-to-end API tests through FastAPI's TestClient."""


def test_app_starts_with_no_keys_and_shows_not_connected(client):
    assert client.get("/api/health").json() == {"ok": True}
    setup = client.get("/api/setup").json()
    statuses = {i["key"]: i["status"] for i in setup["integrations"]}
    assert statuses["telegram_bot"] == "not connected"
    assert statuses["claude"] == "not connected"
    assert setup["telegram"]["bot"]["running"] is False
    assert client.get("/").status_code == 200
    assert "Sourcing Copilot" in client.get("/").text


def test_home_with_demo_data(seeded_client):
    home = seeded_client.get("/api/home").json()
    recs = home["recommendations"]
    assert 5 <= len(recs) <= 10
    top = recs[0]
    for k in ("trend_score", "best_offer", "landing_tmt", "sell_tmt", "profit_tmt", "delivery", "seller_score", "explanation"):
        assert k in top
    assert top["name"] == "crocheted hamster plush"
    labubu = next(r for r in recs if "labubu" in r["name"])
    assert labubu["counterfeit_risk"]
    assert home["mock_data"]


def test_product_detail_top3_and_links(seeded_client):
    d = seeded_client.get("/api/products/2").json()
    assert len(d["top3"]) == 3 and d["why_winner"].startswith("#1 wins")
    assert d["search_links"][0]["taobao"].startswith("https://s.taobao.com/search?q=")
    assert d["trend"]["components"]
    caps = seeded_client.post("/api/products/2/captions").json()
    assert "tk" in caps and "ru" in caps


def test_import_endpoint_auth(client, monkeypatch):
    payload = {"source": "vidiq", "items": [{"id": "1", "title": "Örgü hamster peluş hediye, link bio'da", "views": 10, "creator": "a"}]}
    assert client.post("/api/import/trends", json=payload).status_code == 503  # no secret configured
    monkeypatch.setenv("TREND_IMPORT_SECRET", "s3cret")
    assert client.post("/api/import/trends", json=payload).status_code == 401
    assert client.post("/api/import/trends", json=payload, headers={"X-Import-Secret": "nope"}).status_code == 401
    r = client.post("/api/import/trends", json=payload, headers={"X-Import-Secret": "s3cret"})
    assert r.status_code == 200 and r.json()["product"] == 1
    r = client.post("/api/import/trends", json=payload, headers={"Authorization": "Bearer s3cret"})
    assert r.status_code == 200 and r.json()["updated"] == 1


def test_manual_json_import_and_paste_box(client):
    r = client.post("/api/import/trends/manual", json={"json": '{"items":[{"id":"9","title":"Squishy haul 🫧 aldım","views":5000,"creator":"z"}]}'})
    assert r.status_code == 200 and r.json()["product"] == 1
    assert client.post("/api/import/trends/manual", json={"json": "{bad"}).status_code == 400
    r = client.post("/api/import/paste", json={"text": "#squishy 1.2M\nMini dijital kamera ucuz\nlip sync şarkı akım"})
    body = r.json()
    assert body["received"] == 3 and body["noise"] >= 1


def test_offer_flow_basket_shipment_stock_sale(client):
    pid = client.post("/api/products", json={"name": "crocheted hamster plush"}).json()["id"]
    r = client.post("/api/offers/extract", data={"url": "https://item.taobao.com/x"})
    assert r.json()["ai"] is False  # manual mode without key
    o = client.post("/api/offers", json={"product_id": pid, "url": "https://item.taobao.com/x", "title_en": "Hamster",
                                         "price_cny": "14.5", "weight_kg": "0.11", "price_tiers": [{"min_qty": 10, "price_cny": 13}],
                                         "store_name": "Hut", "store_age_years": "3", "store_rating": "4.9", "is_factory": False}).json()
    assert o["seller_name"] == "Hut"
    b = client.post("/api/basket/items", json={"offer_id": o["id"], "qty": 20}).json()
    assert b["totals"]["units"] == 20 and b["lines"][0]["unit_cny"] == 13
    text = client.get(f"/api/orders/{b['order']['id']}/export?lang=ru").text
    assert "Заказ" in text and "https://item.taobao.com/x" in text
    placed = client.post("/api/basket/place").json()
    assert client.get("/api/basket").json()["lines"] == []
    sid = placed["shipment_id"]
    assert client.post(f"/api/shipments/{sid}/status", json={"status": "in_transit"}).json()["status"] == "in_transit"
    client.post(f"/api/shipments/{sid}/status", json={"status": "arrived"})
    stock = client.get("/api/stock").json()
    assert stock[0]["stock"] == 20
    sale = client.post("/api/sales/quick", json={"text": "3 hamster 140"}).json()
    assert sale["stock"] == 17 and sale["profit_tmt"] > 0
    assert client.post("/api/sales/quick", json={"text": "gibberish"}).status_code == 400
    assert client.post(f"/api/shipments/{sid}/status", json={"status": "teleported"}).status_code == 400
    rep = client.get("/api/report/weekly").json()
    assert rep["totals"]["units"] == 3


def test_send_order_without_bot_gives_clear_error(seeded_client):
    r = seeded_client.post("/api/orders/1/send", json={})
    assert r.status_code == 400 and "not connected" in r.json()["detail"]


def test_settings_change_reprices(seeded_client):
    before = seeded_client.get("/api/products/2").json()["top3"][0]["pricing"]["landing_tmt"]
    seeded_client.put("/api/settings/pricing", json={"yuan_to_tmt": 4.0})
    after = seeded_client.get("/api/products/2").json()["top3"][0]["pricing"]["landing_tmt"]
    assert after > before
    assert seeded_client.put("/api/settings/nope", json={}).status_code == 404


def test_telegram_chat_approval(client):
    from app.db import SessionLocal, TelegramChat
    db = SessionLocal()
    db.add(TelegramChat(chat_id=42, name="Me", role="pending"))
    db.commit()
    db.close()
    assert client.post("/api/telegram/chats/42/approve", json={"role": "owner"}).json()["role"] == "owner"
    client.post("/api/telegram/chats/43/approve", json={"role": "person"})
    assert client.get("/api/settings").json()["settings"]["telegram"]["person_chat_id"] == 43


def test_clear_mock_data(seeded_client):
    r = seeded_client.post("/api/mock/clear").json()["deleted"]
    assert r["products"] >= 6 and r["sales"] > 0
    assert seeded_client.get("/api/home").json()["recommendations"] == []
    assert seeded_client.get("/api/home").json()["mock_data"] is False


def test_password_protection(monkeypatch, tmp_path):
    monkeypatch.setenv("APP_PASSWORD", "pw")
    from fastapi.testclient import TestClient
    from app.main import app
    with TestClient(app) as c:
        assert c.get("/api/home").status_code == 401
        assert c.get("/api/auth/status").json()["logged_in"] is False
        assert c.post("/api/auth/login", json={"password": "wrong"}).status_code == 401
        assert c.post("/api/auth/login", json={"password": "pw"}).status_code == 200
        assert c.get("/api/home").status_code == 200


def test_run_adapter_without_key(client):
    r = client.post("/api/adapters/youtube/run").json()
    assert r["status"] == "skipped"
    assert client.post("/api/adapters/nope/run").status_code == 404
