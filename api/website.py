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
from core import db, service
from core.utils import now, parse_dt


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
    check_origin(request)
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
    phone = russian_phone(data.get("phone"))
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
            "web_client_name": name, "web_client_phone": phone,
            "web_request_id": str(key), "web_client_hash": client_hash(request),
        })
    return ok(booking_result(b, g), 201)


def check_origin(request):
    origin = request.headers.get("Origin")
    try:
        same_origin = not origin or (urlsplit(origin).scheme in ("http", "https") and urlsplit(origin).netloc == request.host)
    except ValueError:
        same_origin = False
    if not same_origin:
        raise service.ServiceError("forbidden", "Отправьте заявку с сайта GarageHub.", 403)


def booking_result(b, g):
    result = serializers.booking_json(b, g)
    result["can_cancel"] = False
    if b.get("client_telegram_id") is None and b.get("web_booking_token"):
        result["access_token"] = b["web_booking_token"]
        result["is_past"] = parse_dt(b["end_time"]) <= now()
        demo = bool(b.get("demo_payment_token") or (g and g.get("demo_payment")))
        result["is_demo"] = demo
        if b["status"] == "confirmed" and not result["is_past"]:
            result["arrival"] = {
                "instructions": ("Это демонстрационная бронь. Физического бокса нет: приезжать и платить не нужно."
                    if demo else (g or {}).get("arrival_instructions") or "Инструкция по входу пока не добавлена."),
                "can_show_route": bool(not demo and (g or {}).get("address")),
            }
        result["support"] = {"message": "Поддержка скоро появится"}
    if b.get("demo_payment_token"):
        # This capability is returned only to its creator or the holder of this random token.
        result["payment"] = {"mode": "demo", "token": b["demo_payment_token"],
            "status": b["demo_payment_status"], "expires_at": b["demo_payment_expires_at"]}
    return result


async def demo_payment(request):
    check_origin(request)
    try:
        header = request.headers.get("Authorization", "")
        if not header.startswith("Demo "):
            raise ValueError()
        token = str(UUID(header[5:]))
    except ValueError:
        raise service.ServiceError("not_found", "Оплата не найдена.", 404)
    action = "status"
    if request.method == "POST":
        data = await body(request)
        action = data.get("action")
        if action not in ("pay", "cancel"):
            raise service.ServiceError("bad_action", "Неизвестное действие.")
    rows = await db.rpc("gh_demo_payment", p_token=token, p_action=action)
    if not rows:
        raise service.ServiceError("not_found", "Оплата не найдена.", 404)
    b = rows[0]
    return ok(booking_result(b, await db.garage(b["garage_id"])))


def russian_phone(value):
    message = "Введите российский номер: +7 и ровно 10 цифр."
    if not isinstance(value, str) or not 10 <= len(value.strip()) <= 24:
        raise service.ServiceError("bad_phone", message)
    raw = value.strip()
    if not re.fullmatch(r"\+?[0-9\s()\-]+", raw):
        raise service.ServiceError("bad_phone", message)
    digits = re.sub(r"\D", "", raw)
    if raw.startswith("+") and not (len(digits) == 11 and digits.startswith("7")):
        raise service.ServiceError("bad_phone", message)
    if len(digits) == 11 and digits[0] in "78":
        digits = digits[1:]
    if len(digits) != 10:
        raise service.ServiceError("bad_phone", message)
    return "+7" + digits


async def my_bookings(request):
    check_origin(request)
    if request.content_type != "application/json":
        raise service.ServiceError("bad_json", "Нужен JSON-запрос.", 415)
    data = await body(request)
    tokens = data.get("tokens")
    if not isinstance(tokens, list) or len(tokens) > 100:
        raise service.ServiceError("bad_tokens", "Не удалось прочитать список бронирований.")
    try:
        if any(not isinstance(token, str) for token in tokens):
            raise ValueError()
        tokens = list({str(UUID(token)) for token in tokens})
    except (TypeError, ValueError, AttributeError):
        raise service.ServiceError("bad_tokens", "Некорректная ссылка на бронь.")
    rows = await db.website_bookings(tokens)
    garages = await db.garages_by_ids(b["garage_id"] for b in rows)
    return ok({"bookings": [booking_result(b, garages.get(b["garage_id"])) for b in rows]})
