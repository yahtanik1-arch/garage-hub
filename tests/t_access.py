"""Настоящие транзакции PostgreSQL + PostgREST, фиктивные только Telegram и замок.

Запускать через python -m tools.local_db test. Очищает выделенную локальную базу.
"""
import asyncio
from datetime import datetime, timedelta, timezone
import importlib
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import AsyncMock, patch
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from local_guard import require_local_database
require_local_database()

import httpx
from aiohttp.test_utils import TestClient, TestServer
from postgrest.exceptions import APIError

from api import server
from core import config, db, locks, notifications, service
from core.lock_simulator import LockSimulator
from core.worker import Worker
from tools.local_db import migrate, restore_check, sql, ROOT
from tools.local_bot import LocalBot
from tgbot.webhook import WebhookHandler, WEBHOOK_PATH

USER, OTHER, ADMIN = 900000001, 900000003, config.ADMIN_ID
RESET = ("TRUNCATE device_events, audit_log, notification_outbox, lock_commands, unlock_actions, "
         "access_grants, lock_devices, bookings, garages, owner_requests, telegram_updates, service_heartbeats "
         "RESTART IDENTITY CASCADE;")


async def rows(table):
    return await db._run(config.supabase.table(table).select("*"))


class AccessTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        sql(RESET)
        self.bot = LocalBot(config.TOKEN)
        self.adapter = LockSimulator()
        self.worker = Worker(self.bot, self.adapter)
        self.g = await db.create_garage({"name": "ТЕСТ Замок", "address": "Тестовая, 1",
            "district": "Марьино", "type": "garage", "price_per_hour": 500,
            "open_hour": 0, "close_hour": 24, "is_active": True})
        self.device = await locks.configure_device(ADMIN, self.g["id"], "simulator", "fake-device", True, "Локальная проверка")

    async def asyncTearDown(self):
        await self.bot.session.close()

    async def booking(self, user=USER, start=None, end=None, garage=None, confirm=True):
        current = datetime.now(timezone.utc)
        b = await db.create_booking({"garage_id": (garage or self.g)["id"], "client_telegram_id": user,
            "start_time": (start or current - timedelta(minutes=5)).isoformat(),
            "end_time": (end or current + timedelta(minutes=30)).isoformat(),
            "total_price": 500, "status": "pending"})
        if confirm:
            b = await db.decide_booking(b["id"], "confirmed", ADMIN)
        return b

    async def command(self, booking=None, key=None):
        b = booking or await self.booking()
        return await locks.request_open(USER, b["id"], key or str(uuid4()))

    async def test_database_prevents_concurrent_overlap_and_accepts_neighbours(self):
        start = datetime.now(timezone.utc) + timedelta(hours=2)
        async def insert(user, begin):
            return await self.booking(user=user, start=begin, end=begin + timedelta(hours=1), confirm=False)
        results = await asyncio.gather(insert(USER, start), insert(OTHER, start), return_exceptions=True)
        self.assertEqual(sum(isinstance(x, dict) for x in results), 1)
        self.assertEqual([x.code for x in results if isinstance(x, APIError)], ["23P01"])
        self.assertIsNotNone(await insert(OTHER, start + timedelta(hours=1)))

    async def test_confirmation_creates_grant_outbox_and_audit_once(self):
        b = await self.booking(confirm=False)
        decisions = await asyncio.gather(*(db.decide_booking(b["id"], "confirmed", ADMIN) for _ in range(5)))
        self.assertEqual(sum(x is not None for x in decisions), 1)
        self.assertEqual(len(await rows("access_grants")), 1)
        self.assertEqual(len(await rows("notification_outbox")), 1)
        self.assertTrue(any(x["actor_id"] == ADMIN and x["action"] == "grant_created" for x in await rows("audit_log")))

    async def test_confirmation_rolls_back_if_outbox_fails(self):
        b = await self.booking(confirm=False)
        sql("CREATE FUNCTION test_fail_outbox() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN "
            "RAISE EXCEPTION 'injected_failure'; END $$; "
            "CREATE TRIGGER test_outbox BEFORE INSERT ON notification_outbox FOR EACH ROW EXECUTE FUNCTION test_fail_outbox();")
        try:
            with self.assertRaises(APIError):
                await db.decide_booking(b["id"], "confirmed", ADMIN)
        finally:
            sql("DROP TRIGGER test_outbox ON notification_outbox; DROP FUNCTION test_fail_outbox();")
        self.assertEqual((await db.booking(b["id"]))["status"], "pending")
        self.assertEqual(await rows("access_grants"), [])
        self.assertEqual(await rows("notification_outbox"), [])

    async def test_access_denied_for_pending_foreign_early_and_expired(self):
        b = await self.booking(confirm=False)
        for user in (USER, OTHER):
            with self.assertRaises(service.ServiceError):
                await locks.request_open(user, b["id"], str(uuid4()))
        await db.decide_booking(b["id"], "confirmed", ADMIN)
        sql("UPDATE access_grants SET starts_at=now()+interval '1 minute';")
        with self.assertRaises(service.ServiceError):
            await self.command(b)
        sql("UPDATE access_grants SET starts_at=now()-interval '2 minutes', ends_at=now()-interval '1 minute';")
        with self.assertRaises(service.ServiceError):
            await self.command(b)
        self.assertEqual(await rows("lock_commands"), [])

    async def test_same_action_is_one_command_under_concurrency(self):
        b = await self.booking()
        key = str(uuid4())
        commands = await asyncio.gather(*(self.command(b, key) for _ in range(16)))
        self.assertEqual(len({c["id"] for c in commands}), 1)
        self.assertEqual(len(await rows("lock_commands")), 1)

    async def test_idempotency_key_cannot_change_booking(self):
        b = await self.booking()
        key = str(uuid4())
        await self.command(b, key)
        g2 = await db.create_garage({"name": "ТЕСТ другой бокс"})
        b2 = await self.booking(garage=g2)
        with self.assertRaises(service.ServiceError) as caught:
            await self.command(b2, key)
        self.assertEqual(caught.exception.code, "idempotency_conflict")

    async def test_cancellation_atomically_revokes_and_cancels_queued(self):
        b = await self.booking()
        action = await locks.action_for_booking(b["id"], USER)
        c = await locks.request_open(USER, b["id"], action["id"], "bot")
        await service.cancel_booking(self.bot, USER, b["id"])
        self.assertEqual((await db.lock_command(c["id"]))["state"], "cancelled")
        self.assertIsNotNone((await rows("access_grants"))[0]["revoked_at"])
        with self.assertRaises(service.ServiceError):
            await locks.request_open(USER, b["id"], str(uuid4()))
        await self.worker.step()
        self.assertEqual(self.adapter.calls, [])
        # Повтор старого действия возвращает отменённую команду, не новое открытие.
        self.assertEqual((await locks.request_open(USER, b["id"], action["id"], "bot"))["id"], c["id"])

    async def test_two_workers_cannot_send_one_command_twice(self):
        c = await self.command()
        other = LockSimulator()
        await asyncio.gather(self.worker.step(), Worker(self.bot, other).step())
        self.assertEqual(self.adapter.calls + other.calls, [c["id"]])
        self.assertEqual((await db.lock_command(c["id"]))["state"], "device_confirmed")

    async def test_expired_queued_command_is_never_sent(self):
        c = await self.command()
        sql("UPDATE lock_commands SET expires_at=now()-interval '1 second';")
        await self.worker.step()
        self.assertEqual(self.adapter.calls, [])
        self.assertEqual((await db.lock_command(c["id"]))["state"], "expired")

    async def test_changed_grant_version_is_rechecked_before_send(self):
        c = await self.command()
        sql("UPDATE access_grants SET version=version+1;")
        await self.worker.step()
        self.assertEqual(self.adapter.calls, [])
        self.assertEqual((await db.lock_command(c["id"]))["state"], "cancelled")

    async def test_cancellation_during_external_request_does_not_resend(self):
        b = await self.booking()
        c = await self.command(b)
        started, release = asyncio.Event(), asyncio.Event()
        class WaitingSimulator(LockSimulator):
            async def send(self, command, device):
                started.set()
                await release.wait()
                return await super().send(command, device)
        adapter = WaitingSimulator()
        sending = asyncio.create_task(Worker(self.bot, adapter).step())
        await asyncio.wait_for(started.wait(), 2)
        try:
            await service.cancel_booking(self.bot, USER, b["id"])
        finally:
            release.set()
        await sending
        self.assertEqual(adapter.calls, [c["id"]])
        self.assertEqual((await db.booking(b["id"]))["status"], "cancelled")
        # Устройство вправе подтвердить уже переданный запрос после отзыва права.
        self.assertEqual((await db.lock_command(c["id"]))["state"], "device_confirmed")
        with self.assertRaises(service.ServiceError):
            await self.command(b)

    async def test_database_failure_keeps_queue_for_next_worker(self):
        c = await self.command()
        with patch.object(db, "claim_lock_command", new_callable=AsyncMock, side_effect=OSError("database_offline")):
            with self.assertRaises(OSError):
                await self.worker.step()
        self.assertEqual(self.adapter.calls, [])
        self.assertEqual((await db.lock_command(c["id"]))["state"], "queued")
        await Worker(self.bot, self.adapter).step()
        self.assertEqual(self.adapter.calls, [c["id"]])

    async def test_ambiguous_timeout_is_not_retried_after_restart(self):
        b = await self.booking()
        c = await self.command(b)
        adapter = LockSimulator("timeout")
        await Worker(self.bot, adapter).step()
        self.assertEqual((await db.lock_command(c["id"]))["state"], "unknown")
        await Worker(self.bot, adapter).step()
        self.assertEqual(adapter.calls, [c["id"]])
        with self.assertRaises(service.ServiceError) as caught:
            await self.command(b)
        self.assertEqual(caught.exception.code, "device_busy")
        self.assertTrue(any(x["payload"]["kind"] == "command_alert" and x["recipient_id"] == ADMIN
            for x in await rows("notification_outbox")))

    async def test_crash_in_dispatching_recovers_as_unknown(self):
        c = await self.command()
        await db.claim_lock_command()
        sql("UPDATE lock_commands SET lease_until=now()-interval '1 second';")
        await Worker(self.bot, self.adapter).step()
        self.assertEqual(self.adapter.calls, [])
        self.assertEqual((await db.lock_command(c["id"]))["state"], "unknown")

    async def test_cloud_ack_without_event_never_claims_physical_success(self):
        c = await self.command()
        adapter = LockSimulator("no_event")
        await Worker(self.bot, adapter).step()
        self.assertEqual((await db.lock_command(c["id"]))["state"], "cloud_accepted")
        sql("UPDATE lock_commands SET lease_until=now()-interval '1 second';")
        await Worker(self.bot, adapter).step()
        self.assertEqual((await db.lock_command(c["id"]))["state"], "unknown")
        self.assertEqual(len(adapter.calls), 1)

    async def test_rejected_and_offline_are_explicit_failures(self):
        c = await self.command()
        adapter = LockSimulator("offline")
        await Worker(self.bot, adapter).step()
        self.assertEqual((await db.lock_command(c["id"]))["state"], "failed")
        self.assertEqual(len(adapter.calls), 1)

    async def test_duplicate_event_is_saved_once(self):
        c = await self.command()
        await Worker(self.bot, LockSimulator("duplicate")).step()
        self.assertEqual(len(await rows("device_events")), 1)
        self.assertEqual((await db.lock_command(c["id"]))["state"], "device_confirmed")

    async def test_old_or_uncorrelated_event_does_not_confirm_command(self):
        c = await self.command()
        await Worker(self.bot, LockSimulator("old_event")).step()
        self.assertEqual((await db.lock_command(c["id"]))["state"], "cloud_accepted")
        fresh = datetime.now(timezone.utc).isoformat()
        await db.record_device_event(p_device_id=self.device["id"], p_event_id="fresh-event",
            p_kind="telemetry", p_occurred_at=fresh, p_data={"battery": 90})
        await db.record_device_event(p_device_id=self.device["id"], p_event_id="old-event",
            p_kind="telemetry", p_occurred_at="2020-01-01T00:00:00Z", p_data={"battery": 1})
        await db.record_device_event(p_device_id=self.device["id"], p_event_id="uncorrelated",
            p_kind="unlocked", p_occurred_at=fresh)
        self.assertEqual((await db.lock_command(c["id"]))["state"], "cloud_accepted")
        self.assertNotEqual((await db.lock_device(self.device["id"]))["telemetry"].get("battery"), 1)

    async def test_wrong_device_correlation_and_future_event_are_rejected(self):
        c = await self.command()
        g2 = await db.create_garage({"name": "ТЕСТ Второй"})
        d2 = await locks.configure_device(ADMIN, g2["id"], "simulator", "second-device", True, "Тест корреляции")
        for device, when in [(d2["id"], datetime.now(timezone.utc)),
                              (self.device["id"], datetime.now(timezone.utc) + timedelta(days=1))]:
            with self.assertRaises(APIError):
                await db.record_device_event(p_device_id=device, p_event_id=str(uuid4()), p_kind="unlocked",
                    p_occurred_at=when.isoformat(), p_command_id=c["id"])
        self.assertEqual(await rows("device_events"), [])

    async def test_operator_requires_explicit_role_reason_and_uses_queue(self):
        for actor, reason in [(USER, "Проверка"), (900000002, "Проверка"), (ADMIN, "")]:
            with self.assertRaises(service.ServiceError):
                await locks.manual_open(actor, self.device["id"], str(uuid4()), reason)
        c = await locks.manual_open(ADMIN, self.device["id"], str(uuid4()), "Помощь клиенту на месте")
        self.assertEqual(c["state"], "queued")
        self.assertTrue(any(x["actor_id"] == ADMIN and x["command_id"] == c["id"] and x["reason"]
            for x in await rows("audit_log")))
        await self.worker.step()
        self.assertEqual(self.adapter.calls, [c["id"]])

    async def test_unknown_requires_audited_resolution_and_allows_reentry(self):
        b = await self.booking()
        c = await self.command(b)
        await Worker(self.bot, LockSimulator("timeout")).step()
        with self.assertRaises(service.ServiceError):
            await locks.resolve_unknown(USER, c["id"], "failed", "Проверил")
        await locks.resolve_unknown(ADMIN, c["id"], "failed", "Проверено на месте, замок не сработал")
        self.assertTrue(any(x["action"] == "unknown_resolved_failed" and x["actor_id"] == ADMIN
            for x in await rows("audit_log")))
        sql("UPDATE lock_commands SET created_at=now()-interval '5 seconds';")
        next_c = await self.command(b)
        self.assertNotEqual(c["id"], next_c["id"])

    async def test_device_cannot_be_reassigned_during_active_grant(self):
        await self.booking()
        with self.assertRaises(service.ServiceError) as caught:
            await locks.configure_device(ADMIN, self.g["id"], "simulator", "foreign-device", True, "Смена замка")
        self.assertEqual(caught.exception.code, "device_in_use")

    async def test_notifications_survive_telegram_failure_without_resending_lock(self):
        c = await self.command()
        await self.worker.step()
        with patch.object(self.bot, "send_message", new_callable=AsyncMock, side_effect=OSError("offline")):
            self.assertFalse(await notifications.deliver_one(self.bot))
        pending = [x for x in await rows("notification_outbox") if not x["sent_at"]]
        self.assertTrue(pending)
        sql("UPDATE notification_outbox SET available_at=now(), lease_until=NULL WHERE sent_at IS NULL;")
        other_worker = Worker(self.bot, self.adapter)
        for _ in range(8):
            await other_worker.step()
        self.assertFalse(any(not x["sent_at"] for x in await rows("notification_outbox")))
        self.assertEqual(self.adapter.calls, [c["id"]])

    async def test_outbox_claim_is_atomic(self):
        await self.booking()
        claimed = await asyncio.gather(*(db.claim_notification() for _ in range(8)))
        self.assertEqual(sum(x is not None for x in claimed), 1)

    async def test_monitoring_emits_alert_and_heartbeat(self):
        await self.command()
        sql("UPDATE lock_commands SET created_at=now()-interval '2 minutes';")
        await db.worker_tick()
        pending = await rows("notification_outbox")
        self.assertTrue(any(x["payload"].get("code") == "queue_delayed" for x in pending))
        self.assertEqual((await rows("service_heartbeats"))[0]["name"], "worker")

    async def test_api_authorization_deduplication_and_readiness(self):
        b = await self.booking()
        key = str(uuid4())
        auth = lambda user: {"Authorization": "dev " + str(user)}
        async with TestClient(TestServer(server.build_app(self.bot))) as client:
            self.assertEqual((await client.get("/api/ready")).status, 200)
            with patch.object(db, "readiness", new_callable=AsyncMock, side_effect=OSError()):
                self.assertEqual((await client.get("/api/ready")).status, 503)
            url = "/api/bookings/%s/unlock" % b["id"]
            self.assertEqual((await client.post(url, json={})).status, 401)
            self.assertEqual((await client.post(url, json={}, headers=auth(OTHER))).status, 400)
            headers = dict(auth(USER), **{"Idempotency-Key": key})
            self.assertEqual((await client.post(url, json={"device_id": self.device["id"]}, headers=headers)).status, 400)
            r = await client.post(url, json={}, headers=headers)
            self.assertEqual(r.status, 202, await r.text())
            command = await r.json()
            r2 = await client.post(url, json={}, headers=headers)
            self.assertEqual((await r2.json())["id"], command["id"])
            self.assertEqual((await client.get("/api/lock-commands/" + command["id"], headers=auth(OTHER))).status, 404)
            self.assertEqual((await client.get("/api/admin/locks", headers=auth(USER))).status, 403)
            self.assertEqual((await client.get("/api/admin/locks", headers=auth(ADMIN))).status, 200)
            self.assertEqual((await client.get("/api/garages")).status, 200)
            mine = await (await client.get("/api/bookings", headers=auth(USER))).json()
            self.assertTrue(mine["bookings"][0]["unlock_action"])

    async def test_real_bot_callbacks_and_persistent_webhook_ledger(self):
        # Перезагружаем Router только для этого теста: aiogram не допускает повторного включения.
        from tgbot import app
        for module in (app.common, app.admin_bookings, app.admin_objects, app.booking, app.locks, app.search, app.owners):
            importlib.reload(module)
        import harness
        harness.VERBOSE = False
        h = harness.Harness()
        b = await self.booking()
        client = h.user(USER)
        try:
            await client.send("Мои заявки")
            await client.click("🟢")
            _, button = client.find("Открыть бокс")
            data = button.callback_data
            handler = WebhookHandler(h.bot, h.dp, "local_secret")
            payload = {"update_id": 700, "callback_query": {"id": "cb1", "chat_instance": "1", "data": data,
                "from": {"id": USER, "first_name": "Test", "is_bot": False},
                "message": {"message_id": 30, "date": 1700000000, "chat": {"id": USER, "type": "private"}, "text": "Бронь"}}}
            app_http = server.build_app(h.bot)
            app_http.router.add_post(WEBHOOK_PATH, handler.handle)
            app_http.on_shutdown.append(handler.shutdown)
            async with TestClient(TestServer(app_http)) as http:
                headers = {"X-Telegram-Bot-Api-Secret-Token": "local_secret"}
                self.assertEqual((await http.post(WEBHOOK_PATH, json=payload, headers=headers)).status, 200)
                payload["update_id"] += 1  # новый Update, тот же логический клик
                self.assertEqual((await http.post(WEBHOOK_PATH, json=payload, headers=headers)).status, 200)
            new_handler = WebhookHandler(h.bot, h.dp, "local_secret")
            self.assertTrue(await db.telegram_update_processed(h.bot.id, 701))
            new_http = server.build_app(h.bot)
            new_http.router.add_post(WEBHOOK_PATH, new_handler.handle)
            new_http.on_shutdown.append(new_handler.shutdown)
            with patch.object(h.dp, "feed_update", new_callable=AsyncMock, wraps=h.dp.feed_update) as feed:
                async with TestClient(TestServer(new_http)) as http:
                    self.assertEqual((await http.post(WEBHOOK_PATH, json=payload, headers=headers)).status, 200)
                feed.assert_not_awaited()
            self.assertEqual(len(await rows("lock_commands")), 1)
            await Worker(h.bot, self.adapter).step()
            self.assertEqual(len(self.adapter.calls), 1)
            await new_handler.shutdown(None)
        finally:
            await h.dp.emit_shutdown(bot=h.bot)
            await h.bot.session.close()

    async def test_production_cannot_enable_simulator_or_local_database(self):
        env = dict(os.environ, APP_ENV="production")
        proc = subprocess.run([sys.executable, "-c", "import core.config"], cwd=ROOT, env=env,
            capture_output=True, text=True)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("разрешены только", proc.stderr)

    async def test_anonymous_postgrest_cannot_call_privileged_functions(self):
        async with httpx.AsyncClient() as client:
            r = await client.post(config.POSTGREST_URL + "/rpc/gh_claim_command", json={})
        self.assertIn(r.status_code, (401, 403, 404))

    async def test_restore_gate_blocks_all_restored_dispatch(self):
        c = await self.command()
        await db.rpc("gh_restore_gate", p_actor_id=ADMIN, p_reason="Восстановление резервной копии")
        await self.worker.step()
        self.assertEqual(self.adapter.calls, [])
        self.assertEqual((await db.lock_command(c["id"]))["state"], "unknown")

    async def test_migrations_are_repeatable(self):
        await asyncio.to_thread(migrate)
        self.assertTrue(await db.readiness())

    async def test_real_backup_restore_into_separate_database(self):
        c = await self.command()
        await asyncio.to_thread(restore_check)
        # Проверка восстановления не изменила исходную базу.
        self.assertEqual((await db.lock_command(c["id"]))["state"], "queued")


if __name__ == "__main__":
    unittest.main(verbosity=2)
