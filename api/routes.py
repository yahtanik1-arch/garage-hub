"""HTTP API для Mini App. Бизнес-правила живут в service.py, здесь только приём и выдача JSON."""
import asyncio
import json
import logging
import re
import time
from datetime import date, timedelta
from typing import Any, Dict

from aiohttp import web

from core import db
from core import service, locks
from tgbot import texts as t
from api import serializers as ser
from api.auth import User, authenticate
from core.utils import at_hour, now, parse_dt

log = logging.getLogger(__name__)

PUBLIC_PREFIXES = ("/api/health", "/api/ready", "/api/photo/")
# Каталог открыт без входа — для будущего сайта и поисковиков. Только чтение и только публичные поля
# (serializers.garage_public без контактов владельца). Брони, «мои заявки» и админка — только со входом.
PUBLIC_CATALOG = re.compile(r"^/api/garages(/\d+(/availability)?)?$")


def is_public(request: web.Request) -> bool:
    path = request.path
    return path.startswith(PUBLIC_PREFIXES) or (request.method == "GET" and bool(PUBLIC_CATALOG.match(path)))


def dumps(data) -> str:
    return json.dumps(data, ensure_ascii=False, separators=(",", ":"))


_owner_throttle: Dict[int, float] = {}


# ─── Промежуточные обработчики ──────────────────────────────

def error_response(code: str, message: str, status: int) -> web.Response:
    return web.json_response({"error": {"code": code, "message": message}}, status=status, dumps=dumps)


@web.middleware
async def errors_middleware(request: web.Request, handler):
    try:
        return await handler(request)
    except service.ServiceError as e:
        return error_response(e.code, e.message, e.status)
    except web.HTTPException as e:
        if request.path.startswith("/api/"):
            return error_response("http_%s" % e.status, e.reason or "Ошибка", e.status)
        raise
    except Exception:
        log.exception("Ошибка API: %s %s", request.method, request.path)
        return error_response("server", "Что-то пошло не так. Попробуйте ещё раз.", 500)


@web.middleware
async def auth_middleware(request: web.Request, handler):
    path = request.path
    if path.startswith("/api/") and not is_public(request):
        user = authenticate(request)
        if not user:
            header = request.headers.get("Authorization", "")
            why = "нет данных Telegram" if not header else "подпись Telegram не прошла проверку" if header.startswith("tma ") else "чужой формат входа"
            log.warning("вход не удался: %s (%s %s)", why, request.method, path)
            return error_response("unauthorized", "Откройте приложение через Telegram.", 401)
        request["user"] = user
        if path.startswith("/api/admin/") and not user.is_admin:
            return error_response("forbidden", "Раздел только для администратора.", 403)
    return await handler(request)


def _device(request: web.Request) -> str:
    ua = request.headers.get("User-Agent", "")
    for marker, name in (("iPhone", "iPhone"), ("iPad", "iPad"), ("Android", "Android"),
                         ("Macintosh", "Mac"), ("Windows", "Windows"), ("Linux", "Linux")):
        if marker in ua:
            return name
    return "неизвестно"


@web.middleware
async def headers_middleware(request: web.Request, handler):
    try:
        response = await handler(request)
    except web.HTTPException as e:
        if request.path == "/" or request.path.startswith("/api/") and not request.path.startswith("/api/photo/"):
            log.info("запрос %s %s → %s (%s)", request.method, request.path, e.status, _device(request))
        raise
    # Журнал открытий: страница и API (без фото и статики) — чтобы видеть, доходят ли люди до приложения
    if request.path == "/" or (request.path.startswith("/api/") and not request.path.startswith("/api/photo/")):
        log.info("запрос %s %s → %s (%s)", request.method, request.path, response.status, _device(request))
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    if request.path.startswith("/api/") and not request.path.startswith("/api/photo/"):
        response.headers["Cache-Control"] = "no-store"
    elif not request.path.startswith("/api/"):
        response.headers.setdefault("Cache-Control", "no-cache")
        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self' https://telegram.org; "
            "style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; "
            "connect-src 'self'; base-uri 'none'; form-action 'none'",
        )
    return response


# ─── Помощники ──────────────────────────────────────────────

def ok(data: Any, status: int = 200) -> web.Response:
    return web.json_response(data, status=status, dumps=dumps)


def user_of(request: web.Request) -> User:
    return request["user"]


async def body(request: web.Request) -> dict:
    try:
        data = await request.json()
    except Exception:
        raise service.ServiceError("bad_json", "Некорректный запрос.")
    if not isinstance(data, dict):
        raise service.ServiceError("bad_json", "Некорректный запрос.")
    return data


def int_param(value, name: str) -> int:
    try:
        if isinstance(value, bool):
            raise ValueError()
        return int(value)
    except (TypeError, ValueError):
        raise service.ServiceError("bad_param", f"Некорректное поле «{name}».")


def text_field(data: dict, key: str, label: str, min_len: int, max_len: int) -> str:
    value = data.get(key)
    value = value.strip() if isinstance(value, str) else ""
    if not min_len <= len(value) <= max_len:
        raise service.ServiceError("bad_field", f"Проверьте поле «{label}».")
    return value


async def active_garage(garage_id) -> dict:
    g = await db.garage(int_param(garage_id, "id"))
    if not g or not g.get("is_active", True):
        raise service.ServiceError("not_found", "Площадка не найдена.", 404)
    return g


# ─── Общие ──────────────────────────────────────────────────

async def health(request: web.Request) -> web.Response:
    return ok({"ok": True})


async def ready(request: web.Request) -> web.Response:
    try:
        healthy = await asyncio.wait_for(db.readiness(), timeout=3)
    except Exception:
        healthy = False
    return ok({"ready": healthy}, 200 if healthy else 503)


async def me(request: web.Request) -> web.Response:
    u = user_of(request)
    return ok({"id": u.id, "name": u.name, "username": u.username, "is_admin": u.is_admin, "dev": u.dev,
        "can_operate_locks": u.is_admin and u.id in locks.MANUAL_UNLOCK_ADMIN_IDS})


# ─── Каталог ────────────────────────────────────────────────

async def garages_list(request: web.Request) -> web.Response:
    rows = await db.garages(active=True)
    today = await service.today_status(rows)
    return ok({"garages": [ser.garage_public(g, today.get(g["id"])) for g in rows]})


async def garage_detail(request: web.Request) -> web.Response:
    g = await active_garage(request.match_info["id"])
    return ok(ser.garage_public(g))


async def garage_availability(request: web.Request) -> web.Response:
    g = await active_garage(request.match_info["id"])
    blocked = await service.availability(g)
    pub = ser.garage_public(g)
    return ok({
        "open_hour": pub["open_hour"],
        "close_hour": pub["close_hour"],
        "price": pub["price"],
        "days": [
            {"date": day.isoformat(), "blocked": sorted(hours),
             "free": (pub["close_hour"] - pub["open_hour"]) - len(hours)}
            for day, hours in blocked.items()
        ],
    })


# ─── Брони клиента ──────────────────────────────────────────

async def bookings_create(request: web.Request) -> web.Response:
    u, data = user_of(request), await body(request)
    try:
        day = date.fromisoformat(str(data.get("date")))
    except ValueError:
        raise service.ServiceError("bad_date", "Некорректная дата.")
    hours = data.get("hours")
    if not isinstance(hours, list) or len(hours) > 24 or not all(isinstance(h, int) for h in hours):
        raise service.ServiceError("bad_hours", "Некорректные часы.")
    created, g = await service.create_booking_request(
        request.app["bot"], u.id, u.username, int_param(data.get("garage_id"), "garage_id"), day, hours,
    )
    return ok(ser.booking_json(created, g), 201)


async def bookings_mine(request: web.Request) -> web.Response:
    rows = await db.client_bookings(user_of(request).id)
    garages = await db.garages_by_ids(b["garage_id"] for b in rows)
    result = []
    for b in rows:
        item = ser.booking_json(b, garages.get(b["garage_id"]))
        action = await locks.action_for_booking(b["id"], user_of(request).id) if b["status"] == "confirmed" else None
        item["unlock_action"] = action["id"] if action else None
        result.append(item)
    return ok({"bookings": result})


async def bookings_unlock(request: web.Request) -> web.Response:
    if await body(request):
        raise service.ServiceError("bad_body", "Запрос открытия не принимает дополнительных полей.")
    c = await locks.request_open(user_of(request).id, int_param(request.match_info["id"], "id"),
        request.headers.get("Idempotency-Key"), "miniapp")
    return ok(locks.command_json(c), 202)


async def lock_command(request: web.Request) -> web.Response:
    u = user_of(request)
    return ok(locks.command_json(await locks.command_for_user(request.match_info["id"], u.id, u.is_admin)))


async def bookings_cancel(request: web.Request) -> web.Response:
    booking_id = int_param(request.match_info["id"], "id")
    updated = await service.cancel_booking(request.app["bot"], user_of(request).id, booking_id)
    return ok(ser.booking_json(updated, await db.garage(updated["garage_id"])))


# ─── Заявка владельца ───────────────────────────────────────

async def owner_request(request: web.Request) -> web.Response:
    u, data = user_of(request), await body(request)
    if time.monotonic() - _owner_throttle.get(u.id, -999) < 30:
        raise service.ServiceError("too_fast", "Заявка уже отправлена. Подождите немного.", 429)
    name = text_field(data, "name", "Имя", 2, 80)
    phone = text_field(data, "phone", "Телефон", 10, 30)
    if sum(ch.isdigit() for ch in phone) < 10:
        raise service.ServiceError("bad_phone", "В номере не хватает цифр.")
    kind = data.get("object_type")
    if kind not in ("garage", "detailing"):
        raise service.ServiceError("bad_type", "Выберите тип объекта.")
    address = text_field(data, "address", "Адрес", 5, 200)
    _owner_throttle[u.id] = time.monotonic()
    await service.create_owner_request(request.app["bot"], u.id, u.username, name, phone, kind, address)
    return ok({"ok": True}, 201)


# ─── Админ ──────────────────────────────────────────────────

async def _garages_for(rows):
    return await db.garages_by_ids(b["garage_id"] for b in rows)


async def admin_summary(request: web.Request) -> web.Response:
    today = now().date()
    active, archived = await db.garages(active=True), await db.garages(active=False)
    pending = await db.pending_bookings()
    todays = await db.bookings_between(at_hour(today, 0), at_hour(today, 24))
    try:
        leads = await db.owner_requests("new")
    except Exception:
        leads = []
    return ok({
        "active": len(active), "archived": len(archived), "pending": len(pending),
        "today": len(todays), "leads": len(leads),
    })


async def admin_inbox(request: web.Request) -> web.Response:
    rows = await db.pending_bookings()
    garages = await _garages_for(rows)
    return ok({"bookings": [ser.booking_admin(b, garages.get(b["garage_id"])) for b in rows]})


async def admin_day(request: web.Request) -> web.Response:
    offset = max(0, min(6, int_param(request.query.get("offset", 0), "offset")))
    day = now().date() + timedelta(days=offset)
    rows = await db.bookings_between(at_hour(day, 0), at_hour(day, 24))
    garages = await _garages_for(rows)
    confirmed = sum(float(b["total_price"] or 0) for b in rows if b["status"] == "confirmed")
    return ok({
        "date": day.isoformat(), "offset": offset, "confirmed_total": confirmed,
        "bookings": [ser.booking_admin(b, garages.get(b["garage_id"])) for b in rows],
    })


async def admin_confirm(request: web.Request) -> web.Response:
    booking_id = int_param(request.match_info["id"], "id")
    result = await service.confirm_booking(request.app["bot"], booking_id, user_of(request).id)
    if not result:
        return await _already_decided(booking_id)
    b, g, delivered = result
    return ok({"booking": ser.booking_admin(b, g), "delivered": delivered})


async def admin_reject(request: web.Request) -> web.Response:
    booking_id = int_param(request.match_info["id"], "id")
    reason = text_field(await body(request), "reason", "Причина", 2, 300)
    result = await service.reject_booking(request.app["bot"], booking_id, reason, user_of(request).id)
    if not result:
        return await _already_decided(booking_id)
    b, g, delivered = result
    return ok({"booking": ser.booking_admin(b, g), "delivered": delivered})


async def _already_decided(booking_id: int) -> web.Response:
    b = await db.booking(booking_id)
    status = t.STATUS.get(b["status"], ("", b["status"]))[1] if b else "не найдена"
    return error_response("decided", f"Эта заявка уже обработана: {status}.", 409)


async def admin_garages(request: web.Request) -> web.Response:
    rows = await db.garages(active=None)
    counts = await asyncio.gather(*(db.upcoming_for_garage(g["id"], now()) for g in rows))
    return ok({"garages": [ser.garage_admin(g, len(c)) for g, c in zip(rows, counts)]})


async def _set_active(request: web.Request, active: bool) -> web.Response:
    garage_id = int_param(request.match_info["id"], "id")
    if not await db.garage(garage_id):
        raise service.ServiceError("not_found", "Объект не найден.", 404)
    updated = await db.update_garage(garage_id, {"is_active": active})
    upcoming = await db.upcoming_for_garage(garage_id, now())
    return ok(ser.garage_admin(updated or await db.garage(garage_id), len(upcoming)))


async def admin_archive(request: web.Request) -> web.Response:
    return await _set_active(request, False)


async def admin_restore(request: web.Request) -> web.Response:
    return await _set_active(request, True)


async def admin_leads(request: web.Request) -> web.Response:
    try:
        rows = await db.owner_requests("new")
    except Exception:
        rows = []
    return ok({"leads": [ser.lead_json(r) for r in rows]})


async def admin_lead_done(request: web.Request) -> web.Response:
    await db.close_owner_request(int_param(request.match_info["id"], "id"))
    return ok({"ok": True})


async def admin_locks(request: web.Request) -> web.Response:
    return ok({"locks": await locks.devices_overview(), "queue": await db.lock_overview(),
        "can_operate": user_of(request).id in locks.MANUAL_UNLOCK_ADMIN_IDS})


async def admin_lock_configure(request: web.Request) -> web.Response:
    data = await body(request)
    d = await locks.configure_device(user_of(request).id, int_param(data.get("garage_id"), "garage_id"),
        data.get("provider"), data.get("provider_device_id"), data.get("enabled"), data.get("reason"))
    return ok({"lock": d})


async def admin_lock_unlock(request: web.Request) -> web.Response:
    data = await body(request)
    if set(data) != {"reason"}:
        raise service.ServiceError("bad_body", "Укажите только причину открытия.")
    c = await locks.manual_open(user_of(request).id, request.match_info["id"],
        request.headers.get("Idempotency-Key"), data.get("reason"))
    return ok(locks.command_json(c), 202)


async def admin_lock_resolve(request: web.Request) -> web.Response:
    data = await body(request)
    c = await locks.resolve_unknown(user_of(request).id, request.match_info["id"], data.get("state"), data.get("reason"))
    return ok(locks.command_json(c))


def add_routes(app: web.Application) -> None:
    r = app.router
    r.add_get("/api/health", health)
    r.add_get("/api/ready", ready)
    r.add_get("/api/me", me)
    r.add_get("/api/garages", garages_list)
    r.add_get("/api/garages/{id}", garage_detail)
    r.add_get("/api/garages/{id}/availability", garage_availability)
    r.add_post("/api/bookings", bookings_create)
    r.add_get("/api/bookings", bookings_mine)
    r.add_post("/api/bookings/{id}/cancel", bookings_cancel)
    r.add_post("/api/bookings/{id}/unlock", bookings_unlock)
    r.add_get("/api/lock-commands/{id}", lock_command)
    r.add_post("/api/owner-requests", owner_request)
    r.add_get("/api/admin/summary", admin_summary)
    r.add_get("/api/admin/inbox", admin_inbox)
    r.add_get("/api/admin/day", admin_day)
    r.add_post("/api/admin/bookings/{id}/confirm", admin_confirm)
    r.add_post("/api/admin/bookings/{id}/reject", admin_reject)
    r.add_get("/api/admin/garages", admin_garages)
    r.add_post("/api/admin/garages/{id}/archive", admin_archive)
    r.add_post("/api/admin/garages/{id}/restore", admin_restore)
    r.add_get("/api/admin/leads", admin_leads)
    r.add_post("/api/admin/leads/{id}/done", admin_lead_done)
    r.add_get("/api/admin/locks", admin_locks)
    r.add_post("/api/admin/locks", admin_lock_configure)
    r.add_post("/api/admin/locks/{id}/unlock", admin_lock_unlock)
    r.add_post("/api/admin/lock-commands/{id}/resolve", admin_lock_resolve)
