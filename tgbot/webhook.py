"""Webhook Telegram: проверка секрета и защита от повторной доставки.

HTTP 200 отправляется после обработчика и записи update_id в Supabase.
До этого Telegram может повторить запрос; одновременные копии ждут одну задачу.
Как и MemoryStorage бота, рассчитано на один процесс приёма событий.
"""
import asyncio
import hmac
import re
from typing import Dict
from urllib.parse import urlsplit

from aiogram import Bot, Dispatcher
from aiogram.methods import TelegramMethod
from aiogram.types import Update
from aiohttp import web

from core import db

WEBHOOK_PATH = "/telegram/webhook"


def validate_config(url: str, secret: str) -> None:
    if not url:
        if secret:
            raise ValueError("TELEGRAM_WEBHOOK_SECRET задан без TELEGRAM_WEBHOOK_URL")
        return
    parsed = urlsplit(url)
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
            or parsed.path != WEBHOOK_PATH or parsed.query or parsed.fragment):
        raise ValueError("TELEGRAM_WEBHOOK_URL должен иметь вид https://домен/telegram/webhook")
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,256}", secret):
        raise ValueError("Для webhook нужен TELEGRAM_WEBHOOK_SECRET: 1–256 символов A-Z, a-z, 0-9, _ или -")


class WebhookHandler:
    def __init__(self, bot: Bot, dispatcher: Dispatcher, secret: str):
        if not secret:
            raise ValueError("Webhook без секретного токена запрещён")
        self.bot = bot
        self.dispatcher = dispatcher
        self.secret = secret.encode()
        self.inflight: Dict[int, asyncio.Task] = {}

    async def _process(self, update: Update) -> None:
        if await db.telegram_update_processed(self.bot.id, update.update_id):
            return
        result = await self.dispatcher.feed_update(self.bot, update)
        if isinstance(result, TelegramMethod):
            await self.bot(result)
        await db.mark_telegram_update_processed(self.bot.id, update.update_id)

    def _finished(self, update_id: int, task: asyncio.Task) -> None:
        self.inflight.pop(update_id, None)
        if not task.cancelled():
            task.exception()  # Получить ошибку, даже если HTTP-клиент уже отключился.

    async def handle(self, request: web.Request) -> web.Response:
        received = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "").encode()
        if not hmac.compare_digest(received, self.secret):
            raise web.HTTPUnauthorized()
        try:
            data = await request.json()
            if not isinstance(data, dict) or type(data.get("update_id")) is not int:
                raise ValueError("update_id должен быть целым числом")
            update = Update.model_validate(data, context={"bot": self.bot})
        except ValueError:
            raise web.HTTPBadRequest(reason="Некорректное событие Telegram")

        task = self.inflight.get(update.update_id)
        if task is None:
            task = asyncio.create_task(self._process(update))
            self.inflight[update.update_id] = task
            task.add_done_callback(lambda done: self._finished(update.update_id, done))
        # Разрыв HTTP-соединения не прерывает действие между обработкой и записью update_id.
        await asyncio.shield(task)
        return web.json_response({})

    async def shutdown(self, app: web.Application) -> None:
        if self.inflight:
            await asyncio.gather(*list(self.inflight.values()), return_exceptions=True)
