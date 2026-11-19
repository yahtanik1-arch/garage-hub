"""Фоновые напоминания: раз в минуту проверяем заявки.

- админу: заявка висит без ответа дольше ADMIN_REMIND_AFTER_MIN;
- клиенту: заявка долго на согласовании (CLIENT_WAIT_NOTICE_MIN);
- клиенту: до подтверждённого визита осталось VISIT_REMIND_BEFORE_MIN.

Каждое напоминание отправляется один раз — отметка хранится в самой заявке.
"""
import asyncio
import logging
from datetime import timedelta

from aiogram import Bot

from core import db, service
from tgbot import keyboards as kb
from tgbot import texts as t
from core.config import (
    ADMIN_REMIND_AFTER_MIN, CLIENT_WAIT_NOTICE_MIN, REMINDER_INTERVAL_SEC,
    VISIT_REMIND_BEFORE_MIN,
)
from core.utils import now, parse_dt

log = logging.getLogger(__name__)


async def _send(bot: Bot, chat_id: int, text: str, markup=None) -> None:
    try:
        await bot.send_message(chat_id, text, reply_markup=markup)
    except Exception as e:
        log.warning("Напоминание не доставлено %s: %s", chat_id, e)


async def tick(bot: Bot) -> None:
    current = now()

    for b in await db.stale_pending(current - timedelta(minutes=ADMIN_REMIND_AFTER_MIN), "admin_reminded"):
        await db.mark_booking(b["id"], "admin_reminded")
        g = await db.garage(b["garage_id"]) or {}
        start, end = parse_dt(b["start_time"]), parse_dt(b["end_time"])
        await service.notify_admins(bot, t.admin_reminder(b, g.get("name") or "—", start, end), kb.decide(b["id"]))

    for b in await db.stale_pending(current - timedelta(minutes=CLIENT_WAIT_NOTICE_MIN), "client_wait_notified"):
        await db.mark_booking(b["id"], "client_wait_notified")
        if b["client_telegram_id"] and parse_dt(b["start_time"]) > current:
            await _send(bot, b["client_telegram_id"], t.wait_notice(b["id"]), kb.my_bookings_link())

    for b in await db.visits_starting(current, current + timedelta(minutes=VISIT_REMIND_BEFORE_MIN)):
        await db.mark_booking(b["id"], "visit_reminded")
        if not b["client_telegram_id"]:
            continue
        g = await db.garage(b["garage_id"]) or {}
        start, end = parse_dt(b["start_time"]), parse_dt(b["end_time"])
        await _send(bot, b["client_telegram_id"], t.visit_reminder(g, start, end, current.date()), kb.after_confirm(g))


async def run(bot: Bot) -> None:
    log.info("Напоминания запущены (каждые %s с)", REMINDER_INTERVAL_SEC)
    while True:
        try:
            await tick(bot)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Ошибка в напоминаниях")
        await asyncio.sleep(REMINDER_INTERVAL_SEC)
