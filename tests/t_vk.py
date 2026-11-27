"""VK callbacks + real shared handlers + isolated PostgreSQL; no VK/Telegram network calls."""
import asyncio
import copy
import json
import os
from pathlib import Path
import sys
from unittest.mock import AsyncMock, patch
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parent))
from local_guard import require_local_database
require_local_database()
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ["VK_ADMIN_IDS"] = "701"
os.environ["VK_GROUP_TOKEN"] = "test-only-no-network"

from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from postgrest.exceptions import APIError

from core import config, db, locks, notifications, service
from core.actors import booking_actor, event_key
from core.utils import at_hour, is_admin, now
from tgbot import keyboards, texts
from tgbot.app import build_dispatcher
from tgbot.callbacks import AddCb, EditCb, BookCb, DecideCb, MyCb, ObjCb, OwnerCb, SearchCb, UnlockCb
from tools.local_db import sql
from vkbot.__main__ import propagate_error
from vkbot.client import VKClient, VKError, keyboards as vk_keyboards, plain, trusted_media_url
from vkbot.transport import VKSession
from vkbot.webhook import VKWebhook, WEBHOOK_PATH, private_event, validate_config

GROUP, USER, VKADMIN = 333, 700, 701
SECRET = "test-secret-at-least-sixteen"


class FakeVK(VKClient):
    def __init__(self):
        super().__init__("test-no-network")
        self.sent, self.calls = [], []
        self.next_id = 100

    async def send(self, *args, **kwargs):
        with patch("vkbot.client.asyncio.sleep", new=AsyncMock()):
            return await super().send(*args, **kwargs)

    async def photo(self, source, peer_id):
        return "photo-333_1"

    async def api(self, method, **params):
        self.calls.append((method, params))
        if method == "messages.send":
            assert params["random_id"] > 0
            assert int(params["peer_ids"]) > 0
            markup = json.loads(params.get("keyboard", '{"inline":true,"buttons":[]}'))
            assert len(markup["buttons"]) <= (6 if markup["inline"] else 10)
            assert all(len(row) <= 4 for row in markup["buttons"])
            self.next_id += 1
            self.sent.append({**params, "user": int(params["peer_ids"]), "mid": self.next_id})
            return [{"peer_id": int(params["peer_ids"]), "message_id": self.next_id + 1000,
                     "conversation_message_id": self.next_id}]
        if method == "messages.edit":
            self.sent.append({**params, "user": params["peer_id"], "mid": params["conversation_message_id"]})
        return 1


async def main():
    sql("TRUNCATE vk_events,device_events,audit_log,notification_outbox,lock_commands,unlock_actions,"
        "access_grants,lock_devices,bookings,garages,owner_requests,telegram_updates,service_heartbeats "
        "RESTART IDENTITY CASCADE;")
    fake = FakeVK()
    bot = Bot("333:VKTRANSPORTONLYTOKENNOTATELEGRAMSECRET", session=VKSession(fake, GROUP),
              default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = build_dispatcher(error_handler=propagate_error)
    handler = VKWebhook(bot, dp, GROUP, SECRET, "confirmed-code")
    app = web.Application(client_max_size=64 * 1024)
    app.router.add_post(WEBHOOK_PATH, handler.handle)
    g = await db.create_garage({"name": "ТЕСТ ВК", "district": "Марьино", "address": "Тестовая, 1",
        "type": "garage", "price_per_hour": 500, "open_hour": 0, "close_hour": 24, "is_active": True})
    event_seq = 0

    def event(uid=USER, text="/start", callback=None):
        nonlocal event_seq
        event_seq += 1
        obj = {"message": {"id": event_seq, "conversation_message_id": event_seq,
                           "from_id": uid, "peer_id": uid, "text": text, "attachments": []}}
        kind = "message_new"
        if callback:
            kind = "message_event"
            obj = {"user_id": uid, "peer_id": uid, "event_id": "button-%s" % event_seq,
                   "conversation_message_id": max(1, fake.next_id), "payload": {"gh": callback}}
        return {"group_id": GROUP, "secret": SECRET, "type": kind,
                "event_id": "event-%s" % event_seq, "object": obj}

    async with TestClient(TestServer(app)) as client:
        async def post(payload):
            return await client.post(WEBHOOK_PATH, json=payload)

        async def process(payload):
            response = await post(payload)
            assert response.status == 200, await response.text()
            row = await db.vk_event_claim(GROUP)
            assert row
            await handler.process(row)
            await db.vk_event_finish(row, True)
            return row

        async def click(data, uid=USER):
            return await process(event(uid, callback=data.pack()))

        # Callback security, correct plaintext handshake and durable duplicate protection.
        confirmation = {"type": "confirmation", "group_id": GROUP, "secret": SECRET}
        assert await (await post(confirmation)).text() == "confirmed-code"
        for altered in ({"secret": "wrong"}, {"group_id": GROUP + 1}, {"group_id": True}):
            assert (await post({**confirmation, **altered})).status == 403
        payload = event(text="/id")
        wrong = copy.deepcopy(payload)
        wrong["object"]["message"]["peer_id"] = 2000000001
        assert (await post(wrong)).status == 200
        bad = event(callback="m:view:1")
        bad["object"]["payload"]["gh"] = {"user_id": 701}
        assert (await post(bad)).status == 400
        assert not await db._run(config.supabase.table("vk_events").select("id"))
        await asyncio.gather(post(payload), post(payload))
        stored = await db._run(config.supabase.table("vk_events").select("*"))
        assert len(stored) == 1 and "secret" not in stored[0]["payload"]
        row = await db.vk_event_claim(GROUP)
        await handler.process(row)
        await db.vk_event_finish(row, True)
        assert any("VK ID: 700" in m["message"] for m in fake.sent)
        assert await db.vk_event_claim(GROUP) is None
        assert await (await post(payload)).text() == "ok"
        assert await db.vk_event_claim(GROUP) is None

        await process(event())
        assert any(m["user"] == USER and "GarageHub" in m["message"] for m in fake.sent)
        await process(event(text=keyboards.Btn.FIND))
        await click(SearchCb(action="type", value="garage"))
        await click(SearchCb(action="show"))
        assert any("ТЕСТ ВК" in m["message"] for m in fake.sent)
        await click(BookCb(action="start", value=str(g["id"])))
        day = service.window_days()[1]
        await click(BookCb(action="date", value=day.isoformat()))
        await click(BookCb(action="slot", value="10"))
        submitted = await click(BookCb(action="send"))
        mine = await db.client_bookings(-USER)
        assert len(mine) == 1
        booking = mine[0]
        assert booking["client_vk_id"] == USER and booking["client_telegram_id"] is None
        assert booking["total_price"] == 500 and booking["status"] == "pending"
        assert not booking["demo_payment_token"]
        assert not await db.client_bookings(USER)  # Same number is a different Telegram account.
        assert "ВКонтакте" in texts.booking_client(booking)
        assert is_admin(-VKADMIN) and not is_admin(VKADMIN) and not is_admin(-config.ADMIN_ID)
        try:
            await service.confirm_booking(bot, booking["id"], VKADMIN)
            raise AssertionError("VK ID granted Telegram admin rights")
        except service.ServiceError as error:
            assert error.status == 403
        try:
            await service.cancel_booking(bot, USER, booking["id"])
            raise AssertionError("Telegram user cancelled a VK booking")
        except service.ServiceError as error:
            assert error.code == "not_found"
        # Forged/stale callback cannot bypass ownership.
        before = len(fake.sent)
        await click(MyCb(action="cancel_yes", booking_id=booking["id"]), uid=USER + 10)
        assert (await db.booking(booking["id"]))["status"] == "pending"
        assert before <= len(fake.sent)
        # Telegram confirmation sends to the VK account, never a numeric Telegram lookalike.
        native = type("NativeBot", (), {"send_message": AsyncMock()})()
        with patch("vkbot.client.VKClient", return_value=fake):
            result = await service.confirm_booking(native, booking["id"], config.ADMIN_ID)
            assert result[0]["status"] == "confirmed" and result[2]
        assert any(m["user"] == USER and "подтверждена" in m["message"].lower() for m in fake.sent)
        # Replay after a committed transaction and a lost send restores the receipt, no second row.
        await handler.process(submitted)
        assert len(await db.client_bookings(-USER)) == 1
        await process(event(text=keyboards.Btn.BOOKINGS))
        await click(MyCb(action="view", booking_id=booking["id"]))
        assert any("yandex.ru/maps" in json.dumps(m) for m in fake.sent)
        await click(MyCb(action="cancel", booking_id=booking["id"]))
        assert (await db.booking(booking["id"]))["status"] == "confirmed"
        await click(MyCb(action="cancel_yes", booking_id=booking["id"]))
        assert (await db.booking(booking["id"]))["status"] == "cancelled"

        # Owner form uses the existing FSM, but no VK ID is written as a Telegram identity.
        await process(event(text=keyboards.Btn.LIST))
        await process(event(text="Владелец ВК"))
        await process(event(text="+79001112233"))
        await click(OwnerCb(value="garage"))
        await process(event(text="Москва, Тестовая, 1"))
        leads = await db.owner_requests()
        assert len(leads) == 1 and leads[0]["vk_id"] == USER and leads[0]["telegram_id"] is None
        await click(ObjCb(action="delete_yes", garage_id=g["id"]), uid=USER)
        assert (await db.garage(g["id"]))["is_active"]
        # VK administrator gets the real admin menu and can archive/restore the shared object.
        await process(event(VKADMIN))
        assert any(m["user"] == VKADMIN and "Панель" in json.dumps(m, ensure_ascii=False) for m in fake.sent)
        await click(ObjCb(action="delete", garage_id=g["id"]), uid=VKADMIN)
        # Hiding asks for confirmation in the existing admin flow; exercise actual action below.
        assert (await db.garage(g["id"]))["is_active"]
        await click(ObjCb(action="delete_yes", garage_id=g["id"]), uid=VKADMIN)
        assert not (await db.garage(g["id"]))["is_active"]
        await click(ObjCb(action="restore", garage_id=g["id"]), uid=VKADMIN)
        assert (await db.garage(g["id"]))["is_active"]

        # Create and edit an object through the actual shared admin FSM.
        await process(event(VKADMIN, text=keyboards.Btn.ADD))
        await click(AddCb(field="type", value="detailing"), uid=VKADMIN)
        for text in ("ТЕСТ ВК новый объект", "Химки", "Москва, Тестовая, 2"):
            await process(event(VKADMIN, text=text))
        photo_event = event(VKADMIN, text="")
        photo_event["object"]["message"]["attachments"] = [{"type": "photo", "photo": {"sizes": [
            {"url": "https://sun1.userapi.com/test.jpg", "width": 500, "height": 500}]}}]
        with patch.object(fake, "save_incoming_photo", new=AsyncMock(return_value="local:test-workbench-v1.jpg")):
            await process(photo_event)
        await click(AddCb(field="photos_done"), uid=VKADMIN)
        await process(event(VKADMIN, text="600"))
        await click(AddCb(field="size", value="standard"), uid=VKADMIN)
        await process(event(VKADMIN, text="9:00–22:00"))
        for _ in range(4):
            await click(AddCb(field="skip"), uid=VKADMIN)
        published = await click(AddCb(field="publish"), uid=VKADMIN)
        created = [item for item in await db.garages() if item["name"] == "ТЕСТ ВК новый объект"]
        assert len(created) == 1 and created[0]["photos"] == ["local:test-workbench-v1.jpg"]
        await handler.process(published)
        assert len([item for item in await db.garages() if item["name"] == "ТЕСТ ВК новый объект"]) == 1
        await click(EditCb(field="price_per_hour", garage_id=created[0]["id"]), uid=VKADMIN)
        await process(event(VKADMIN, text="700"))
        assert (await db.garage(created[0]["id"]))["price_per_hour"] == 700

        # VK administrators can decide Telegram bookings using the same inbox.
        telegram_booking, _ = await service.create_booking_request(None, USER + 20, "telegram_client", g["id"], day, [15])
        await process(event(VKADMIN, text=keyboards.Btn.INBOX))
        await click(DecideCb(action="ok", booking_id=telegram_booking["id"]), uid=VKADMIN)
        assert (await db.booking(telegram_booking["id"]))["status"] == "confirmed"
        assert not any(m["user"] == USER + 20 for m in fake.sent)
        native.send_message.reset_mock()
        assert await notifications.deliver_one(native, telegram_booking["id"])
        assert native.send_message.call_args.args[0] == USER + 20
        rejected, _ = await service.create_booking_request(None, USER + 21, "telegram_client", g["id"], day, [16])
        await click(DecideCb(action="reason", booking_id=rejected["id"], reason="Другое время"), uid=VKADMIN)
        assert (await db.booking(rejected["id"]))["status"] == "rejected"

        # Distinct actors share the same exclusion constraint and time slots.
        async def race(actor):
            from core.actors import event_key
            key = event_key.set(str(uuid4()))
            try:
                b, _ = await service.create_booking_request(None, actor, None, g["id"], day, [13])
                return b
            except service.ServiceError as error:
                return error.status
            finally:
                event_key.reset(key)
        results = await asyncio.gather(race(-USER), race(USER))
        assert sum(isinstance(item, dict) for item in results) == 1 and 409 in results, results
        # Grants and one-use unlock actions respect VK ownership, with identical numeric TG ID denied.
        device = await db.set_lock_device(p_garage_id=g["id"], p_provider="simulator",
            p_provider_device_id="vk-test", p_enabled=True, p_actor_id=config.ADMIN_ID, p_reason="Test")
        active = await db.create_booking({"garage_id": g["id"], "client_vk_id": USER,
            "start_time": (now() - __import__('datetime').timedelta(minutes=5)).isoformat(),
            "end_time": (now() + __import__('datetime').timedelta(minutes=30)).isoformat(),
            "total_price": 500, "status": "pending"})
        await db.decide_booking(active["id"], "confirmed", config.ADMIN_ID)
        action = await db.issue_unlock_action(active["id"], -USER)
        assert action and not await db.issue_unlock_action(active["id"], USER)
        command = await locks.request_open(-USER, active["id"], action["id"], "bot")
        assert command["actor_id"] == -USER
        assert (await locks.request_open(-USER, active["id"], action["id"], "bot"))["id"] == command["id"]
        try:
            await locks.request_open(USER, active["id"], action["id"], "bot")
            raise AssertionError("Telegram lookalike opened a VK booking")
        except service.ServiceError:
            pass
        # Website capabilities never turn a VK booking into a guest booking.
        assert not await db.website_bookings([active["web_booking_token"]])
        assert not await db.cancel_website_booking(active["web_booking_token"])
        await fake.send(USER, "Обычный ответ")
        assert "keyboard" not in fake.sent[-1]
        # Keyboard conversion preserves every row and replaces the unsupported contact button.
        many = keyboards.my_bookings([(dict(booking, id=i), "Бокс", now(), now()) for i in range(30)])
        parts = vk_keyboards(many)
        assert sum(len(k["buttons"]) for k in parts) == 30 and all(len(k["buttons"]) <= 6 for k in parts)
        assert plain('<b>Бокс</b> &amp; <a href="https://example.com">карта</a>') == 'Бокс & карта (https://example.com)'
        assert not trusted_media_url("http://127.0.0.1/private")
        assert not trusted_media_url("https://userapi.com.evil.example/image")
        assert trusted_media_url("https://sun1.userapi.com/photo.jpg")
        # Earlier failed messages block only their own conversation, preserving form order.
        await post(event(USER + 100, text="first"))
        await post(event(USER + 100, text="second"))
        await post(event(USER + 101, text="other"))
        first = await db.vk_event_claim(GROUP)
        assert first["payload"]["object"]["message"]["text"] == "first"
        await db.vk_event_finish(first, False)
        other = await db.vk_event_claim(GROUP)
        assert other["payload"]["object"]["message"]["text"] == "other"
        await db.vk_event_finish(other, True)
        assert await db.vk_event_claim(GROUP) is None
        scrubbed = await db._run(config.supabase.table("vk_events").select("payload").eq("id", other["id"]))
        assert scrubbed == [{"payload": {}}]

        # A legacy Telegram worker can never lease a VK notification.
        sql("UPDATE notification_outbox SET sent_at=now() WHERE recipient_id>0;")
        assert await db.claim_notification() is None
        row = await db.claim_notification(channels=["vk"])
        assert row and row["recipient_id"] < 0
        await db.finish_notification(row, False)

    await dp.emit_shutdown(bot=bot)
    await bot.session.close()
    print("✓ VK: callback auth/inbox/dedup, shared search/booking/cancel/owner/admin, cross-channel identity/slots/notifications/grants, receipts, media boundaries and keyboards")


if __name__ == "__main__":
    asyncio.run(main())
