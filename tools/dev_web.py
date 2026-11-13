"""Запускает ТОЛЬКО веб-часть Mini App (без бота), чтобы смотреть и править интерфейс.
Из корня проекта: WEB_PORT=8081 python3 -m tools.dev_web
Вход без Telegram: http://localhost:8081/?dev=client  или  ?dev=admin
Сообщения в Telegram не отправляются (QuietBot).
"""
import asyncio
import logging
import os

os.environ.setdefault("MINIAPP_DEV", "1")
os.environ.setdefault("APP_ENV", "local")

from aiogram import Bot

from api.server import start_web
from core.config import TOKEN

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(name)s: %(message)s")
log = logging.getLogger("dev_web")


class QuietBot(Bot):
    """Бот, который НЕ отправляет сообщения (только пишет в лог) — чтобы тесты не слали уведомления в настоящий Telegram.
    Скачивание фото работает как обычно."""

    async def send_message(self, chat_id, text, **kwargs):
        log.info("[не отправлено] → %s: %s", chat_id, str(text).replace("\n", " | ")[:140])


async def main():
    bot = QuietBot(TOKEN)
    runner = await start_web(bot)
    try:
        await asyncio.Event().wait()
    finally:
        await runner.cleanup()
        await bot.session.close()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
