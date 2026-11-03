"""Админ: новые заявки, расписание по дням, подтверждение/отказ, заявки владельцев."""
import logging
from datetime import timedelta

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from core import db
from tgbot import keyboards as kb
from core import service
from tgbot import texts as t
from tgbot.callbacks import DayCb, DecideCb, LeadCb
from tgbot.states import Reject
from core.utils import IsAdmin, answer_quietly, at_hour, edit_or_send, now, parse_dt, reset, user_lock

log = logging.getLogger(__name__)
router = Router(name="admin_bookings")
router.message.filter(IsAdmin())
router.callback_query.filter(IsAdmin())


def booking_times(b: dict):
    return parse_dt(b["start_time"]), parse_dt(b["end_time"])


async def booking_card(b: dict, g=None, title: str = "") -> str:
    g = g if g is not None else await db.garage(b["garage_id"])
    start, end = booking_times(b)
    created = parse_dt(b["created_at"]) if b.get("created_at") else None
    return t.admin_booking_card(b, g, start, end, created, now(), title=title)


# ─── Новые заявки ───────────────────────────────────────────

async def show_inbox(message: Message) -> None:
    rows = await db.pending_bookings()
    if not rows:
        await message.answer(t.NO_PENDING)
        return
    garages = await db.garages_by_ids(b["garage_id"] for b in rows)
    noun = t.plural(len(rows), "заявка ждёт", "заявки ждут", "заявок ждут")
    await message.answer(f"<b>{len(rows)}</b> {noun} решения · от ранних к поздним")
    for b in rows:
        await message.answer(
            await booking_card(b, garages.get(b["garage_id"], {})),
            reply_markup=kb.decide(b["id"]),
        )


# ─── Расписание по дням ─────────────────────────────────────

async def day_view(offset: int):
    day = now().date() + timedelta(days=offset)
    rows = await db.bookings_between(at_hour(day, 0), at_hour(day, 24))
    garages = await db.garages_by_ids(b["garage_id"] for b in rows)
    items = []
    for b in rows:
        start, end = booking_times(b)
        name = (garages.get(b["garage_id"]) or {}).get("name") or "Объект недоступен"
        items.append((b, name, start, end))
    pending = [item for item in items if item[0]["status"] == "pending"]
    return t.day_schedule(day, items, offset), kb.day_schedule(offset, pending)


async def show_day(message: Message, offset: int, edit: bool = False) -> None:
    text, markup = await day_view(offset)
    if edit:
        await edit_or_send(message, text, markup)
    else:
        await message.answer(text, reply_markup=markup)


@router.callback_query(DayCb.filter())
async def on_day(callback: CallbackQuery, callback_data: DayCb):
    await answer_quietly(callback)
    await show_day(callback.message, max(0, min(6, callback_data.offset)), edit=True)


# ─── Решение по заявке ──────────────────────────────────────

async def already_decided(callback: CallbackQuery, booking_id: int) -> None:
    b = await db.booking(booking_id)
    status = t.status_line(b["status"]) if b else "не найдена"
    await answer_quietly(callback, t.ALREADY_DECIDED.format(status=status), alert=True)
    if b:
        await edit_or_send(callback.message, await booking_card(b))


@router.callback_query(DecideCb.filter())
async def on_decide(callback: CallbackQuery, callback_data: DecideCb, state: FSMContext):
    action, bid = callback_data.action, callback_data.booking_id

    if action == "open":
        b = await db.booking(bid)
        await answer_quietly(callback)
        if b:
            await callback.message.answer(
                await booking_card(b), reply_markup=kb.decide(bid) if b["status"] == "pending" else None,
            )
        return

    if action == "ok":
        async with user_lock(callback.from_user.id):
            result = await service.confirm_booking(callback.bot, bid, callback.from_user.id)
        if not result:
            await already_decided(callback, bid)
            return
        b, g, delivered = result
        await answer_quietly(callback, "Подтверждено. Клиент уведомлён." if delivered else "Подтверждено")
        await edit_or_send(callback.message, await booking_card(b, g))
        if not delivered:
            await callback.message.answer("Уведомление сохранено в очереди и будет отправлено повторно.")
        return

    if action == "no":
        b = await db.booking(bid)
        if not b or b["status"] != "pending":
            await already_decided(callback, bid)
            return
        await answer_quietly(callback)
        await edit_or_send(callback.message, f"{await booking_card(b)}\n\n<b>{t.REJECT_ASK}</b>",
                           kb.reject_reasons(bid))
        return

    if action == "back":
        await answer_quietly(callback)
        b = await db.booking(bid)
        if b:
            await edit_or_send(callback.message, await booking_card(b),
                               kb.decide(bid) if b["status"] == "pending" else None)
        return

    if action == "reason":
        if callback_data.reason == "custom":
            await answer_quietly(callback)
            await state.set_state(Reject.reason)
            await state.update_data(reject={
                "booking_id": bid,
                "chat_id": callback.message.chat.id,
                "message_id": callback.message.message_id,
            })
            await callback.message.answer(t.REJECT_CUSTOM, reply_markup=kb.cancel_menu())
            return
        reason = t.REJECT_REASONS.get(callback_data.reason, "Без указания причины")
        async with user_lock(callback.from_user.id):
            result = await service.reject_booking(callback.bot, bid, reason, callback.from_user.id)
        if not result:
            await already_decided(callback, bid)
            return
        await answer_quietly(callback, "Отклонено. Клиент уведомлён." if result[2] else "Отклонено. Уведомление в очереди.")
        await edit_or_send(callback.message, await booking_card(result[0], result[1]))


@router.message(Reject.reason, F.text)
async def on_reject_reason(message: Message, state: FSMContext):
    data = (await state.get_data()).get("reject") or {}
    await reset(state)
    if not data:
        return
    result = await service.reject_booking(message.bot, data["booking_id"], message.text.strip(), message.from_user.id)
    if not result:
        await message.answer("Эта заявка уже обработана.", reply_markup=kb.admin_menu())
        return
    await message.answer("Заявка отклонена, клиент уведомлён." if result[2] else "Заявка отклонена. Уведомление в очереди.", reply_markup=kb.admin_menu())
    try:
        await message.bot.edit_message_text(
            await booking_card(result[0], result[1]), chat_id=data["chat_id"], message_id=data["message_id"],
        )
    except Exception:
        pass


# ─── Заявки владельцев ──────────────────────────────────────

async def show_leads(message: Message) -> None:
    try:
        rows = await db.owner_requests("new")
    except Exception:
        log.exception("Таблица owner_requests недоступна")
        await message.answer("Таблица заявок владельцев ещё не создана — выполните sql/001_v2.sql.")
        return
    if not rows:
        await message.answer(t.NO_LEADS)
        return
    await message.answer(f"Новых заявок от владельцев: <b>{len(rows)}</b>")
    for r in rows:
        await message.answer(t.owner_lead(r), reply_markup=kb.lead_actions(r["id"]))


@router.callback_query(LeadCb.filter(F.action == "done"))
async def on_lead_done(callback: CallbackQuery, callback_data: LeadCb):
    closed = await db.close_owner_request(callback_data.request_id)
    await answer_quietly(callback, "Отмечено")
    body = t.owner_lead(closed) if closed else callback.message.html_text
    await edit_or_send(callback.message, f"{body}\n{t.DIVIDER}\n✓ Обработано")
