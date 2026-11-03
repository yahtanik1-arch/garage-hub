"""Два локальных бокса, один имитатор и действующая бронь клиента 900000001."""
import asyncio
from datetime import datetime, timedelta, timezone

from core import db, locks
from core.config import ADMIN_ID, APP_ENV, POSTGREST_URL


async def main():
    if APP_ENV != "local" or not POSTGREST_URL:
        raise RuntimeError("Демонстрационные данные разрешены только на локальном стенде")
    garages = await db.garages(active=None)
    existing = {g["name"]: g for g in garages}
    for i in (1, 2):
        name = "Локальный бокс №%s" % i
        g = existing.get(name) or await db.create_garage({
            "name": name, "type": "garage", "district": "Марьино", "address": "Тестовая улица, %s" % i,
            "price_per_hour": 500, "open_hour": 0, "close_hour": 24, "working_hours": "Круглосуточно",
            "description": "Локальная демонстрация, реальная площадка не подключена.", "is_active": True})
        await locks.configure_device(ADMIN_ID, g["id"], "simulator", "local-box-%s" % i, True, "Локальная демонстрация")
        if i == 1:
            current = datetime.now(timezone.utc)
            pending = await db.upcoming_for_garage(g["id"], current)
            if not pending:
                start = current.replace(minute=0, second=0, microsecond=0)
                b = await db.create_booking({"garage_id": g["id"], "client_telegram_id": 900000001,
                    "start_time": start.isoformat(), "end_time": (start + timedelta(hours=2)).isoformat(),
                    "total_price": 1000, "status": "pending"})
                await db.decide_booking(b["id"], "confirmed", ADMIN_ID)
    print("Созданы два локальных бокса. У клиента есть действующая бронь с тестовым замком.")


if __name__ == "__main__":
    asyncio.run(main())
