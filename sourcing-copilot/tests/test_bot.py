"""Telegram bot with a mocked Telegram API (no network)."""
import pytest

from app.bot import handlers
from app.bot.api import FakeTelegramAPI, TelegramAPI
from app.db import Sale, Shipment, StockMovement, TelegramChat
from app.settings_store import set_setting

OWNER, PERSON, STRANGER = 111, 222, 999


def msg(chat_id, text, first_name="X"):
    return {"update_id": 1, "message": {"chat": {"id": chat_id, "first_name": first_name, "type": "private"}, "text": text}}


@pytest.fixture
def bot(seeded):
    seeded.add(TelegramChat(chat_id=OWNER, name="Me", role="owner"))
    seeded.add(TelegramChat(chat_id=PERSON, name="Aman", role="person"))
    seeded.commit()
    set_setting(seeded, "telegram", {"person_chat_id": PERSON})
    api = FakeTelegramAPI()
    return handlers.BotHandler(api), api, seeded


def last(api, chat_id):
    return [m for m in api.sent if m["chat_id"] == chat_id][-1]["text"]


def test_unknown_chat_is_registered_pending_and_otherwise_ignored(bot):
    h, api, db = bot
    h.handle_update(db, msg(STRANGER, "/start", "Stranger"))
    row = db.get(TelegramChat, STRANGER)
    assert row.role == "pending" and row.name == "Stranger"
    assert "Waiting for approval" in last(api, STRANGER) and str(STRANGER) in last(api, STRANGER)
    n = len(api.sent)
    h.handle_update(db, msg(STRANGER, "/top"))
    h.handle_update(db, msg(STRANGER, "sold 2 hamster 250"))
    assert len(api.sent) == n  # ignored completely
    assert db.query(Sale).filter(Sale.source == "bot").count() == 0


def test_env_allowed_chat_ids(monkeypatch, seeded):
    monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_IDS", "555, 666")
    assert handlers.role_of(seeded, 555) == "owner"
    assert handlers.role_of(seeded, 777) is None


@pytest.mark.parametrize("cmd,needle", [("/help", "/top"), ("/top", "What to order"), ("/stock", "Stock"),
                                        ("/profit", "30 days"), ("/orders", "Orders"), ("/digest", "Last 7 days")])
def test_owner_commands(bot, cmd, needle):
    h, api, db = bot
    h.handle_update(db, msg(OWNER, cmd))
    assert needle in last(api, OWNER)


def test_top_mentions_counterfeit_warning_and_profit(bot):
    h, api, db = bot
    h.handle_update(db, msg(OWNER, "/top"))
    text = last(api, OWNER)
    assert "crocheted hamster plush" in text and "TMT" in text


def test_person_cannot_see_profit_but_can_see_shipments(bot):
    h, api, db = bot
    h.handle_update(db, msg(PERSON, "/profit"))
    assert "Unknown command" in last(api, PERSON)
    h.handle_update(db, msg(PERSON, "/shipments"))
    with_buttons = [m for m in api.sent if m["chat_id"] == PERSON and m["buttons"]]
    assert with_buttons
    assert with_buttons[0]["buttons"][0][0]["callback_data"].startswith("ship:")


def test_quick_sale_logging(bot):
    h, api, db = bot
    before = db.query(Sale).count()
    h.handle_update(db, msg(OWNER, "sold 2 crocheted hamster 250"))
    reply = last(api, OWNER)
    assert "Logged 2 × crocheted hamster plush at 250 TMT = 500 TMT" in reply
    assert "stock left" in reply and "profit" in reply
    sale = db.query(Sale).order_by(Sale.id.desc()).first()
    assert db.query(Sale).count() == before + 1 and sale.qty == 2 and sale.unit_price_tmt == 250 and sale.source == "bot"
    assert db.query(StockMovement).filter(StockMovement.sale_id == sale.id).one().qty == -2


def test_ambiguous_and_unparseable_sales(bot):
    h, api, db = bot
    h.handle_update(db, msg(OWNER, "sold 2 plush 100"))
    assert "Which product" in last(api, OWNER)
    h.handle_update(db, msg(OWNER, "hello there"))
    assert "sold 2 hamster 250" in last(api, OWNER)


def test_person_sale_notifies_owner(bot):
    h, api, db = bot
    h.handle_update(db, msg(PERSON, "sold 1 camera 400"))
    assert "Logged 1 × mini digital camera" in last(api, PERSON)
    assert "Sale by your person" in last(api, OWNER)


def test_status_buttons_update_shipment_and_stock(bot):
    h, api, db = bot
    ship = db.query(Shipment).filter(Shipment.status == "in_transit").first()
    stock_before = db.query(StockMovement).filter(StockMovement.shipment_id == ship.id).count()
    assert stock_before == 0
    h.handle_update(db, {"update_id": 2, "callback_query": {"id": "cb1", "data": f"ship:{ship.id}:arrived",
                                                            "message": {"chat": {"id": PERSON}}, "from": {"id": PERSON}}})
    db.refresh(ship)
    assert ship.status == "arrived" and ship.arrived_at
    assert db.query(StockMovement).filter(StockMovement.shipment_id == ship.id, StockMovement.kind == "arrival").count() == 2
    assert api.callbacks[-1]["text"].startswith("Marked arrived")
    assert "Your person marked shipment" in last(api, OWNER)
    for st in ("delivered", "cash_received"):
        h.handle_update(db, {"update_id": 3, "callback_query": {"id": "cb", "data": f"ship:{ship.id}:{st}",
                                                                "message": {"chat": {"id": PERSON}}}})
    db.refresh(ship)
    assert ship.delivered_at and ship.cash_received_at and ship.status == "arrived"
    # arriving twice must not double stock
    h.handle_update(db, {"update_id": 4, "callback_query": {"id": "cb", "data": f"ship:{ship.id}:arrived",
                                                            "message": {"chat": {"id": PERSON}}}})
    assert db.query(StockMovement).filter(StockMovement.shipment_id == ship.id).count() == 2


def test_stranger_buttons_ignored(bot):
    h, api, db = bot
    ship = db.query(Shipment).first()
    h.handle_update(db, {"update_id": 5, "callback_query": {"id": "x", "data": f"ship:{ship.id}:sold_out",
                                                            "message": {"chat": {"id": STRANGER}}}})
    assert not api.callbacks


def test_digest_alerts_and_send_order(bot):
    h, api, db = bot
    assert handlers.send_digest(db, api) == 1
    assert "What to order" in last(api, OWNER)
    assert handlers.send_alerts(db, api, [{"message": "📈 test alert"}]) == 1
    from app import services
    order = services.draft_order(db)
    services.place_order(db, order)
    assert handlers.send_order(db, api, order, "ru") == PERSON
    texts = [m["text"] for m in api.sent if m["chat_id"] == PERSON]
    assert any("Заказ" in t for t in texts)
    assert any(m["buttons"] for m in api.sent if m["chat_id"] == PERSON)


def test_real_api_client_is_mockable():
    import httpx

    def handler(request):
        assert request.url.path.endswith("/sendMessage")
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 7}})

    api = TelegramAPI("123:ABC", client=httpx.Client(transport=httpx.MockTransport(handler)))
    assert api.send_message(1, "hi")["message_id"] == 7

    def bad(request):
        return httpx.Response(200, json={"ok": False, "description": "Unauthorized"})
    api2 = TelegramAPI("x", client=httpx.Client(transport=httpx.MockTransport(bad)))
    with pytest.raises(RuntimeError, match="Unauthorized"):
        api2.get_me()


def test_runner_does_not_start_without_token():
    from app.bot.runner import BotRunner
    r = BotRunner()
    assert r.start() is False
    assert r.status() == {"token_set": False, "running": False, "username": None, "error": None}


def test_runner_bad_token_does_not_crash(monkeypatch):
    from app.bot import runner as rmod
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "bad")

    class Boom(TelegramAPI):
        def get_me(self):
            raise RuntimeError("Unauthorized")
    monkeypatch.setattr(rmod, "TelegramAPI", Boom)
    r = rmod.BotRunner()
    assert r.start() is False and "Unauthorized" in r.status()["error"]
