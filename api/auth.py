"""Проверка, что запрос пришёл из Telegram Mini App от настоящего пользователя.

Telegram передаёт приложению строку initData, подписанную токеном бота.
Мы пересчитываем подпись: если она совпала, данные не подделаны.
https://core.telegram.org/bots/webapps#validating-data-received-via-the-mini-app
"""
import hashlib
import hmac
import json
import time
from typing import Optional
from urllib.parse import parse_qsl

from aiohttp import web

from core.config import ADMIN_ID, ADMIN_IDS, INIT_DATA_MAX_AGE_SEC, MINIAPP_DEV, TOKEN

LOCAL_HOSTS = {"localhost", "127.0.0.1", "[::1]"}
PROXY_HEADERS = ("X-Forwarded-For", "X-Forwarded-Host", "Forwarded", "Cf-Connecting-Ip", "X-Real-Ip")


class User:
    def __init__(self, user_id: int, first_name: str = "", last_name: str = "",
                 username: Optional[str] = None, dev: bool = False):
        self.id = user_id
        self.first_name = first_name
        self.last_name = last_name
        self.username = username
        self.dev = dev

    @property
    def is_admin(self) -> bool:
        return self.id in ADMIN_IDS

    @property
    def name(self) -> str:
        return " ".join(p for p in (self.first_name, self.last_name) if p) or self.username or str(self.id)


def validate_init_data(init_data: str) -> Optional[User]:
    if not TOKEN:
        return None
    try:
        pairs = dict(parse_qsl(init_data, keep_blank_values=True))
    except ValueError:
        return None
    received = pairs.pop("hash", None)
    if not received:
        return None
    check = "\n".join(f"{k}={v}" for k, v in sorted(pairs.items()))
    secret = hmac.new(b"WebAppData", TOKEN.encode(), hashlib.sha256).digest()
    expected = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, received):
        return None
    try:
        if time.time() - int(pairs.get("auth_date", "0")) > INIT_DATA_MAX_AGE_SEC:
            return None
        data = json.loads(pairs["user"])
        return User(int(data["id"]), data.get("first_name", ""), data.get("last_name", ""),
                    data.get("username"))
    except (KeyError, ValueError, TypeError):
        return None


def _is_local_request(request: web.Request) -> bool:
    """Запрос идёт напрямую с этого компьютера, а не через туннель или прокси."""
    if any(h in request.headers for h in PROXY_HEADERS):
        return False
    host = request.host.rsplit(":", 1)[0] if not request.host.startswith("[") else "[::1]"
    return request.remote in ("127.0.0.1", "::1") and host in LOCAL_HOSTS


def dev_user(header: str, request: web.Request) -> Optional[User]:
    """«dev <id>» — вход без Telegram для разработки. Работает только при MINIAPP_DEV=1 и только локально."""
    if not MINIAPP_DEV or not _is_local_request(request):
        return None
    alias = header.split(None, 1)[1].strip() if " " in header else ""
    user_id = {"admin": ADMIN_ID, "client": 900000001}.get(alias)
    if user_id is None:
        try:
            user_id = int(alias)
        except ValueError:
            return None
    return User(user_id, "Эльман" if user_id == ADMIN_ID else "Тест", "", "dev_user", dev=True)


def authenticate(request: web.Request) -> Optional[User]:
    header = request.headers.get("Authorization", "")
    if header.startswith("tma "):
        return validate_init_data(header[4:])
    if header.startswith("dev "):
        return dev_user(header, request)
    return None
