"""Real local DB, public booking and separate HTTP process; no Telegram calls."""
import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys
from datetime import timedelta
from uuid import uuid4
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from local_guard import require_local_database
require_local_database()
os.environ["TELEGRAM_BOT_TOKEN"] = ""
os.environ["MINIAPP_DEV"] = ""
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from aiohttp.test_utils import TestClient, TestServer
from api.__main__ import check_database
from api.server import build_app
from api import serializers
from core import config, db, service
from core.notifications import render_notification
from core.utils import now


async def main():
    g = await db.create_garage({"name": "ТЕСТ WEB", "district": "Москва",
        "price_per_hour": 500, "is_active": True, "open_hour": 9, "close_hour": 22,
        "owner_name": "PRIVATE_OWNER", "owner_phone": "PRIVATE_PHONE", "photos": ["test_web_photo"]})
    gid = g["id"]
    day = (now().date() + timedelta(days=1)).isoformat()
    payload = {"garage_id": gid, "date": day, "hours": [10, 11], "name": "Иван <тест>",
               "phone": "8 (900) 111-22-33", "consent": True}
    assert not config.TOKEN
    # A bot object must never be constructed for the standalone HTTP application.
    with patch("aiogram.Bot", side_effect=AssertionError("Telegram used")):
        app = build_app(website=True)
        app.on_startup.append(check_database)
    async with TestClient(TestServer(app)) as c:
        async def post(data=None, key=None, ip="203.0.113.1", **kwargs):
            return await c.post("/api/public/bookings", json=payload if data is None else data,
                headers={"Idempotency-Key": key or str(uuid4()), "X-Real-IP": ip}, **kwargs)

        for path in ("/api/health", "/api/ready", "/api/garages", "/", "/js/booking.js"):
            r = await c.get(path)
            assert r.status == 200, (path, await r.text())
        r = await c.get("/api/garages")
        assert "PRIVATE_" not in await r.text()
        r = await c.get("/api/photo/test_web_photo")
        assert r.status == 200 and r.content_type == "image/png"
        for path in ("/api/bookings", "/api/admin/inbox", "/api/me", "/api/public/bookings/1"):
            r = await c.get(path)
            assert r.status == 401
        for path in ("/api/bookings", "/api/bookings/1/unlock", "/api/public/bookings/1"):
            assert (await c.post(path, json={})).status == 401
        r = await c.post("/api/public/bookings", json=payload)
        assert r.status == 400
        r = await c.post("/api/public/bookings", json=payload,
            headers={"Idempotency-Key": str(uuid4()), "Origin": "https://evil.example"})
        assert r.status == 403
        r = await c.post("/api/public/bookings", json=payload,
            headers={"Idempotency-Key": str(uuid4()), "Origin": "http://[bad"})
        assert r.status == 403
        for change in ({"consent": False}, {"phone": "123"}, {"name": "A"}, {"hours": [True]},
                       {"hours": [10, 12]}, {"hours": [2]}, {"date": "2020-01-01"}, {"company": "spam"}):
            r = await post({**payload, **change})
            assert r.status == 400, (change, r.status, await r.text())
        key = str(uuid4())
        r = await post({**payload, "total_price": 1, "client_telegram_id": 999}, key=key)
        b = await r.json()
        assert r.status == 201 and b["status"] == "pending" and b["total"] == 1000, b
        assert b["can_cancel"] is False and "web_client_phone" not in b and "PRIVATE_" not in json.dumps(b)
        bid = b["id"]
        stored = await db.booking(bid)
        assert stored["client_telegram_id"] is None and stored["web_client_phone"] == "+79001112233"
        assert stored["web_client_hash"] != "203.0.113.1"
        r = await post(key=key)
        assert r.status == 201 and (await r.json())["id"] == bid
        r = await post({**payload, "name": "Другой"}, key=key)
        assert r.status == 409
        r = await post()
        assert r.status == 409
        r = await c.get(f"/api/garages/{gid}/availability")
        assert {10, 11} <= set((await r.json())["days"][1]["blocked"])
        rows = await db._run(db.supabase.table("notification_outbox").select("*")
            .eq("event_key", "booking:%s:created" % bid))
        assert rows and all(row["sent_at"] is None for row in rows)
        text, _ = await render_notification(rows[0])
        assert "+79001112233" in text and "&lt;тест&gt;" in text
        assert serializers.booking_admin(stored, g)["client_name"] == payload["name"]
        assert not await db.decide_booking(bid, "cancelled", 999)
        # Enable before confirmation, then rebind afterwards: neither path issues a guest grant.
        await db.set_lock_device(p_garage_id=gid, p_provider="simulator", p_provider_device_id="web-test-%s" % gid,
            p_enabled=True, p_actor_id=config.ADMIN_ID, p_reason="test")
        assert (await service.confirm_booking(None, bid, config.ADMIN_ID))[0]["status"] == "confirmed"
        await db.set_lock_device(p_garage_id=gid, p_provider="simulator", p_provider_device_id="web-test-%s" % gid,
            p_enabled=True, p_actor_id=config.ADMIN_ID, p_reason="test")
        grants = await db._run(db.supabase.table("access_grants").select("*").eq("booking_id", bid))
        assert grants == []
        assert not await db._run(db.supabase.table("notification_outbox").select("*")
            .eq("event_key", "booking:%s:confirmed" % bid))
        # Same database constraint for web and Telegram under concurrent creation.
        race = {**payload, "hours": [13], "phone": "+79002223344"}
        async def telegram():
            try:
                await service.create_booking_request(None, 900000099, "test", gid,
                    now().date() + timedelta(days=1), [13])
                return 201
            except service.ServiceError as error:
                return error.status
        async def website():
            return (await post(race, ip="203.0.113.2")).status
        statuses = await asyncio.gather(website(), telegram())
        assert sorted(statuses) == [201, 409], statuses
        # A delayed admin alert must not be mistaken for delivery to the client.
        tb, _ = await service.create_booking_request(None, 900000098, "test", gid,
            now().date() + timedelta(days=1), [21])
        sent = []
        class FakeBot:
            async def send_message(self, recipient, text, **kwargs):
                sent.append(recipient)
        _, _, delivered = await service.confirm_booking(FakeBot(), tb["id"], config.ADMIN_ID)
        assert delivered and sent == [900000098], sent
        # Durable IP limit across different phones; retries still work at the limit.
        limit_keys = []
        for hour in range(15, 20):
            limit_keys.append(str(uuid4()))
            r = await post({**payload, "hours": [hour], "phone": "+7900333000%d" % (hour - 15)},
                           key=limit_keys[-1], ip="203.0.113.3")
            assert r.status == 201, await r.text()
        r = await post({**payload, "hours": [20], "phone": "+79003330009"}, ip="203.0.113.3")
        assert r.status == 429, await r.text()
        r = await post({**payload, "hours": [15], "phone": "+79003330000"},
                       key=limit_keys[0], ip="203.0.113.3")
        assert r.status == 201
        # CLI uses OS/DB authority, independent of Telegram ID configuration.
        for command in (["cancel", str(bid)], ["list"]):
            p = await asyncio.to_thread(subprocess.run,
                [sys.executable, "-m", "tools.website_bookings"] + command,
                capture_output=True, text=True, timeout=20)
            assert p.returncode == 0, p.stderr
        assert (await db.booking(bid))["status"] == "cancelled"
        r = await post(key=key)
        assert r.status == 201 and (await r.json())["status"] == "cancelled"
    print("✓ Website: no token, public form, privacy, retries, conflicts, outbox, limits, guest grants and operator CLI")


if __name__ == "__main__":
    asyncio.run(main())
