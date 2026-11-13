"""Telegram transport local-only: запись в лог, без сетевых запросов."""
from datetime import datetime, timezone
import itertools
import logging

from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.base import BaseSession
from aiogram.enums import ParseMode
from aiogram.types import Chat, Message, User

from core.config import APP_ENV

log = logging.getLogger(__name__)


class LocalSession(BaseSession):
    def __init__(self):
        super().__init__()
        self.ids = itertools.count(1)

    async def close(self):
        pass

    async def stream_content(self, *args, **kwargs):
        raise RuntimeError("Local Telegram не скачивает файлы из сети")
        yield b""  # async generator contract

    async def make_request(self, bot, method, timeout=None):
        log.info("local_telegram method=%s chat=%s", type(method).__name__, getattr(method, "chat_id", None))
        if type(method).__name__ == "GetMe":
            return User(id=bot.id, is_bot=True, first_name="Local GarageHub", username="garagehub_local_bot")
        if type(method).__name__ in ("SendMessage", "EditMessageText"):
            return Message(message_id=getattr(method, "message_id", None) or next(self.ids),
                date=datetime.now(timezone.utc), chat=Chat(id=int(method.chat_id), type="private"),
                text=method.text).as_(bot)
        return True


class LocalBot(Bot):
    def __init__(self, token):
        if APP_ENV != "local":
            raise RuntimeError("LocalBot запрещён в production")
        super().__init__(token, session=LocalSession(), default=DefaultBotProperties(parse_mode=ParseMode.HTML))
