"""Основной прогон: поиск → бронь → подтверждение → отмена → архив. Объект создаёт сам и сам убирает."""
import asyncio, logging, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from local_guard import require_local_database
require_local_database()
logging.basicConfig(level=logging.ERROR)
import harness
harness.VERBOSE = False
from harness import Harness
from core import config, db


async def main():
    garage = await db.create_garage({
        "type": "garage", "name": "ТЕСТ Бокс", "district": "Марьино", "address": "ул. Тестовая, 1",
        "photos": [], "price_per_hour": 700, "has_lift": True, "has_pit": False,
        "tool_availability": "all", "box_size": "medium", "working_hours": "ежедневно 9:00–22:00",
        "open_hour": 9, "close_hour": 22, "owner_name": "Тест", "owner_phone": "+7 900 000-00-00",
        "owner_contact": "@test", "owner_telegram_id": config.ADMIN_ID, "is_active": True,
    })
    h = Harness()
    c = h.user(900000001, "test_client"); a = h.user(config.ADMIN_ID, "admin")
    steps = [
        (c, "send", "/start"), (c, "send", "Найти бокс"), (c, "click", "Гараж самообслуживания"),
        (c, "click", "Марьино"), (c, "click", "Далее"), (c, "click", "Неважно"), (c, "click", "Неважно"),
        (c, "click", "Неважно"), (c, "click", "Забронировать"), (c, "click", "Завтра"),
        (c, "click", "12:00"), (c, "click", "14:00"), (c, "click", "Отправить заявку"),
        (a, "click", "Подтвердить"), (c, "send", "Мои заявки"), (c, "click", "🟢"),
        (c, "click", "Отменить заявку"), (c, "click", "Да, отменить"),
        (a, "send", "/start"), (a, "send", "Все объекты"), (a, "click", "ТЕСТ"), (a, "click", "Как видит клиент"),
        (a, "click", "Удалить"), (a, "click", "Да, удалить"),
        (c, "send", "/find"),
    ]
    failed = None
    try:
        for who, kind, arg in steps:
            if kind == "send":
                await who.send(arg)
            else:
                await who.click(arg)
    except AssertionError as e:
        failed = f"{e} (шаг: {arg})"
    errors = [x for x in h.session.log if "пошло не так" in (x[2] or "") or "Ошибка в боте" in (x[2] or "")]
    print("шагов:", len(steps), "| сообщений бота:", len(h.session.log), "| ошибок:", len(errors),
          "| сбой:", failed or "нет")
    await db.update_garage(garage["id"], {"is_active": False})
    sys.exit(1 if failed or errors else 0)

asyncio.run(main())
