"""Бизнес-логика, общая для бота и Mini App.

Бот и API вызывают одни и те же функции, поэтому правила (занятость слотов,
повторное нажатие кнопок, уведомления) не могут разойтись между каналами.
"""
import logging
from datetime import date, timedelta
from typing import Dict, List, Optional, Tuple

from aiogram import Bot
from postgrest.exceptions import APIError

from core import db
from tgbot import keyboards as kb
from tgbot import texts as t
from core.config import ADMIN_IDS, BOOKING_WINDOW_DAYS
from core.utils import (
    at_hour, busy_hours, garage_hours, now, parse_dt, past_hours, user_lock,
)

log = logging.getLogger(__name__)

MAX_ACTIVE_REQUESTS = 5


class ServiceError(Exception):
    """Ошибка с понятным пользователю текстом."""

    def __init__(self, code: str, message: str, status: int = 400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


# ─── Уведомления админам ────────────────────────────────────

async def notify_admins(bot: Bot, text: str, markup=None, **kwargs) -> int:
    """Отправляет сообщение всем админам. Возвращает, скольким дошло (один недоступный не мешает остальным)."""
    delivered = 0
    for admin_id in ADMIN_IDS:
        try:
            await bot.send_message(admin_id, text, reply_markup=markup, **kwargs)
            delivered += 1
        except Exception as e:
            log.warning("Админу %s сообщение не дошло: %s", admin_id, e)
    return delivered


# ─── Свободное время ────────────────────────────────────────

def window_days() -> List[date]:
    today = now().date()
    return [today + timedelta(days=i) for i in range(BOOKING_WINDOW_DAYS)]


async def blocked_hours(g: dict, day: date) -> set:
    open_h, close_h = garage_hours(g)
    bookings = await db.busy_bookings(g["id"], at_hour(day, open_h), at_hour(day, close_h))
    return busy_hours(bookings, day, open_h, close_h) | past_hours(day, open_h, close_h)


async def availability(g: dict) -> Dict[date, set]:
    """Занятые и прошедшие часы на каждый из ближайших дней — одним запросом к базе."""
    days = window_days()
    open_h, close_h = garage_hours(g)
    bookings = await db.busy_bookings(g["id"], at_hour(days[0], 0), at_hour(days[-1], 24))
    return {
        day: busy_hours(bookings, day, open_h, close_h) | past_hours(day, open_h, close_h)
        for day in days
    }


def free_by_day(g: dict, blocked: Dict[date, set]) -> Dict[date, int]:
    open_h, close_h = garage_hours(g)
    return {day: (close_h - open_h) - len(hours) for day, hours in blocked.items()}


async def today_status(garages: List[dict]) -> Dict[int, dict]:
    """Для каталога: сколько часов свободно сегодня и с какого времени."""
    today = now().date()
    rows = await db.busy_for_garages((g["id"] for g in garages), at_hour(today, 0), at_hour(today, 24))
    by_garage: Dict[int, List[dict]] = {}
    for b in rows:
        by_garage.setdefault(b["garage_id"], []).append(b)
    result = {}
    for g in garages:
        open_h, close_h = garage_hours(g)
        blocked = busy_hours(by_garage.get(g["id"], []), today, open_h, close_h) | past_hours(today, open_h, close_h)
        free = [h for h in range(open_h, close_h) if h not in blocked]
        result[g["id"]] = {"free_today": len(free), "next_free": free[0] if free else None}
    return result


# ─── Создание заявки ────────────────────────────────────────

def check_hours(g: dict, hours: List[int]) -> List[int]:
    hours = sorted(set(int(h) for h in hours))
    if not hours:
        raise ServiceError("no_hours", "Выберите хотя бы один час.")
    if any(b - a != 1 for a, b in zip(hours, hours[1:])):
        raise ServiceError("gap", "Выбирайте часы подряд, без пропусков.")
    open_h, close_h = garage_hours(g)
    if hours[0] < open_h or hours[-1] >= close_h:
        raise ServiceError("outside", "Это время вне часов работы площадки.")
    return hours


async def create_booking_request(bot: Bot, user_id: int, username: Optional[str],
                                 garage_id, day: date, hours: List[int]) -> Tuple[dict, dict]:
    """Создаёт заявку pending и отправляет её админу. Возвращает (заявка, объект)."""
    g = await db.garage(garage_id)
    if not g or not g.get("is_active", True):
        raise ServiceError("unavailable", "Эта площадка сейчас недоступна.", 404)
    if day not in window_days():
        raise ServiceError("bad_date", "Эта дата недоступна для брони.")
    hours = check_hours(g, hours)

    async with user_lock(user_id):
        active = [b for b in await db.client_bookings(user_id)
                  if b["status"] == "pending" and parse_dt(b["end_time"]) > now()]
        if len(active) >= MAX_ACTIVE_REQUESTS:
            raise ServiceError(
                "too_many",
                "У вас уже несколько заявок на согласовании. Дождитесь ответа или отмените лишние.",
            )
        blocked = await blocked_hours(g, day)
        if any(h in blocked for h in hours):
            raise ServiceError("taken", t.SLOT_TAKEN, 409)
        start, end = at_hour(day, hours[0]), at_hour(day, hours[-1] + 1)
        total = len(hours) * float(g.get("price_per_hour") or 0)
        try:
            created = await db.create_booking({
                "garage_id": g["id"],
                "client_telegram_id": user_id,
                "client_username": username,
                "start_time": start.isoformat(),
                "end_time": end.isoformat(),
                "total_price": total,
                "status": "pending",
            })
        except APIError as error:
            if error.code == "23P01":  # exclusion constraint: гонка двух разных клиентов
                raise ServiceError("taken", t.SLOT_TAKEN, 409) from None
            raise
    if not created:
        raise ServiceError("save_failed", "Не удалось отправить заявку. Попробуйте ещё раз.", 500)

    if not await notify_admins(
        bot, t.admin_booking_card(created, g, start, end, None, now(), title="Новая заявка"), kb.decide(created["id"]),
    ):
        log.error("Заявка №%s создана, но не дошла ни до одного админа", created["id"])
    return created, g


# ─── Решения по заявке ──────────────────────────────────────

async def confirm_booking(bot: Bot, booking_id: int, actor_id: int):
    """pending → confirmed. None, если заявку уже обработали. Иначе (заявка, объект, доставлено ли клиенту)."""
    if actor_id not in ADMIN_IDS:
        raise ServiceError("forbidden", "Раздел только для администратора.", 403)
    b = await db.decide_booking(booking_id, "confirmed", actor_id)
    if not b:
        return None
    g = await db.garage(b["garage_id"]) or {}
    from core.notifications import try_booking_notification
    delivered = await try_booking_notification(bot, booking_id)
    return b, g, delivered


async def reject_booking(bot: Bot, booking_id: int, reason: str, actor_id: int):
    """pending → rejected с причиной. None, если заявку уже обработали."""
    if actor_id not in ADMIN_IDS:
        raise ServiceError("forbidden", "Раздел только для администратора.", 403)
    b = await db.decide_booking(booking_id, "rejected", actor_id, reason)
    if not b:
        return None
    g = await db.garage(b["garage_id"]) or {}
    from core.notifications import try_booking_notification
    delivered = await try_booking_notification(bot, booking_id)
    return b, g, delivered


def can_cancel(b: dict) -> bool:
    return b["status"] in db.ACTIVE_STATUSES and parse_dt(b["end_time"]) > now()


async def cancel_booking(bot: Bot, user_id: int, booking_id: int) -> dict:
    """Клиент отменяет свою заявку; админу уходит уведомление."""
    b = await db.booking(booking_id)
    if not b or b["client_telegram_id"] != user_id:
        raise ServiceError("not_found", "Заявка не найдена.", 404)
    if not can_cancel(b):
        raise ServiceError("closed", "Эту заявку уже нельзя отменить.", 409)
    updated = await db.decide_booking(booking_id, "cancelled", user_id)
    if not updated:
        raise ServiceError("changed", "Статус заявки уже изменился.", 409)
    from core.notifications import try_booking_notification
    for _ in ADMIN_IDS:
        await try_booking_notification(bot, booking_id)
    return updated


# ─── Заявки владельцев ──────────────────────────────────────

async def create_owner_request(bot: Bot, user_id: int, username: Optional[str], name: str,
                               phone: str, object_type: str, address: str) -> None:
    lead = {
        "name": name,
        "phone": phone,
        "object_type": t.object_type(object_type),
        "address": address,
        "telegram_id": user_id,
        "telegram_username": t.username_label(username, user_id),
    }
    saved = None
    try:
        saved = await db.create_owner_request(lead)
    except Exception:
        log.exception("Не удалось сохранить заявку владельца")
    await notify_admins(bot, t.owner_lead(saved or {**lead, "id": "—"}), kb.lead_actions(saved["id"]) if saved else None)
