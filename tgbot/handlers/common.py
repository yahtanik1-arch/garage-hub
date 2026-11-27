"""Старт, главное меню, контакты, режим «как клиент».

Этот роутер подключается первым: кнопки нижнего меню срабатывают всегда,
даже посреди сценария, и не попадают в чужие поля ввода.
"""
import logging

from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from core import db
from tgbot import keyboards as kb
from tgbot import media
from tgbot import texts as t
from tgbot.callbacks import Noop
from core.config import webapp_url
from tgbot.handlers import admin_bookings, admin_objects, booking, owners, search
from tgbot.keyboards import Btn
from core.utils import IsAdmin, admin_view, answer_quietly, at_hour, now, reset, set_client_view

log = logging.getLogger(__name__)
router = Router(name="common")


async def show_home(message: Message, user_id: int, state: FSMContext, first_name: str = "") -> None:
    await reset(state)
    if admin_view(user_id):
        await show_dashboard(message)
        return
    sent = await message.answer_photo(
        photo=media.image("cover"), caption=t.start_text(first_name) + (t.APP_HINT if webapp_url() and getattr(message.bot.session, "platform", "") != "vk" else ""), reply_markup=kb.user_menu(),
    )
    media.remember("cover", sent)


async def show_dashboard(message: Message) -> None:
    today = now().date()
    active = await db.garages(active=True)
    archived = await db.garages(active=False)
    pending = await db.pending_bookings()
    todays = await db.bookings_between(at_hour(today, 0), at_hour(today, 24))
    try:
        leads = await db.owner_requests("new")
    except Exception:
        leads = []
    await message.answer(
        t.admin_dashboard(len(active), len(archived), len(pending), len(todays), len(leads)),
        reply_markup=kb.admin_menu(),
    )


# ─── Команды ────────────────────────────────────────────────

@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    await show_home(message, message.from_user.id, state, message.from_user.first_name)


@router.message(Command("id"))
async def cmd_id(message: Message):
    """Показывает человеку его Telegram ID — нужен, чтобы выдать права админа."""
    if getattr(message.bot.session, "platform", "") == "vk":
        await message.answer("Ваш VK ID: %s" % -message.from_user.id)
    else:
        await message.answer(t.your_id(message.from_user.id))


@router.message(Command("app"))
async def cmd_app(message: Message):
    if getattr(message.bot.session, "platform", "") == "vk":
        from core.config import WEBSITE_URL
        await message.answer("Сайт GarageHub: " + WEBSITE_URL if WEBSITE_URL else
                             "Сайт пока не указан. Бронируйте через меню бота.")
        return
    markup = kb.open_app()
    if markup:
        await message.answer(t.APP_OPEN, reply_markup=markup)
    else:
        await message.answer("Приложение сейчас недоступно. Пользуйтесь меню бота — всё работает и здесь.")


@router.message(Command("client"), IsAdmin())
async def cmd_client(message: Message, state: FSMContext):
    set_client_view(message.from_user.id, True)
    await reset(state)
    await message.answer(t.CLIENT_MODE_ON, reply_markup=kb.user_menu())


@router.message(Command("admin"), IsAdmin())
async def cmd_admin(message: Message, state: FSMContext):
    set_client_view(message.from_user.id, False)
    await show_home(message, message.from_user.id, state)


@router.message(Command("find"))
@router.message(F.text == Btn.FIND)
async def menu_find(message: Message, state: FSMContext):
    await reset(state)
    await search.start_search(message, state)


@router.message(Command("my"))
@router.message(F.text == Btn.BOOKINGS)
async def menu_bookings(message: Message, state: FSMContext):
    await reset(state)
    await booking.show_my_bookings(message, message.from_user.id)


@router.message(Command("contacts"))
@router.message(F.text == Btn.CONTACT)
async def menu_contact(message: Message):
    await message.answer(t.CONTACT)


@router.message(F.text == Btn.LIST)
async def menu_list(message: Message, state: FSMContext):
    await reset(state)
    await owners.start(message, state)


@router.message(F.text.in_({Btn.CANCEL, "Главная"}))
async def menu_cancel(message: Message, state: FSMContext):
    await reset(state)
    await message.answer(t.CANCELLED, reply_markup=kb.home_markup(message.from_user.id))


# ─── Кнопки админа ──────────────────────────────────────────

@router.message(F.text == Btn.PANEL, IsAdmin())
async def menu_panel(message: Message, state: FSMContext):
    await reset(state)
    await show_dashboard(message)


@router.message(F.text == Btn.ADD, IsAdmin())
async def menu_add(message: Message, state: FSMContext):
    await reset(state)
    await admin_objects.add_start(message, state)


@router.message(F.text == Btn.ALL, IsAdmin())
async def menu_all(message: Message, state: FSMContext):
    await reset(state)
    await admin_objects.show_list(message)


@router.message(F.text == Btn.INBOX, IsAdmin())
async def menu_inbox(message: Message, state: FSMContext):
    await reset(state)
    await admin_bookings.show_inbox(message)


@router.message(F.text == Btn.TODAY, IsAdmin())
async def menu_today(message: Message, state: FSMContext):
    await reset(state)
    await admin_bookings.show_day(message, 0)


@router.message(F.text == Btn.LEADS, IsAdmin())
async def menu_leads(message: Message, state: FSMContext):
    await reset(state)
    await admin_bookings.show_leads(message)


# ─── Кнопки-надписи ─────────────────────────────────────────

NOOP_HINTS = {
    "slot": "Это время занято или уже прошло.",
    "day": "На этот день свободных часов нет.",
    "pos": "Листайте стрелками ◀ ▶",
}


@router.callback_query(Noop.filter())
async def noop(callback: CallbackQuery, callback_data: Noop):
    await answer_quietly(callback, NOOP_HINTS.get(callback_data.hint))
