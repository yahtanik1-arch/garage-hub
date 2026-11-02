"""Все обращения к Supabase. Хендлеры не ходят в базу напрямую — только через этот модуль.

supabase-py синхронный, поэтому каждый запрос уходит в отдельный поток
и не блокирует бота, пока база отвечает.
"""
import asyncio
from datetime import datetime, timezone
from typing import Dict, Iterable, List, Optional

from core.config import supabase

ACTIVE_STATUSES = ["pending", "confirmed"]


async def _run(query):
    result = await asyncio.to_thread(query.execute)
    return result.data or []


def _ts(dt: datetime) -> str:
    """Время для фильтров PostgREST: UTC без знака «+», чтобы не испортился в URL."""
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _first(rows):
    return rows[0] if rows else None


# ─── Объекты ─────────────────────────────────────────────────

def _garages():
    return supabase.table("garages")


async def garages(active: Optional[bool] = True) -> List[dict]:
    query = _garages().select("*").order("id")
    if active is not None:
        query = query.eq("is_active", active)
    return await _run(query)


async def garage(garage_id) -> Optional[dict]:
    return _first(await _run(_garages().select("*").eq("id", int(garage_id)).limit(1)))


async def garages_by_ids(ids: Iterable) -> Dict[int, dict]:
    ids = sorted({int(i) for i in ids})
    if not ids:
        return {}
    rows = await _run(_garages().select("*").in_("id", ids))
    return {g["id"]: g for g in rows}


async def create_garage(data: dict) -> Optional[dict]:
    return _first(await _run(_garages().insert(data)))


async def update_garage(garage_id, fields: dict) -> Optional[dict]:
    return _first(await _run(_garages().update(fields).eq("id", int(garage_id))))


async def districts() -> List[str]:
    rows = await _run(_garages().select("district").eq("is_active", True))
    return sorted({g["district"].strip() for g in rows if (g.get("district") or "").strip()})


# ─── Брони ───────────────────────────────────────────────────

def _bookings():
    return supabase.table("bookings")


async def booking(booking_id) -> Optional[dict]:
    return _first(await _run(_bookings().select("*").eq("id", int(booking_id)).limit(1)))


async def busy_bookings(garage_id, start: datetime, end: datetime) -> List[dict]:
    """Активные брони объекта, пересекающие интервал [start, end)."""
    return await _run(
        _bookings()
        .select("start_time, end_time")
        .eq("garage_id", int(garage_id))
        .in_("status", ACTIVE_STATUSES)
        .lt("start_time", _ts(end))
        .gt("end_time", _ts(start))
    )


async def busy_for_garages(ids: Iterable, start: datetime, end: datetime) -> List[dict]:
    """Активные брони нескольких объектов за интервал — одним запросом."""
    ids = sorted({int(i) for i in ids})
    if not ids:
        return []
    return await _run(
        _bookings()
        .select("garage_id, start_time, end_time")
        .in_("garage_id", ids)
        .in_("status", ACTIVE_STATUSES)
        .lt("start_time", _ts(end))
        .gt("end_time", _ts(start))
    )


async def create_booking(data: dict) -> Optional[dict]:
    return _first(await _run(_bookings().insert(data)))


async def change_booking_status(booking_id, new_status: str, allowed_from: Iterable[str],
                                comment: Optional[str] = None) -> Optional[dict]:
    """Меняет статус, только если заявка сейчас в одном из allowed_from.

    Возвращает обновлённую заявку или None, если её уже кто-то обработал —
    так повторное нажатие старой кнопки ничего не испортит.
    """
    fields = {"status": new_status}
    if comment is not None:
        fields["admin_comment"] = comment
    return _first(await _run(
        _bookings().update(fields).eq("id", int(booking_id)).in_("status", list(allowed_from))
    ))


async def mark_booking(booking_id, field: str) -> None:
    await _run(_bookings().update({field: True}).eq("id", int(booking_id)))


async def pending_bookings() -> List[dict]:
    return await _run(_bookings().select("*").eq("status", "pending").order("start_time"))


async def bookings_between(start: datetime, end: datetime,
                           statuses: Iterable[str] = ACTIVE_STATUSES) -> List[dict]:
    return await _run(
        _bookings().select("*")
        .in_("status", list(statuses))
        .gte("start_time", _ts(start))
        .lt("start_time", _ts(end))
        .order("start_time")
    )


async def client_bookings(client_id: int, limit: int = 30) -> List[dict]:
    return await _run(
        _bookings().select("*")
        .eq("client_telegram_id", client_id)
        .order("start_time", desc=True)
        .limit(limit)
    )


async def upcoming_for_garage(garage_id, now: datetime) -> List[dict]:
    return await _run(
        _bookings().select("id")
        .eq("garage_id", int(garage_id))
        .in_("status", ACTIVE_STATUSES)
        .gt("end_time", _ts(now))
    )


async def stale_pending(created_before: datetime, flag: str) -> List[dict]:
    """Заявки pending, созданные раньше created_before, по которым ещё не было напоминания flag."""
    return await _run(
        _bookings().select("*")
        .eq("status", "pending")
        .eq(flag, False)
        .lt("created_at", _ts(created_before))
    )


async def visits_starting(start: datetime, end: datetime) -> List[dict]:
    return await _run(
        _bookings().select("*")
        .eq("status", "confirmed")
        .eq("visit_reminded", False)
        .gte("start_time", _ts(start))
        .lt("start_time", _ts(end))
    )


# ─── Заявки владельцев ──────────────────────────────────────

def _owner_requests():
    return supabase.table("owner_requests")


async def create_owner_request(data: dict) -> Optional[dict]:
    return _first(await _run(_owner_requests().insert(data)))


async def owner_requests(status: str = "new") -> List[dict]:
    return await _run(_owner_requests().select("*").eq("status", status).order("created_at"))


async def close_owner_request(request_id) -> Optional[dict]:
    return _first(await _run(
        _owner_requests().update({"status": "done"}).eq("id", int(request_id)).eq("status", "new")
    ))


# ─── Доставка событий Telegram ─────────────────────────────

async def telegram_update_processed(bot_id: int, update_id: int) -> bool:
    return bool(await _run(
        supabase.table("telegram_updates").select("update_id")
        .eq("bot_id", bot_id).eq("update_id", update_id).limit(1)
    ))


async def mark_telegram_update_processed(bot_id: int, update_id: int) -> None:
    await _run(supabase.table("telegram_updates").upsert(
        {"bot_id": bot_id, "update_id": update_id}, on_conflict="bot_id,update_id",
    ))
