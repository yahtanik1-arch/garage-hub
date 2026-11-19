"""Заявка с сайта: имя и телефон, без аккаунта Telegram и без выдачи прав на замок."""
import hashlib
import ipaddress
import re
from datetime import date
from urllib.parse import urlsplit
from uuid import UUID

from aiohttp import web

from api import serializers
from api.routes import body, int_param, ok, text_field
from core import service


def client_hash(request):
    peer = request.remote or "unknown"
    # Only our loopback nginx may supply X-Real-IP; nginx must overwrite this header.
    if peer in ("127.0.0.1", "::1") and request.headers.get("X-Real-IP"):
        try:
            peer = str(ipaddress.ip_address(request.headers["X-Real-IP"]))
        except ValueError:
            raise service.ServiceError("bad_request", "Некорректный запрос.")
    return hashlib.sha256(peer.encode()).hexdigest()


async def create_booking(request):
    if request.content_type != "application/json":
        raise service.ServiceError("bad_json", "Нужен JSON-запрос.", 415)
    origin = request.headers.get("Origin")
    try:
        same_origin = not origin or (urlsplit(origin).scheme in ("http", "https") and urlsplit(origin).netloc == request.host)
    except ValueError:
        same_origin = False
    if not same_origin:
        raise service.ServiceError("forbidden", "Отправьте заявку с сайта GarageHub.", 403)
    try:
        key = UUID(request.headers.get("Idempotency-Key", ""))
        if key.version != 4:
            raise ValueError()
    except ValueError:
        raise service.ServiceError("bad_key", "Обновите страницу и отправьте заявку снова.")
    data = await body(request)
    if data.get("company"):
        raise service.ServiceError("bad_request", "Не удалось отправить заявку.")
    name = text_field(data, "name", "Имя", 2, 80)
    raw_phone = text_field(data, "phone", "Телефон", 10, 30)
    if not re.fullmatch(r"[+\d\s()\-]+", raw_phone):
        raise service.ServiceError("bad_phone", "Укажите телефон с кодом страны.")
    digits = re.sub(r"\D", "", raw_phone)
    if len(digits) == 11 and digits.startswith("8"):
        digits = "7" + digits[1:]
    if not re.fullmatch(r"[1-9]\d{9,14}", digits):
        raise service.ServiceError("bad_phone", "Укажите телефон с кодом страны.")
    if data.get("consent") is not True:
        raise service.ServiceError("consent_required", "Нужно согласие на обработку контактов для заявки.")
    try:
        day = date.fromisoformat(str(data.get("date")))
    except ValueError:
        raise service.ServiceError("bad_date", "Некорректная дата.")
    hours = data.get("hours")
    if not isinstance(hours, list) or not 1 <= len(hours) <= 24 or any(type(h) is not int for h in hours):
        raise service.ServiceError("bad_hours", "Выберите часы подряд.")
    b, g = await service.create_booking_request(None, None, None,
        int_param(data.get("garage_id"), "garage_id"), day, hours, guest={
            "web_client_name": name, "web_client_phone": "+" + digits,
            "web_request_id": str(key), "web_client_hash": client_hash(request),
        })
    result = serializers.booking_json(b, g)
    result["can_cancel"] = False  # No account or phone verification; changes go through the operator.
    return ok(result, 201)
