"""Проверить TLS-соединение с Telegram и, если задан токен, getMe. Без отправки сообщений."""
import asyncio
import os
from pathlib import Path

from aiogram import Bot
from aiogram.client.session.aiohttp import AiohttpSession
from aiohttp import ClientTimeout
from dotenv import load_dotenv


async def main():
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
    proxy = os.getenv("TELEGRAM_PROXY_URL", "").strip()
    session = None
    try:
        session = AiohttpSession(proxy=proxy or None, timeout=12)
        client = await session.create_session()
        async with client.get("https://api.telegram.org/", allow_redirects=False,
                              timeout=ClientTimeout(total=12)) as response:
            if response.status not in (200, 302):
                raise RuntimeError("Unexpected Telegram HTTP status")
            print("Telegram HTTPS OK; mode=" + ("proxy" if proxy else "direct"))
        token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
        if token:
            me = await Bot(token, session=session).get_me()
            print("Bot getMe OK: @" + (me.username or str(me.id)))
        else:
            print("Bot getMe skipped: TELEGRAM_BOT_TOKEN is empty")
    except Exception as error:
        # URL прокси может содержать пароль, исключение Telegram — токен.
        print("Telegram check FAILED: " + type(error).__name__)
        return 1
    finally:
        if session is not None:
            await session.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
