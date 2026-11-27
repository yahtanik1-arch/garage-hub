"""Устойчивые уведомления: повтор доставки не повторяет физическую команду."""
import logging
from html import escape

from core import db
from core.utils import now, parse_dt
from tgbot import keyboards as kb, texts as t

log = logging.getLogger(__name__)


async def render_notification(row):
    p = row["payload"]
    kind = p["kind"]
    if kind == "ops_alert":
        return {"queue_delayed": "Задержка очереди открытия: проверьте worker и сроки команд.",
                "notifications_delayed": "Уведомления задерживаются: проверьте связь с мессенджерами.",
                "telemetry_stale": "Состояние замка устарело: проверьте связь и журнал устройства."}[p["code"]], None
    if kind in ("command", "command_alert"):
        c = await db.lock_command(p["command_id"])
        if not c:
            return "Команда больше не найдена. Обратитесь к администратору.", None
        text = t.lock_result(c)
        if kind == "command_alert":
            text += "\nКоманда: " + escape(c["id"])
        markup = kb.lock_command(c["id"]) if c["state"] not in ("device_confirmed", "failed", "expired", "cancelled") else kb.my_bookings_link()
        return text, markup
    b = await db.booking(p["booking_id"])
    if not b:
        return "Заявка больше не найдена.", None
    g = await db.garage(b["garage_id"]) or {}
    start, end = parse_dt(b["start_time"]), parse_dt(b["end_time"])
    if kind == "booking_created":
        return t.admin_booking_card(b, g, start, end, parse_dt(b["created_at"]), now(),
                                    title="Заявка"), kb.decide(b["id"]) if b["status"] == "pending" else None
    # Отложенное подтверждение не должно утверждать, что уже отменённая бронь действует.
    if kind == "booking_confirmed" and b["status"] == "confirmed":
        return t.confirmed_for_client(b["id"], g, start, end, b["total_price"]), kb.after_confirm(g)
    if kind == "booking_rejected":
        return t.rejected_for_client(b["id"], g.get("name") or "", start, end, b.get("admin_comment") or ""), kb.after_reject()
    if kind == "booking_cancelled":
        return t.client_cancelled_for_admin(b, g.get("name") or "—", start, end), None
    return t.my_booking_card(b, g, start, end), kb.my_bookings_link()


async def send_actor_message(bot, actor_id, text, markup=None, seed=None, **kwargs):
    if actor_id < 0:
        from vkbot.client import VKClient
        client = getattr(getattr(bot, "session", None), "vk", None)
        owned = client is None
        client = client or VKClient()
        try:
            return await client.send(-actor_id, text, markup, seed=seed)
        finally:
            if owned:
                await client.close()
    if bot is None:
        raise RuntimeError("Messenger delivery is not configured")
    return await bot.send_message(actor_id, text, reply_markup=markup, **kwargs)


async def deliver_one(bot, booking_id=None):
    from core.config import VK_TOKEN
    session = getattr(bot, "session", None)
    has_telegram = bot is not None and (getattr(session, "platform", "") != "vk" or session.vk.telegram is not None)
    channels = (["telegram"] if has_telegram else []) + (["vk"] if VK_TOKEN else [])
    if not channels:
        return None
    row = await db.claim_notification(booking_id, channels)
    if not row:
        return None
    try:
        text, markup = await render_notification(row)
        await send_actor_message(bot, row["recipient_id"], text, markup, seed="notification:%s" % row["id"])
    except Exception:
        log.warning("notification_delivery_failed id=%s attempt=%s", row["id"], row["attempts"])
        await db.finish_notification(row, False)
        return False
    await db.finish_notification(row, True)
    return True


async def try_booking_notification(bot, booking_id):
    if bot is None:
        return False
    try:
        return bool(await deliver_one(bot, booking_id))
    except Exception:
        log.warning("booking_notification_deferred booking_id=%s", booking_id)
        return False
