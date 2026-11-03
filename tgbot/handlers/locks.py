"""Регистрация открытия и просмотр результата. Физическая отправка только в worker."""
from aiogram import Router
from aiogram.types import CallbackQuery

from core import db, locks, service
from core.config import ADMIN_IDS
from core.utils import answer_quietly, edit_or_send
from tgbot import keyboards as kb, texts as t
from tgbot.callbacks import UnlockCb

router = Router(name="locks")


@router.callback_query(UnlockCb.filter())
async def on_unlock(callback: CallbackQuery, callback_data: UnlockCb):
    try:
        if callback_data.action == "open":
            action_id = locks.uuid_value(callback_data.value)
            # ID броней берётся по серверному действию, а не из редактируемой кнопки.
            action = await db.unlock_action(action_id, callback.from_user.id)
            if not action:
                raise service.ServiceError("not_found", "Кнопка не найдена. Откройте свою бронь заново.", 404)
            c = await locks.request_open(callback.from_user.id, action["booking_id"], action_id, "bot")
        elif callback_data.action == "status":
            c = await locks.command_for_user(callback_data.value, callback.from_user.id,
                callback.from_user.id in ADMIN_IDS)
        else:
            raise service.ServiceError("bad_action", "Неизвестное действие.")
    except service.ServiceError as error:
        await answer_quietly(callback, error.message, alert=True)
        return
    await answer_quietly(callback, "Запрос принят" if callback_data.action == "open" else None)
    markup = kb.my_bookings_link() if c["state"] in locks.TERMINAL else kb.lock_command(c["id"])
    await edit_or_send(callback.message, t.lock_result(c), markup)
