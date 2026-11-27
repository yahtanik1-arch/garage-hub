import asyncio
import re
from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Iterable, Optional, Set, Tuple

from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import BaseFilter
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message

from core.config import ADMIN_IDS, DEFAULT_CLOSE_HOUR, DEFAULT_OPEN_HOUR, LOCAL_TZ


# ─── Роли ────────────────────────────────────────────────────

_client_view: Set[int] = set()


def is_admin(user_id: int) -> bool:
    from core.actors import admin_actor_ids
    return user_id in admin_actor_ids()


def admin_view(user_id: int) -> bool:
    """Админ, который не включил режим «как клиент» (/client)."""
    return is_admin(user_id) and user_id not in _client_view


def set_client_view(user_id: int, enabled: bool) -> None:
    if enabled:
        _client_view.add(user_id)
    else:
        _client_view.discard(user_id)


class IsAdmin(BaseFilter):
    async def __call__(self, event) -> bool:
        return bool(event.from_user) and is_admin(event.from_user.id)


# ─── Блокировки ─────────────────────────────────────────────
# Telegram присылает альбом фото пачкой одновременных сообщений, а кнопку
# «Отправить» можно нажать дважды. Блокировка на пользователя выстраивает
# такие события в очередь, чтобы они не затирали данные друг друга.

_locks = defaultdict(asyncio.Lock)


def user_lock(user_id: int) -> asyncio.Lock:
    return _locks[user_id]


# ─── Время ──────────────────────────────────────────────────

def now() -> datetime:
    return datetime.now(LOCAL_TZ)


_FRACTION = re.compile(r"\.(\d+)")
_SHORT_OFFSET = re.compile(r"([+-]\d{2})$")


def parse_dt(value: str) -> datetime:
    """Время из базы → Москва. Python 3.9 понимает дробные секунды только из 3 или 6 цифр,
    а Postgres отбрасывает нули на конце («.17434»), поэтому дополняем до 6."""
    value = value.strip().replace("Z", "+00:00").replace(" ", "T", 1)
    value = _FRACTION.sub(lambda m: "." + m.group(1)[:6].ljust(6, "0"), value, count=1)
    value = _SHORT_OFFSET.sub(r"\1:00", value)
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=LOCAL_TZ)
    return dt.astimezone(LOCAL_TZ)


def at_hour(day: date, hour: int) -> datetime:
    """Начало часа hour в день day по Москве; hour=24 — полночь следующего дня."""
    return datetime(day.year, day.month, day.day, tzinfo=LOCAL_TZ) + timedelta(hours=hour)


# ─── Часы работы ────────────────────────────────────────────

def parse_working_hours(text: str) -> Optional[Tuple[int, int]]:
    """«ежедневно 9:00–22:00» → (9, 22); «круглосуточно» → (0, 24). Не понял — None."""
    low = (text or "").lower()
    if "круглосуточ" in low or "24/7" in low or "24 часа" in low:
        return 0, 24
    found = re.findall(r"(\d{1,2})(?:[:.](\d{2}))?", low)
    hours = [(int(h), int(m or 0)) for h, m in found if int(h) <= 24]
    if len(hours) < 2:
        return None
    (open_h, _), (close_h, _) = hours[0], hours[1]
    if close_h == 0:
        close_h = 24
    if 0 <= open_h < close_h <= 24:
        return open_h, close_h
    return None


def garage_hours(g: dict) -> Tuple[int, int]:
    open_h, close_h = g.get("open_hour"), g.get("close_hour")
    if isinstance(open_h, int) and isinstance(close_h, int) and 0 <= open_h < close_h <= 24:
        return open_h, close_h
    return DEFAULT_OPEN_HOUR, DEFAULT_CLOSE_HOUR


def busy_hours(bookings: Iterable[dict], day: date, open_h: int, close_h: int) -> Set[int]:
    spans = [(parse_dt(b["start_time"]), parse_dt(b["end_time"])) for b in bookings]
    busy = set()
    for hour in range(open_h, close_h):
        slot_start, slot_end = at_hour(day, hour), at_hour(day, hour + 1)
        if any(start < slot_end and end > slot_start for start, end in spans):
            busy.add(hour)
    return busy


def past_hours(day: date, open_h: int, close_h: int) -> Set[int]:
    """Часы, которые уже начались: бронировать их поздно."""
    current = now()
    return {h for h in range(open_h, close_h) if at_hour(day, h) <= current}


def toggle_range(selected: list, hour: int, blocked: Set[int]) -> list:
    """Выбор диапазона двумя нажатиями: начало и конец, часы между ними выбираются сами."""
    if not selected:
        return [hour]
    lo, hi = selected[0], selected[-1]
    if hour in selected:
        if hour == lo:
            return selected[1:]
        if hour == hi:
            return selected[:-1]
        return [hour]
    span = range(hour, hi + 1) if hour < lo else range(lo, hour + 1)
    if any(h in blocked for h in span):
        return [hour]
    return list(span)


# ─── Сообщения ──────────────────────────────────────────────

async def edit_or_send(message: Message, text: str,
                       markup: Optional[InlineKeyboardMarkup] = None) -> None:
    """Редактирует сообщение на месте; если нельзя (это фото или оно устарело) — шлёт новое."""
    if not message.photo:
        try:
            await message.edit_text(text, reply_markup=markup)
            return
        except TelegramBadRequest as e:
            if "not modified" in str(e):
                return
    await message.answer(text, reply_markup=markup)


async def answer_quietly(callback: CallbackQuery, text: Optional[str] = None,
                         alert: bool = False) -> None:
    """callback.answer, который не падает, если на кнопку уже ответили."""
    try:
        await callback.answer(text, show_alert=alert)
    except TelegramBadRequest:
        pass


# ─── Состояние ──────────────────────────────────────────────

KEEP_ON_RESET = ("results",)


async def reset(state) -> None:
    """Сбрасывает сценарий, но сохраняет результаты поиска — чтобы карусель
    продолжала листаться, даже если человек заглянул в другое меню."""
    data = await state.get_data()
    keep = {k: data[k] for k in KEEP_ON_RESET if k in data}
    await state.clear()
    if keep:
        await state.set_data(keep)
