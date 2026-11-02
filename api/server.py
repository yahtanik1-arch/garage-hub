"""Веб-сервер: API + статические файлы приложения. Работает в одном процессе с ботом."""
import logging
from typing import Optional

from aiogram import Bot, Dispatcher
from aiohttp import web

from api import photos, routes
from core.config import (
    TELEGRAM_WEBHOOK_SECRET, TELEGRAM_WEBHOOK_URL, WEB_HOST, WEB_PORT, WEBAPP_DIR,
)
from tgbot.webhook import WEBHOOK_PATH, WebhookHandler, validate_config

log = logging.getLogger(__name__)


async def index(request: web.Request) -> web.StreamResponse:
    return web.FileResponse(WEBAPP_DIR / "index.html")


def build_app(bot: Bot, dispatcher: Optional[Dispatcher] = None) -> web.Application:
    app = web.Application(
        middlewares=[routes.headers_middleware, routes.errors_middleware, routes.auth_middleware],
        client_max_size=64 * 1024,
    )
    app["bot"] = bot
    routes.add_routes(app)
    app.router.add_get("/api/photo/{file_id}", photos.photo)
    if dispatcher is not None and TELEGRAM_WEBHOOK_URL:
        validate_config(TELEGRAM_WEBHOOK_URL, TELEGRAM_WEBHOOK_SECRET)
        webhook = WebhookHandler(bot, dispatcher, TELEGRAM_WEBHOOK_SECRET)
        app.router.add_post(WEBHOOK_PATH, webhook.handle)
        app.on_shutdown.append(webhook.shutdown)
    app.router.add_get("/", index)
    app.router.add_static("/", WEBAPP_DIR, show_index=False)
    return app


async def start_web(bot: Bot, dispatcher: Optional[Dispatcher] = None) -> web.AppRunner:
    runner = web.AppRunner(build_app(bot, dispatcher), access_log=None)
    try:
        await runner.setup()
        await web.TCPSite(runner, WEB_HOST, WEB_PORT).start()
    except BaseException:
        await runner.cleanup()
        raise
    log.info("Mini App: http://%s:%s", WEB_HOST, WEB_PORT)
    return runner
