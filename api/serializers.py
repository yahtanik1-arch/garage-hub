"""Превращает записи из базы в JSON для приложения.

Главное правило: публичные функции никогда не отдают контакты владельца.
Они есть только в admin_* и только для админа.
"""
from typing import Optional

from core import service
from core.utils import garage_hours, now, parse_dt


def pretty(value: str) -> str:
    """Район пишут как попало («выхино», «МАРЬИНО»): приводим к виду «Выхино»."""
    value = (value or "").strip()
    return value[:1].upper() + value[1:] if value.islower() else value


def photo_url(file_id: str) -> str:
    return f"/api/photo/{file_id}"


def garage_public(g: dict, today: Optional[dict] = None) -> dict:
    open_h, close_h = garage_hours(g)
    data = {
        "id": g["id"],
        "demo_payment": bool(g.get("demo_payment")),
        "type": g.get("type") or "garage",
        "name": g.get("name") or "",
        "district": pretty(g.get("district")),
        "address": (g.get("address") or "").strip(),
        "price": float(g.get("price_per_hour") or 0),
        "has_lift": bool(g.get("has_lift")),
        "has_pit": bool(g.get("has_pit")),
        "tools": g.get("tool_availability") or "",
        "size": g.get("box_size") or "",
        "working_hours": g.get("working_hours") or "",
        "description": g.get("description") or "",
        "open_hour": open_h,
        "close_hour": close_h,
        "photos": [photo_url(p) for p in (g.get("photos") or [])],
    }
    if today is not None:
        data.update(today)
    return data


def garage_admin(g: dict, upcoming: int = 0) -> dict:
    data = garage_public(g)
    data.update({
        "is_active": g.get("is_active", True),
        "upcoming": upcoming,
        "owner_name": g.get("owner_name") or "",
        "owner_phone": g.get("owner_phone") or "",
        "owner_contact": g.get("owner_contact") or "",
    })
    return data


def _times(b: dict) -> dict:
    start, end = parse_dt(b["start_time"]), parse_dt(b["end_time"])
    end_hour = 24 if end.hour == 0 and end.date() > start.date() else end.hour
    return {
        "date": start.date().isoformat(),
        "start_hour": start.hour,
        "end_hour": end_hour,
        "hours": end_hour - start.hour,
    }


def _garage_brief(g: Optional[dict]) -> dict:
    g = g or {}
    photos = g.get("photos") or []
    return {
        "id": g.get("id"),
        "name": g.get("name") or "Площадка недоступна",
        "district": pretty(g.get("district")),
        "address": (g.get("address") or "").strip(),
        "photo": photo_url(photos[0]) if photos else None,
        "type": g.get("type") or "garage",
    }


def booking_json(b: dict, g: Optional[dict]) -> dict:
    data = {
        "id": b["id"],
        "status": b["status"],
        "total": float(b["total_price"] or 0),
        "comment": b.get("admin_comment") if b["status"] == "rejected" else None,
        "can_cancel": service.can_cancel(b),
        "garage": _garage_brief(g),
    }
    data.update(_times(b))
    return data


def booking_admin(b: dict, g: Optional[dict]) -> dict:
    data = booking_json(b, g)
    g = g or {}
    created = parse_dt(b["created_at"]) if b.get("created_at") else None
    data.update({
        "client_id": b["client_telegram_id"],
        "client_username": b.get("client_username") or "",
        "client_name": b.get("web_client_name") or "",
        "client_phone": b.get("web_client_phone") or "",
        "minutes_ago": int((now() - created).total_seconds() // 60) if created else None,
        "owner_name": g.get("owner_name") or "",
        "owner_phone": g.get("owner_phone") or "",
        "owner_contact": g.get("owner_contact") or "",
    })
    return data


def lead_json(r: dict) -> dict:
    return {
        "id": r["id"],
        "name": r.get("name") or "",
        "phone": r.get("phone") or "",
        "object_type": r.get("object_type") or "",
        "address": r.get("address") or "",
        "telegram": r.get("telegram_username") or "",
        "created_at": r.get("created_at"),
    }
