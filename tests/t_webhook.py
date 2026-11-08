"""Webhook и запуск: без .env, реального Telegram и внешней базы.

    python3 tests/t_webhook.py
"""
import asyncio
import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.update(
    TELEGRAM_BOT_TOKEN="123456:TESTTOKENTESTTOKENTESTTOKENTESTTOKEN",
    SUPABASE_URL="https://example.supabase.co", SUPABASE_SECRET_KEY="test-key",
    POSTGREST_URL="", TELEGRAM_PROXY_URL="",
    TELEGRAM_WEBHOOK_URL="", TELEGRAM_WEBHOOK_SECRET="", MINIAPP_DEV="",
)

from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage, SimpleEventIsolation
from aiogram.types import Update
from aiohttp.test_utils import TestClient, TestServer

from api import server
from core import db
from tgbot import app
from tgbot.webhook import WEBHOOK_PATH, WebhookHandler, validate_config

URL = "https://example.com" + WEBHOOK_PATH
SECRET = "test_webhook_secret"
HEADERS = {"X-Telegram-Bot-Api-Secret-Token": SECRET}


def message(update_id=1):
    return {"update_id": update_id, "message": {
        "message_id": update_id, "date": 1700000000,
        "chat": {"id": 42, "type": "private"},
        "from": {"id": 42, "is_bot": False, "first_name": "Test"}, "text": "test",
    }}


class ConfigurationTests(unittest.TestCase):
    def test_valid_modes(self):
        validate_config("", "")
        validate_config(URL, SECRET)

    def test_incomplete_or_unsafe_configuration(self):
        for url, secret in [
            ("", SECRET), (URL, ""), (URL, "bad secret"), (URL, "x" * 257),
            (URL, "секрет"), (URL.replace("https:", "http:"), SECRET),
            ("https://example.com/other", SECRET), (URL + "?token=test", SECRET),
            (URL + "#fragment", SECRET), ("https://user:password@example.com" + WEBHOOK_PATH, SECRET),
        ]:
            with self.subTest(url=url, secret=secret), self.assertRaises(ValueError):
                validate_config(url, secret)


class WebhookTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.bot = Bot(os.environ["TELEGRAM_BOT_TOKEN"])
        self.dp = Dispatcher(storage=MemoryStorage(), events_isolation=SimpleEventIsolation())
        self.calls = []
        self.seen = set()
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.release.set()
        self.fail = False

        async def on_message(msg):
            self.calls.append(msg.message_id)
            self.started.set()
            await self.release.wait()
            if self.fail:
                raise RuntimeError("handler failed")
        self.dp.message.register(on_message)

        async def processed(bot_id, update_id):
            return (bot_id, update_id) in self.seen

        async def mark(bot_id, update_id):
            self.seen.add((bot_id, update_id))

        self.patches = [
            patch.object(db, "telegram_update_processed", side_effect=processed),
            patch.object(db, "mark_telegram_update_processed", side_effect=mark),
            patch.object(server, "TELEGRAM_WEBHOOK_URL", URL),
            patch.object(server, "TELEGRAM_WEBHOOK_SECRET", SECRET),
        ]
        for p in self.patches:
            p.start()
        self.client = TestClient(TestServer(server.build_app(self.bot, self.dp)))
        await self.client.start_server()

    async def asyncTearDown(self):
        self.release.set()
        await self.client.close()
        await self.dp.emit_shutdown(bot=self.bot)
        await self.bot.session.close()
        for p in reversed(self.patches):
            p.stop()

    async def post(self, payload=None, headers=None):
        return await self.client.post(WEBHOOK_PATH, json=payload or message(),
                                      headers=HEADERS if headers is None else headers)

    async def test_secret_required_before_processing(self):
        for headers in ({}, {"X-Telegram-Bot-Api-Secret-Token": "wrong"},
                        {"X-Telegram-Bot-Api-Secret-Token": "секрет"}):
            r = await self.post(headers=headers)
            self.assertEqual(r.status, 401)
        self.assertEqual(self.calls, [])
        db.telegram_update_processed.assert_not_awaited()

    async def test_invalid_payloads(self):
        for payload in ([], {}, {"update_id": "1"}, {"update_id": True},
                        {"update_id": 1, "message": {}}):
            r = await self.client.post(WEBHOOK_PATH, json=payload, headers=HEADERS)
            self.assertEqual(r.status, 400, payload)
        r = await self.client.post(WEBHOOK_PATH, data="not json", headers=HEADERS)
        self.assertEqual(r.status, 400)
        self.assertEqual(self.calls, [])

    async def test_oversized_body(self):
        r = await self.client.post(WEBHOOK_PATH, data="x" * 70000, headers=HEADERS)
        self.assertEqual(r.status, 413)

    async def test_message_and_sequential_duplicate(self):
        self.assertEqual((await self.post()).status, 200)
        self.assertIn((self.bot.id, 1), self.seen)
        self.assertEqual((await self.post()).status, 200)
        self.assertEqual(self.calls, [1])

    async def test_simultaneous_duplicates_wait_for_processing(self):
        self.release.clear()
        first = asyncio.create_task(self.post())
        await asyncio.wait_for(self.started.wait(), 2)
        second = asyncio.create_task(self.post())
        await asyncio.sleep(0.05)
        self.assertFalse(first.done())
        self.assertFalse(second.done())
        self.assertEqual(self.calls, [1])
        self.release.set()
        responses = await asyncio.gather(first, second)
        self.assertEqual([r.status for r in responses], [200, 200])
        db.mark_telegram_update_processed.assert_awaited_once_with(self.bot.id, 1)

    async def test_duplicate_after_handler_restart(self):
        self.assertEqual((await self.post()).status, 200)
        handler = WebhookHandler(self.bot, self.dp, SECRET)
        await handler._process(Update.model_validate(message(), context={"bot": self.bot}))
        self.assertEqual(self.calls, [1])

    async def test_failure_is_retryable(self):
        self.fail = True
        with self.assertLogs("api.routes", level="ERROR"):
            r = await self.post()
        self.assertEqual(r.status, 500)
        self.assertEqual(self.seen, set())
        self.fail = False
        self.assertEqual((await self.post()).status, 200)
        self.assertEqual(self.calls, [1, 1])

    async def test_database_failure_does_not_acknowledge_update(self):
        with patch.object(db, "telegram_update_processed", side_effect=RuntimeError("database offline")):
            with self.assertLogs("api.routes", level="ERROR"):
                r = await self.post()
        self.assertEqual(r.status, 500)
        self.assertEqual(self.calls, [])
        self.assertEqual((await self.post()).status, 200)

    async def test_recording_failure_does_not_acknowledge_update(self):
        with patch.object(db, "mark_telegram_update_processed", side_effect=RuntimeError("write failed")):
            with self.assertLogs("api.routes", level="ERROR"):
                r = await self.post()
        self.assertEqual(r.status, 500)
        self.assertEqual(self.seen, set())

    async def test_disconnected_request_does_not_cancel_processing(self):
        handler = WebhookHandler(self.bot, self.dp, SECRET)
        request = SimpleNamespace(headers=HEADERS, json=AsyncMock(return_value=message()))
        self.release.clear()
        receiving = asyncio.create_task(handler.handle(request))
        await asyncio.wait_for(self.started.wait(), 2)
        receiving.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await receiving
        self.assertEqual(self.seen, set())
        self.release.set()
        await handler.shutdown(None)
        self.assertIn((self.bot.id, 1), self.seen)
        self.assertEqual((await handler.handle(request)).status, 200)
        self.assertEqual(self.calls, [1])

    async def test_returned_telegram_method_is_sent_before_acknowledgement(self):
        from aiogram.methods import SendMessage
        result = SendMessage(chat_id=42, text="Reply")
        with patch.object(self.dp, "feed_update", new_callable=AsyncMock, return_value=result), \
                patch.object(self.bot.session, "make_request", new_callable=AsyncMock, return_value=True) as send:
            self.assertEqual((await self.post()).status, 200)
            send.assert_awaited_once()
            self.assertEqual(send.call_args.args[1], result)
        self.assertIn((self.bot.id, 1), self.seen)

    async def test_callback_uses_the_same_dispatcher(self):
        callbacks = []
        self.dp.callback_query.register(lambda cb: callbacks.append(cb.data))
        payload = {"update_id": 2, "callback_query": {
            "id": "callback-2", "from": {"id": 42, "is_bot": False, "first_name": "Test"},
            "chat_instance": "test", "data": "booking:confirm:7",
        }}
        self.assertEqual((await self.post(payload)).status, 200)
        self.assertEqual(callbacks, ["booking:confirm:7"])

    async def test_api_auth_is_unchanged(self):
        self.assertEqual((await self.client.get("/api/health")).status, 200)
        self.assertEqual((await self.client.get("/api/me")).status, 401)
        self.assertEqual((await self.client.get("/api/admin/summary")).status, 401)
        self.assertEqual((await self.client.get("/")).status, 200)

    async def test_dev_server_has_no_webhook(self):
        async with TestClient(TestServer(server.build_app(self.bot))) as client:
            r = await client.post(WEBHOOK_PATH, json=message(), headers=HEADERS)
            self.assertIn(r.status, (404, 405))
        self.assertEqual(self.calls, [])


class RealHandlerTests(unittest.IsolatedAsyncioTestCase):
    async def test_existing_commands_and_owner_form_through_webhook(self):
        # Настоящие обработчики и FSM, подменены только внешняя база и Telegram.
        import harness
        harness.VERBOSE = False
        from tgbot.callbacks import OwnerCb
        from tgbot.keyboards import Btn
        h = harness.Harness()
        seen = set()

        async def mark(bot_id, update_id):
            seen.add((bot_id, update_id))

        async def processed(bot_id, update_id):
            return (bot_id, update_id) in seen

        with patch.object(server, "TELEGRAM_WEBHOOK_URL", URL), \
                patch.object(server, "TELEGRAM_WEBHOOK_SECRET", SECRET), \
                patch.object(db, "telegram_update_processed", side_effect=processed), \
                patch.object(db, "mark_telegram_update_processed", side_effect=mark), \
                patch.object(db, "create_owner_request", new_callable=AsyncMock) as save:
            save.return_value = {"id": 7, "name": "Test Owner"}
            try:
                async with TestClient(TestServer(server.build_app(h.bot, h.dp))) as client:
                    async def send(update_id, text):
                        payload = message(update_id)
                        payload["message"]["text"] = text
                        if text.startswith("/"):
                            payload["message"]["entities"] = [{"type": "bot_command", "offset": 0, "length": len(text)}]
                        r = await client.post(WEBHOOK_PATH, json=payload, headers=HEADERS)
                        self.assertEqual(r.status, 200)
                        return payload

                    await send(1, "/id")
                    self.assertTrue(any("42" in (entry[2] or "") for entry in h.session.log))
                    await send(2, Btn.LIST)
                    await send(3, "Test Owner")
                    await send(4, "+7 900 111-22-33")
                    callback = {"update_id": 5, "callback_query": {
                        "id": "owner-kind", "from": {"id": 42, "is_bot": False, "first_name": "Test"},
                        "chat_instance": "test", "message": message(5)["message"],
                        "data": OwnerCb(value="garage").pack(),
                    }}
                    self.assertEqual((await client.post(WEBHOOK_PATH, json=callback, headers=HEADERS)).status, 200)
                    last = await send(6, "Москва, ул. Тестовая, 1")
                    save.assert_awaited_once()
                    self.assertEqual(save.call_args.args[0]["telegram_id"], 42)
                    self.assertEqual(save.call_args.args[0]["phone"], "+7 900 111-22-33")
                    self.assertEqual((await client.post(WEBHOOK_PATH, json=last, headers=HEADERS)).status, 200)
                    save.assert_awaited_once()
            finally:
                await h.dp.emit_shutdown(bot=h.bot)
                await h.bot.session.close()


class StartupTests(unittest.IsolatedAsyncioTestCase):
    async def run_mode(self, url, secret, bind_error=False, proxy=""):
        self.bot = SimpleNamespace(
            get_me=AsyncMock(return_value=SimpleNamespace(username="testbot")),
            set_webhook=AsyncMock(), delete_webhook=AsyncMock(),
            session=SimpleNamespace(close=AsyncMock()),
        )
        self.dp = MagicMock()
        self.dp.resolve_used_update_types.return_value = ["message", "callback_query"]
        for name in ("emit_startup", "emit_shutdown", "start_polling"):
            setattr(self.dp, name, AsyncMock())
        self.runner = SimpleNamespace(cleanup=AsyncMock())
        with patch.multiple(app, TELEGRAM_WEBHOOK_URL=url, TELEGRAM_WEBHOOK_SECRET=secret,
                            TELEGRAM_PROXY_URL=proxy), \
                patch.object(app, "setup_logging"), patch.object(app, "check_schema", return_value=True), \
                patch.object(app, "Bot", return_value=self.bot) as make_bot, \
                patch.object(app, "build_dispatcher", return_value=self.dp), \
                patch.object(app, "setup_commands", new_callable=AsyncMock), \
                patch.object(app, "setup_profile", new_callable=AsyncMock), \
                patch.object(app, "start_web", new_callable=AsyncMock) as start_web, \
                patch.object(app.reminders, "run", new_callable=AsyncMock), \
                patch.object(app, "sync_menu_button", new_callable=AsyncMock), \
                patch.object(app, "wait_for_shutdown", new_callable=AsyncMock):
            if bind_error:
                start_web.side_effect = OSError("port in use")
            else:
                start_web.return_value = self.runner
            await app.main()
            self.assertEqual(make_bot.call_args.kwargs["session"].proxy, proxy or None)

    async def test_webhook_uses_proxy_session(self):
        await self.run_mode(URL, SECRET, proxy="socks5://127.0.0.1:1080")

    async def test_worker_uses_proxy_session(self):
        from core import worker
        bot = SimpleNamespace(session=SimpleNamespace(close=AsyncMock()))
        with patch.multiple(worker, APP_ENV="production", LOCK_PROVIDER="disabled",
                            TELEGRAM_PROXY_URL="socks5://127.0.0.1:1080"), \
                patch.object(worker, "Bot", return_value=bot) as make_bot, \
                patch.object(worker.db, "readiness", new_callable=AsyncMock, return_value=True), \
                patch.object(worker.Worker, "run", new_callable=AsyncMock):
            await worker.main()
        self.assertEqual(make_bot.call_args.kwargs["session"].proxy, "socks5://127.0.0.1:1080")
        bot.session.close.assert_awaited_once()

    async def test_webhook_registration_and_shutdown(self):
        await self.run_mode(URL, SECRET)
        self.bot.set_webhook.assert_awaited_once_with(
            URL, secret_token=SECRET, allowed_updates=["message", "callback_query"],
            drop_pending_updates=False,
        )
        self.dp.start_polling.assert_not_awaited()
        self.dp.emit_startup.assert_awaited_once()
        self.dp.emit_shutdown.assert_awaited_once()
        self.bot.delete_webhook.assert_not_awaited()
        self.runner.cleanup.assert_awaited_once()
        self.bot.session.close.assert_awaited_once()

    async def test_polling_removes_webhook_without_dropping_events(self):
        await self.run_mode("", "")
        self.bot.delete_webhook.assert_awaited_once_with(drop_pending_updates=False)
        self.dp.start_polling.assert_awaited_once()
        self.bot.set_webhook.assert_not_awaited()

    async def test_bind_failure_is_fatal_for_webhook(self):
        with self.assertRaises(OSError):
            await self.run_mode(URL, SECRET, bind_error=True)
        self.bot.set_webhook.assert_not_awaited()
        self.dp.start_polling.assert_not_awaited()
        self.bot.session.close.assert_awaited_once()

    async def test_polling_still_works_without_web_server(self):
        await self.run_mode("", "", bind_error=True)
        self.dp.start_polling.assert_awaited_once()


if __name__ == "__main__":
    unittest.main(verbosity=2)
