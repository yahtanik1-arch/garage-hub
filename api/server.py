"""API и статика. python -m api работает независимо от Telegram."""
import logging
from typing import Optional

from aiogram import Bot, Dispatcher
from aiohttp import web

from api import photos, routes
from core.config import (
    BASE_DIR, TELEGRAM_WEBHOOK_SECRET, TELEGRAM_WEBHOOK_URL, WEB_HOST, WEB_PORT, WEBAPP_DIR,
)
from tgbot.webhook import WEBHOOK_PATH, WebhookHandler, validate_config

log = logging.getLogger(__name__)


async def index(request: web.Request) -> web.StreamResponse:
    return web.FileResponse(WEBAPP_DIR / "index.html")


def build_app(bot: Optional[Bot] = None, dispatcher: Optional[Dispatcher] = None,
              website: bool = False) -> web.Application:
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
    if website:
        async def site_index(request):
            return web.FileResponse(BASE_DIR / "site" / "index.html")
        app.router.add_get("/", site_index)
    else:
        app.router.add_get("/", index)
    app.router.add_static("/", BASE_DIR / "site" if website else WEBAPP_DIR, show_index=False)
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
