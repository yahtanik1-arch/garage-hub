"""python -m vkbot: independent VK Callback API server; no Telegram webhook/polling."""
import asyncio
import logging

from aiohttp import web
from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.enums import ParseMode

from core import config, db
from tgbot.app import build_dispatcher
from tgbot import reminders
from vkbot.client import VKClient
from vkbot.transport import VKSession
from vkbot.webhook import VKWebhook, WEBHOOK_PATH, validate_config


async def propagate_error(event):
    # The durable VK inbox retries failures; the Telegram error handler deliberately swallows them.
    raise event.exception


def build_app():
    validate_config(config.VK_GROUP_ID, config.VK_TOKEN, config.VK_CALLBACK_SECRET, config.VK_CONFIRMATION_CODE)
    telegram = Bot(config.TOKEN, session=AiohttpSession(proxy=config.TELEGRAM_PROXY_URL or None),
                   default=DefaultBotProperties(parse_mode=ParseMode.HTML)) if config.TOKEN else None
    client = VKClient(config.VK_TOKEN, telegram)
    # Syntactic adapter token only; never sent to Telegram. Existing handlers use the VKSession transport.
    bot = Bot("%s:VKTRANSPORTONLYTOKENNOTATELEGRAMSECRET" % config.VK_GROUP_ID,
              session=VKSession(client, config.VK_GROUP_ID),
              default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dispatcher = build_dispatcher(error_handler=propagate_error)
    handler = VKWebhook(bot, dispatcher, config.VK_GROUP_ID, config.VK_CALLBACK_SECRET, config.VK_CONFIRMATION_CODE)
    app = web.Application(client_max_size=64 * 1024)
    app.router.add_post(WEBHOOK_PATH, handler.handle)

    async def health(request):
        return web.json_response({"ok": True})

    async def startup(app):
        if not await db.readiness():
            raise RuntimeError("Схема базы не готова")
        await db._run(db.supabase.table("vk_events").select("id").limit(1))
        await db._run(db.supabase.table("bookings").select("client_vk_id,vk_request_id").limit(1))
        await dispatcher.emit_startup(bot=bot)
        app["vk_task"] = asyncio.create_task(handler.run())
        app["reminders_task"] = asyncio.create_task(reminders.run(bot))

    async def cleanup(app):
        handler.stopped.set()
        tasks = [app[name] for name in ("vk_task", "reminders_task") if name in app]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await dispatcher.emit_shutdown(bot=bot)
        await bot.session.close()

    app.router.add_get("/health", health)
    app.on_startup.append(startup)
    app.on_cleanup.append(cleanup)
    return app


def main():
    logging.basicConfig(level=logging.INFO)
    for name in ("httpx", "httpcore", "aiogram.event"):
        logging.getLogger(name).setLevel(logging.WARNING)
    web.run_app(build_app(), host="127.0.0.1", port=config.VK_PORT, access_log=None)


if __name__ == "__main__":
    main()
