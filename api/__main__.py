"""Production HTTP API + сайт: python -m api. Без токена и соединений с Telegram."""
import logging

from aiohttp import web

from api.server import build_app
from core import db
from core.config import WEB_HOST, WEB_PORT


async def check_database(app):
    if not await db.readiness():
        raise RuntimeError("Примените миграции sql/000–004 перед запуском сайта")


def main():
    logging.basicConfig(level=logging.INFO)
    app = build_app(website=True)
    app.on_startup.append(check_database)
    web.run_app(app, host=WEB_HOST, port=WEB_PORT, access_log=None)


if __name__ == "__main__":
    main()
