"""Фото объектов. В базе хранятся file_id Telegram или local:имя.jpg; приложение получает их по /api/photo/<file_id>.

Сервер один раз скачивает файл у Telegram, кладёт в cache/photos и дальше отдаёт сам.
Отдаём только те file_id, которые есть у объектов каталога: прокси не открыт для чужих файлов бота.
"""
import asyncio
import hashlib
import logging
import re
import time
from typing import Dict, Optional, Set

from aiohttp import web

from core import db
from core.config import ASSETS_DIR, CACHE_DIR

log = logging.getLogger(__name__)

PHOTO_DIR = CACHE_DIR / "photos"
KNOWN_TTL_SEC = 60

_known: Set[str] = set()
_known_at: Optional[float] = None   # None = ещё не загружали (monotonic на маке стартует почти с нуля)
_locks: Dict[str, asyncio.Lock] = {}


async def is_known(file_id: str) -> bool:
    global _known, _known_at
    age = None if _known_at is None else time.monotonic() - _known_at
    if age is None or age > KNOWN_TTL_SEC or file_id not in _known:
        # Если id нет в кеше, перечитываем базу: объект могли только что добавить (не чаще раза в 5 с).
        if age is None or age > 5:
            rows = await db.garages(active=None)
            _known = {p for g in rows for p in (g.get("photos") or [])}
            _known_at = time.monotonic()
    return file_id in _known


def cache_path(file_id: str):
    return PHOTO_DIR / (hashlib.sha1(file_id.encode()).hexdigest() + ".jpg")


async def photo(request: web.Request) -> web.StreamResponse:
    file_id = request.match_info["file_id"]
    if len(file_id) > 300 or not await is_known(file_id):
        raise web.HTTPNotFound()
    if file_id.startswith("local:"):
        name = file_id[6:]
        if not re.fullmatch(r"[A-Za-z0-9_-]+\.(?:jpg|png|webp)", name):
            raise web.HTTPNotFound()
        path = ASSETS_DIR / "garages" / name
        if not path.is_file():
            raise web.HTTPNotFound()
        return web.FileResponse(path, headers={"Cache-Control": "public, max-age=604800, immutable"})
    path = cache_path(file_id)
    if not path.exists() and request.app["bot"] is None:
        # uncached Telegram file_id must not make the standalone site wait on Telegram.
        return web.FileResponse(ASSETS_DIR / "placeholder.png",
                                headers={"Cache-Control": "public, max-age=60"})
    if not path.exists():
        lock = _locks.setdefault(file_id, asyncio.Lock())
        async with lock:
            if not path.exists():
                PHOTO_DIR.mkdir(parents=True, exist_ok=True)
                bot = request.app["bot"]
                try:
                    tg_file = await bot.get_file(file_id)
                    part = path.with_suffix(".part")
                    await bot.download_file(tg_file.file_path, destination=part)
                    part.replace(path)
                except Exception:
                    log.exception("Не удалось скачать фото %s…", file_id[:12])
                    raise web.HTTPBadGateway()
    return web.FileResponse(
        path, headers={"Cache-Control": "public, max-age=604800, immutable"},
    )
