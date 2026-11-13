"""Полный локальный HTTP + настоящие aiogram handlers + worker, без Telegram/Tuya."""
import asyncio
import logging
import secrets

from aiohttp import web

from api.server import build_app
from core.config import APP_ENV, TOKEN, WEB_HOST, WEB_PORT
from core.lock_simulator import LockSimulator
from core.worker import Worker
from tgbot.app import build_dispatcher, wait_for_shutdown
from tgbot.webhook import WEBHOOK_PATH, WebhookHandler
from tools.local_bot import LocalBot


async def main():
    if APP_ENV != "local" or WEB_HOST not in ("127.0.0.1", "localhost", "::1"):
        raise RuntimeError("Локальный стенд слушает только loopback")
    bot = LocalBot(TOKEN)
    dp = build_dispatcher()
    app = build_app(bot)
    # Произвольные тестовые Update принимаются только локально с локальным секретом.
    secret = secrets.token_urlsafe(32)
    from core.config import CACHE_DIR
    CACHE_DIR.mkdir(exist_ok=True)
    secretfile = CACHE_DIR / "local-webhook-secret"
    secretfile.write_text(secret)
    secretfile.chmod(0o600)
    handler = WebhookHandler(bot, dp, secret)
    app.router.add_post(WEBHOOK_PATH, handler.handle)
    app.on_shutdown.append(handler.shutdown)
    runner = web.AppRunner(app)
    stopped = asyncio.Event()
    worker = None
    try:
        await runner.setup()
        await web.TCPSite(runner, WEB_HOST, WEB_PORT).start()
        worker = asyncio.create_task(Worker(bot, LockSimulator()).run(stopped))
        print("GarageHub локально: http://127.0.0.1:%s/?dev=client" % WEB_PORT, flush=True)
        print("Администратор: http://127.0.0.1:%s/?dev=admin" % WEB_PORT, flush=True)
        await wait_for_shutdown()
    finally:
        stopped.set()
        if worker:
            await worker
        await runner.cleanup()
        await dp.emit_shutdown(bot=bot)
        await bot.session.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
