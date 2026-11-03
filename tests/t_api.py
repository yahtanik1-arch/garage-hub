"""Проверка API Mini App: вход, права, бронь, отмена, админ. Бот подменён фейковым — в Telegram ничего не уходит."""
import asyncio, hashlib, hmac, json, logging, os, sys, time
from pathlib import Path
from urllib.parse import urlencode
sys.path.insert(0, str(Path(__file__).resolve().parent))
from local_guard import require_local_database
require_local_database()
os.environ["MINIAPP_DEV"] = "1"
os.environ["EXTRA_ADMIN_IDS"] = "900000002"   # второй админ для проверки
logging.basicConfig(level=logging.ERROR)
import harness
harness.VERBOSE = False
from aiohttp.test_utils import TestClient, TestServer
from core import config, db
from api.server import build_app

CLIENT, ADMIN = 900000001, config.ADMIN_ID
fails = []


def check(name, cond, extra=""):
    print(("  ✓ " if cond else "  ✗ ") + name + ((" — " + str(extra)) if extra and not cond else ""))
    if not cond:
        fails.append(name)


def init_data(user_id, tamper=False, age=0):
    fields = {"auth_date": str(int(time.time()) - age), "query_id": "AAH",
              "user": json.dumps({"id": user_id, "first_name": "Тест", "username": "t"}, ensure_ascii=False)}
    check_str = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    secret = hmac.new(b"WebAppData", config.TOKEN.encode(), hashlib.sha256).digest()
    fields["hash"] = hmac.new(secret, check_str.encode(), hashlib.sha256).hexdigest()
    if tamper:
        fields["user"] = json.dumps({"id": ADMIN, "first_name": "Хакер"}, ensure_ascii=False)
    return urlencode(fields)


async def main():
    garage = await db.create_garage({
        "type": "garage", "name": "ТЕСТ API", "district": "Марьино", "address": "ул. Тестовая, 2",
        "photos": [], "price_per_hour": 500, "has_lift": True, "has_pit": True, "tool_availability": "all",
        "box_size": "medium", "working_hours": "9–22", "open_hour": 9, "close_hour": 22,
        "owner_name": "СЕКРЕТНОЕ_ИМЯ", "owner_phone": "СЕКРЕТНЫЙ_ТЕЛЕФОН", "owner_contact": "@secret",
        "owner_telegram_id": ADMIN, "is_active": True,
    })
    gid = garage["id"]
    h = harness.Harness()
    app = build_app(h.bot)
    async with TestClient(TestServer(app)) as c:
        dev = lambda uid: {"Authorization": f"dev {uid}"}
        print("Вход и права")
        r = await c.get("/api/garages"); check("каталог открыт без входа (для сайта)", r.status == 200)
        r = await c.get("/api/garages"); check("в открытом каталоге нет контактов владельца", "СЕКРЕТ" not in await r.text())
        r = await c.get("/api/bookings"); check("«мои заявки» без входа → 401", r.status == 401)
        r = await c.post("/api/bookings", json={}); check("бронь без входа → 401", r.status == 401)
        r = await c.get("/api/me"); check("профиль без входа → 401", r.status == 401)
        r = await c.get("/api/admin/garages"); check("админка без входа → 401", r.status == 401)
        r = await c.get("/api/me", headers={"Authorization": "tma " + init_data(CLIENT)})
        check("настоящая подпись Telegram принимается", r.status == 200 and (await r.json())["id"] == CLIENT)
        r = await c.get("/api/me", headers={"Authorization": "tma " + init_data(CLIENT, tamper=True)})
        check("подделанные данные → 401", r.status == 401)
        r = await c.get("/api/me", headers={"Authorization": "tma " + init_data(CLIENT, age=3 * 86400)})
        check("просроченная подпись → 401", r.status == 401)
        r = await c.get("/api/me", headers={**dev(ADMIN), "X-Forwarded-For": "1.2.3.4"})
        check("dev-вход через прокси/туннель запрещён", r.status == 401)
        r = await c.get("/api/me", headers=dev(CLIENT)); me = await r.json()
        check("клиент не админ", r.status == 200 and me["is_admin"] is False)
        r = await c.get("/api/admin/summary", headers=dev(CLIENT)); check("клиенту админка закрыта → 403", r.status == 403)
        r = await c.get("/api/admin/summary", headers=dev(ADMIN)); check("админу открыта", r.status == 200)
        r = await c.get("/api/admin/summary", headers=dev(900000002)); check("второму админу (EXTRA_ADMIN_IDS) открыта", r.status == 200)
        r = await c.get("/api/me", headers=dev(900000002)); check("второй админ видит себя админом", (await r.json())["is_admin"] is True)

        print("Каталог")
        r = await c.get("/api/garages", headers=dev(CLIENT)); raw = await r.text()
        check("каталог отдаётся", r.status == 200 and "ТЕСТ API" in raw)
        check("контакты владельца НЕ утекают клиенту", "СЕКРЕТ" not in raw and "owner_" not in raw)
        r = await c.get(f"/api/garages/{gid}", headers=dev(CLIENT)); check("карточка без контактов", "СЕКРЕТ" not in await r.text())
        r = await c.get(f"/api/garages/{gid}/availability", headers=dev(CLIENT)); av = await r.json()
        check("доступность на 7 дней", r.status == 200 and len(av["days"]) == 7)
        r = await c.get("/api/photo/неизвестный_id"); check("чужой file_id → 404", r.status == 404)

        print("Бронь")
        tomorrow = av["days"][1]["date"]
        r = await c.post("/api/bookings", headers=dev(CLIENT), json={"garage_id": gid, "date": tomorrow, "hours": [10, 11, 12]})
        b = await r.json(); check("заявка создана", r.status == 201 and b["status"] == "pending" and b["total"] == 1500, b)
        check("часы и дата в ответе", (b.get("start_hour"), b.get("end_hour")) == (10, 13) and b.get("date") == tomorrow)
        r = await c.post("/api/bookings", headers=dev(CLIENT), json={"garage_id": gid, "date": tomorrow, "hours": [12, 13]})
        check("пересечение с занятым → 409", r.status == 409)
        r = await c.post("/api/bookings", headers=dev(CLIENT), json={"garage_id": gid, "date": tomorrow, "hours": [15, 17]})
        check("часы с пропуском → 400", r.status == 400)
        r = await c.post("/api/bookings", headers=dev(CLIENT), json={"garage_id": gid, "date": tomorrow, "hours": [3]})
        check("время вне часов работы → 400", r.status == 400)
        r = await c.post("/api/bookings", headers=dev(CLIENT), json={"garage_id": gid, "date": "2020-01-01", "hours": [10]})
        check("прошедшая дата → 400", r.status == 400)
        r = await c.post("/api/bookings", headers=dev(CLIENT), data="не json"); check("мусор вместо JSON → 400", r.status == 400)
        r = await c.get(f"/api/garages/{gid}/availability", headers=dev(CLIENT)); av = await r.json()
        check("занятые часы видны в доступности", {10, 11, 12} <= set(av["days"][1]["blocked"]))
        r = await c.get("/api/bookings", headers=dev(CLIENT)); mine = (await r.json())["bookings"]
        check("«Мои заявки» содержит новую", any(x["id"] == b["id"] for x in mine))
        r = await c.get("/api/bookings", headers=dev(ADMIN)); check("чужие заявки не видны", all(x["id"] != b["id"] for x in (await r.json())["bookings"]))
        r = await c.post(f"/api/bookings/{b['id']}/cancel", headers=dev(ADMIN)); check("чужую заявку отменить нельзя → 404", r.status == 404)

        print("Админ")
        r = await c.get("/api/admin/inbox", headers=dev(ADMIN)); inbox = (await r.json())["bookings"]
        mine_in = [x for x in inbox if x["id"] == b["id"]]
        check("заявка в «Новых» с контактами владельца", bool(mine_in) and mine_in[0]["owner_phone"] == "СЕКРЕТНЫЙ_ТЕЛЕФОН")
        r = await c.post(f"/api/admin/bookings/{b['id']}/confirm", headers=dev(ADMIN)); j = await r.json()
        check("подтверждение", r.status == 200 and j["booking"]["status"] == "confirmed")
        r = await c.post(f"/api/admin/bookings/{b['id']}/confirm", headers=dev(ADMIN)); check("повторное подтверждение → 409", r.status == 409)
        r = await c.post(f"/api/admin/bookings/{b['id']}/reject", headers=dev(ADMIN), json={"reason": "тест"}); check("отказ после подтверждения → 409", r.status == 409)
        r = await c.get("/api/admin/day?offset=1", headers=dev(ADMIN)); day = await r.json()
        check("расписание дня", r.status == 200 and any(x["id"] == b["id"] for x in day["bookings"]))
        r = await c.post(f"/api/bookings/{b['id']}/cancel", headers=dev(CLIENT)); j = await r.json()
        check("клиент отменяет подтверждённую", r.status == 200 and j["status"] == "cancelled")
        r = await c.post(f"/api/bookings/{b['id']}/cancel", headers=dev(CLIENT)); check("повторная отмена → 409", r.status == 409)
        r = await c.post("/api/bookings", headers=dev(CLIENT), json={"garage_id": gid, "date": tomorrow, "hours": [14]})
        b2 = await r.json()
        r = await c.post(f"/api/admin/bookings/{b2['id']}/reject", headers=dev(ADMIN), json={"reason": "Это время уже занято"}); j = await r.json()
        check("отказ с причиной", r.status == 200 and j["booking"]["status"] == "rejected" and j["booking"]["comment"] == "Это время уже занято")
        r = await c.post(f"/api/admin/bookings/{b2['id']}/reject", headers=dev(ADMIN), json={}); check("отказ без причины → 400", r.status in (400, 409))
        r = await c.get("/api/admin/garages", headers=dev(ADMIN)); gs = (await r.json())["garages"]
        check("админ видит владельца объекта", any(g["id"] == gid and g["owner_phone"] == "СЕКРЕТНЫЙ_ТЕЛЕФОН" for g in gs))
        r = await c.post(f"/api/admin/garages/{gid}/archive", headers=dev(ADMIN)); check("архив", r.status == 200 and (await r.json())["is_active"] is False)
        r = await c.get("/api/garages", headers=dev(CLIENT)); check("архивный объект скрыт от клиента", "ТЕСТ API" not in await r.text())
        r = await c.post("/api/bookings", headers=dev(CLIENT), json={"garage_id": gid, "date": tomorrow, "hours": [16]}); check("бронь архивного → 404", r.status == 404)
        r = await c.post(f"/api/admin/garages/{gid}/restore", headers=dev(ADMIN)); check("возврат из архива", r.status == 200 and (await r.json())["is_active"] is True)

        print("Владелец")
        r = await c.post("/api/owner-requests", headers=dev(CLIENT), json={"name": "Иван", "phone": "123", "object_type": "garage", "address": "Химки, ул. Победы 1"})
        check("короткий телефон → 400", r.status == 400)
        r = await c.post("/api/owner-requests", headers=dev(CLIENT), json={"name": "Иван", "phone": "+7 900 111-22-33", "object_type": "garage", "address": "Химки, ул. Победы 1"})
        check("заявка владельца", r.status == 201)
        r = await c.post("/api/owner-requests", headers=dev(CLIENT), json={"name": "Иван", "phone": "+7 900 111-22-33", "object_type": "garage", "address": "Химки, ул. Победы 1"})
        check("повтор подряд → 429", r.status == 429)
        r = await c.get("/api/admin/leads", headers=dev(ADMIN)); leads = (await r.json())["leads"]
        check("заявка владельца видна админу", any(x["address"].startswith("Химки") for x in leads))

        print("Статика и безопасность")
        r = await c.get("/"); page = await r.text()
        check("главная страница", r.status == 200 and "GarageHub" in page)
        check("защитные заголовки на странице", "script-src 'self' https://telegram.org" in r.headers.get("Content-Security-Policy", ""))
        for path, kind in [("/css/tokens.css", "css"), ("/js/main.js", "javascript"), ("/assets/favicon.svg", "svg")]:
            r = await c.get(path); check(f"файл {path}", r.status == 200 and kind in r.headers.get("Content-Type", ""), r.status)
        for evil in ["/../.env", "/%2e%2e/.env", "/js/../../.env", "/..%2f.env", "/js/%2e%2e/%2e%2e/.env", "/.env", "/../config.py"]:
            r = await c.get(evil); text = await r.text()
            check(f"нет доступа к секретам: {evil}", "TELEGRAM_BOT_TOKEN" not in text and "SUPABASE" not in text and r.status != 200, r.status)
        r = await c.get("/api/health"); check("health", r.status == 200)
        r = await c.get("/api/health"); check("API без кеширования", r.headers.get("Cache-Control") == "no-store")

        print("Фото")
        with_photo = [g for g in await db.garages(active=True) if g.get("photos")]
        if with_photo:
            fid = with_photo[0]["photos"][0]
            r = await c.get(f"/api/photo/{fid}")
            body = await r.read()
            check("фото отдаётся", r.status == 200 and r.headers.get("Content-Type", "").startswith("image/") and len(body) > 1000, r.status)
            check("фото кешируется надолго", "immutable" in r.headers.get("Cache-Control", ""))
            r = await c.get(f"/api/photo/{fid}"); check("повторный запрос из кеша", r.status == 200)
        else:
            print("  (нет объектов с фото — пропущено)")
    await db.update_garage(gid, {"is_active": False})
    print("\nИТОГ:", "всё прошло" if not fails else f"упало {len(fails)}: {fails}")
    sys.exit(1 if fails else 0)

asyncio.run(main())
