"""Фирменные картинки. После первой загрузки Telegram отдаёт file_id — дальше шлём его,
чтобы не загружать файл заново."""
import re
from typing import Dict, Optional, Union

from aiogram.types import FSInputFile, Message

from core.config import ASSETS_DIR

_file_ids: Dict[str, str] = {}


def image(name: str) -> Union[str, FSInputFile]:
    return _file_ids.get(name) or FSInputFile(ASSETS_DIR / f"{name}.png")


def remember(name: str, message: Optional[Message]) -> None:
    if name not in _file_ids and message is not None and message.photo:
        _file_ids[name] = message.photo[-1].file_id


def photo(source: str) -> Union[str, FSInputFile]:
    if not source.startswith("local:"):
        return source
    name = source[6:]
    if re.fullmatch(r"[A-Za-z0-9_-]+\.(?:jpg|png|webp)", name):
        path = ASSETS_DIR / "garages" / name
        if path.is_file():
            return FSInputFile(path)
    return image("placeholder")


def garage_photo(g: dict) -> Union[str, FSInputFile]:
    photos = g.get("photos") or []
    return photo(photos[0]) if photos else image("placeholder")


def uses_placeholder(g: dict) -> bool:
    return not (g.get("photos") or [])
