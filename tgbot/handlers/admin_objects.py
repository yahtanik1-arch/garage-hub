"""Админ: каталог объектов, добавление, редактирование, фото, удаление в архив."""
import logging
from typing import List

from aiogram import F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InputMediaPhoto, Message

from core import db
from tgbot import keyboards as kb
from tgbot import media
from tgbot import texts as t
from tgbot.callbacks import AddCb, EditCb, EditValCb, ObjCb, PhotoEditCb
from core.config import ADMIN_ID, DEFAULT_CLOSE_HOUR, DEFAULT_OPEN_HOUR, MAX_PHOTOS
from tgbot.states import AddGarage, EditGarage
from core.utils import (
    IsAdmin, answer_quietly, edit_or_send, now, parse_working_hours, reset, user_lock,
)

log = logging.getLogger(__name__)
router = Router(name="admin_objects")
router.message.filter(IsAdmin())
router.callback_query.filter(IsAdmin())


# ─── Каталог ────────────────────────────────────────────────

async def list_view(archive: bool = False):
    active = await db.garages(active=True)
    archived = await db.garages(active=False)
    items = archived if archive else active
    text = t.objects_list(len(active), len(archived), archive_view=archive)
    if not items and not archive:
        text += "\n\nКаталог пуст — добавьте первый объект."
    return text, kb.objects_list(items, len(archived), archive_view=archive)


async def show_list(message: Message, edit: bool = False, archive: bool = False) -> None:
    text, markup = await list_view(archive)
    if edit:
        await edit_or_send(message, text, markup)
    else:
        await message.answer(text, reply_markup=markup)


async def card_view(garage_id: int):
    g = await db.garage(garage_id)
    if not g:
        return None, None, None
    upcoming = len(await db.upcoming_for_garage(garage_id, now()))
    return g, t.admin_object_card(g, upcoming), upcoming


async def show_card(message: Message, garage_id: int, edit: bool = True) -> None:
    g, text, _ = await card_view(garage_id)
    if not g:
        await message.answer("Объект не найден.")
        return
    if edit:
        await edit_or_send(message, text, kb.object_actions(g))
    else:
        await message.answer(text, reply_markup=kb.object_actions(g))


@router.callback_query(ObjCb.filter())
async def on_object(callback: CallbackQuery, callback_data: ObjCb, state: FSMContext):
    action, gid = callback_data.action, callback_data.garage_id

    if action in ("list", "archive"):
        await answer_quietly(callback)
        await show_list(callback.message, edit=True, archive=action == "archive")
        return

    if action == "add":
        await answer_quietly(callback)
        await reset(state)
        await add_start(callback.message, state)
        return

    if action == "view":
        await answer_quietly(callback)
        await reset(state)
        await show_card(callback.message, gid)
        return

    if action == "preview":
        g = await db.garage(gid)
        if not g:
            await answer_quietly(callback, "Объект не найден.", alert=True)
            return
        await answer_quietly(callback)
        await send_client_card(callback.message, g)
        return

    if action == "edit":
        await answer_quietly(callback)
        await reset(state)
        g, text, _ = await card_view(gid)
        if g:
            await edit_or_send(callback.message, f"{text}\n\n<b>{t.EDIT_CHOOSE}</b>", kb.edit_menu(gid))
        return

    if action == "delete":
        g, _, upcoming = await card_view(gid)
        if not g:
            await answer_quietly(callback, "Объект не найден.", alert=True)
            return
        await answer_quietly(callback)
        await edit_or_send(callback.message, t.delete_ask(g, upcoming), kb.delete_confirm(gid))
        return

    if action in ("delete_yes", "restore"):
        restoring = action == "restore"
        await db.update_garage(gid, {"is_active": restoring})
        await answer_quietly(callback, t.RESTORED if restoring else t.DELETED)
        log.info("Объект %s: is_active=%s", gid, restoring)
        if restoring:
            await show_card(callback.message, gid)
        else:
            await show_list(callback.message, edit=True)


async def send_client_card(message: Message, g: dict) -> None:
    from tgbot.handlers.search import card_caption
    sent = await message.answer_photo(
        photo=media.garage_photo(g), caption=card_caption(g), reply_markup=kb.carousel(g, 0, 1),
    )
    if media.uses_placeholder(g):
        media.remember("placeholder", sent)


# ─── Добавление объекта ─────────────────────────────────────

CHOICE_STEPS = {"type", "lift", "pit", "tools", "size"}
OPTIONAL_STEPS = {"description", "owner_name", "owner_phone", "owner_contact"}
TEXT_STEPS = ["name", "district", "address", "price", "hours", "description",
              "owner_name", "owner_phone", "owner_contact"]


def add_steps(draft: dict) -> List[str]:
    if draft.get("type") == "detailing":
        return [s for s in t.ADD_STEPS if s not in ("lift", "pit", "tools")]
    return list(t.ADD_STEPS)


async def ask(message: Message, state: FSMContext, field: str) -> None:
    draft = (await state.get_data()).get("draft", {})
    await state.set_state(getattr(AddGarage, field))
    text = t.add_step(field, add_steps(draft))
    if field in CHOICE_STEPS:
        markup = kb.add_choice(field)
    elif field == "photos":
        markup = kb.add_photos_done()
    elif field in OPTIONAL_STEPS:
        markup = kb.add_skip()
    else:
        markup = None
    await message.answer(text, reply_markup=markup)


async def ask_next(message: Message, state: FSMContext, current: str) -> None:
    draft = (await state.get_data()).get("draft", {})
    steps = add_steps(draft)
    index = steps.index(current)
    if index + 1 < len(steps):
        await ask(message, state, steps[index + 1])
    else:
        await show_preview(message, state)


async def add_start(message: Message, state: FSMContext) -> None:
    await state.update_data(draft={"photos": []})
    await message.answer("Заполним карточку. Прервать можно кнопкой «Отмена».", reply_markup=kb.cancel_menu())
    await ask(message, state, "type")


def draft_to_garage(draft: dict) -> dict:
    hours = parse_working_hours(draft.get("working_hours") or "") or (DEFAULT_OPEN_HOUR, DEFAULT_CLOSE_HOUR)
    is_detailing = draft.get("type") == "detailing"
    return {
        "type": draft.get("type", "garage"),
        "name": draft.get("name"),
        "district": draft.get("district"),
        "address": draft.get("address"),
        "photos": draft.get("photos", []),
        "price_per_hour": draft.get("price_per_hour"),
        "has_lift": False if is_detailing else draft.get("has_lift", False),
        "has_pit": False if is_detailing else draft.get("has_pit", False),
        "tool_availability": "none" if is_detailing else draft.get("tool_availability", "none"),
        "box_size": draft.get("box_size"),
        "working_hours": draft.get("working_hours"),
        "open_hour": hours[0],
        "close_hour": hours[1],
        "description": draft.get("description"),
        "owner_name": draft.get("owner_name"),
        "owner_phone": draft.get("owner_phone"),
        "owner_contact": draft.get("owner_contact"),
        "owner_telegram_id": ADMIN_ID,
        "is_active": True,
    }


async def show_preview(message: Message, state: FSMContext) -> None:
    draft = (await state.get_data()).get("draft", {})
    await state.set_state(AddGarage.preview)
    await send_client_card(message, {**draft_to_garage(draft), "id": 0})
    await message.answer(t.ADD_PREVIEW, reply_markup=kb.add_preview())


@router.message(StateFilter(*(getattr(AddGarage, s) for s in TEXT_STEPS)), F.text)
async def add_text(message: Message, state: FSMContext):
    field = (await state.get_state()).split(":")[1]
    value = message.text.strip()
    data = await state.get_data()
    draft = data.get("draft", {})

    if field == "price":
        try:
            price = float(value.replace(",", ".").replace(" ", ""))
            if price <= 0:
                raise ValueError
        except ValueError:
            await message.answer(t.PRICE_INVALID)
            return
        draft["price_per_hour"] = price
    elif field == "hours":
        draft["working_hours"] = value
        hours = parse_working_hours(value)
        await message.answer(t.hours_parsed(*(hours or (None, None))))
    else:
        draft[field] = value

    await state.update_data(draft=draft)
    await ask_next(message, state, field)


@router.message(AddGarage.photos, F.photo)
async def add_photo(message: Message, state: FSMContext):
    async with user_lock(message.from_user.id):
        data = await state.get_data()
        draft = data.get("draft", {})
        photos = draft.setdefault("photos", [])
        if len(photos) >= MAX_PHOTOS:
            if not data.get("photos_limit_said"):
                await state.update_data(photos_limit_said=True)
                await message.answer(t.PHOTOS_LIMIT.format(max=MAX_PHOTOS), reply_markup=kb.add_photos_done())
            return
        if message.photo[-1].file_id not in photos:
            photos.append(message.photo[-1].file_id)
        await state.update_data(draft=draft)
        await counter(message, state, len(photos), kb.add_photos_done())


@router.message(AddGarage.photos)
async def add_photo_wrong(message: Message):
    await message.answer(t.PHOTO_EXPECTED, reply_markup=kb.add_photos_done())


async def counter(message: Message, state: FSMContext, count: int, markup) -> None:
    """Один счётчик фото, который обновляется на месте, а не сообщение на каждое фото."""
    data = await state.get_data()
    text = t.PHOTOS_COUNTER.format(n=count, max=MAX_PHOTOS)
    counter_id = data.get("photo_counter_id")
    if counter_id:
        try:
            await message.bot.edit_message_text(text, chat_id=message.chat.id, message_id=counter_id,
                                                reply_markup=markup)
            return
        except Exception:
            pass
    sent = await message.answer(text, reply_markup=markup)
    await state.update_data(photo_counter_id=sent.message_id)


ADD_CHOICE_FIELDS = {
    "type": ("type", lambda v: v),
    "lift": ("has_lift", lambda v: v == "1"),
    "pit": ("has_pit", lambda v: v == "1"),
    "tools": ("tool_availability", lambda v: v),
    "size": ("box_size", lambda v: v),
}


@router.callback_query(AddCb.filter())
async def on_add(callback: CallbackQuery, callback_data: AddCb, state: FSMContext):
    field = callback_data.field
    current = await state.get_state()
    if not current or not current.startswith("AddGarage:"):
        await answer_quietly(callback, t.SESSION_EXPIRED, alert=True)
        return
    step = current.split(":")[1]
    draft = (await state.get_data()).get("draft", {})

    if field in ADD_CHOICE_FIELDS:
        if step != field:
            await answer_quietly(callback)
            return
        key, convert = ADD_CHOICE_FIELDS[field]
        draft[key] = convert(callback_data.value)
        await state.update_data(draft=draft)
        await answer_quietly(callback)
        label = dict(kb.EDIT_CHOICES[key]).get(callback_data.value, "")
        await edit_or_send(callback.message, f"{t.add_step(field, add_steps(draft))}\n→ <b>{label}</b>")
        await ask_next(callback.message, state, field)
        return

    if field == "photos_done" and step == "photos":
        await answer_quietly(callback)
        await state.update_data(photo_counter_id=None, photos_limit_said=None)
        await ask_next(callback.message, state, "photos")
        return

    if field == "skip" and step in OPTIONAL_STEPS:
        await answer_quietly(callback)
        draft[step] = None
        await state.update_data(draft=draft)
        await ask_next(callback.message, state, step)
        return

    if field == "publish" and step == "preview":
        async with user_lock(callback.from_user.id):
            if await state.get_state() != AddGarage.preview.state:
                await answer_quietly(callback)
                return
            created = await db.create_garage(draft_to_garage(draft))
            await reset(state)
        if not created:
            await answer_quietly(callback, "Не удалось сохранить объект.", alert=True)
            return
        await answer_quietly(callback, "Опубликовано")
        await edit_or_send(callback.message, t.published(created.get("name") or ""))
        await callback.message.answer(t.EDIT_DONE, reply_markup=kb.admin_menu())
        await show_card(callback.message, created["id"], edit=False)
        return

    if field == "discard" and step == "preview":
        await reset(state)
        await answer_quietly(callback)
        await edit_or_send(callback.message, "Объект не сохранён.")
        await callback.message.answer(t.CANCELLED, reply_markup=kb.admin_menu())
        return

    await answer_quietly(callback)


# ─── Редактирование ─────────────────────────────────────────

@router.callback_query(EditCb.filter())
async def on_edit_field(callback: CallbackQuery, callback_data: EditCb, state: FSMContext):
    field, gid = callback_data.field, callback_data.garage_id
    g = await db.garage(gid)
    if not g:
        await answer_quietly(callback, "Объект не найден.", alert=True)
        return
    await answer_quietly(callback)
    label = dict(kb.EDIT_FIELDS)[field]
    if field in kb.EDIT_CHOICES:
        await edit_or_send(callback.message, f"<b>{t.h(g.get('name') or '')}</b>\n{label}:", kb.edit_choice(field, gid))
        return
    await state.set_state(EditGarage.value)
    await state.update_data(edit={"garage_id": gid, "field": field})
    await callback.message.answer(t.edit_prompt(field, g.get(field)), reply_markup=kb.cancel_menu())


@router.callback_query(EditValCb.filter())
async def on_edit_choice(callback: CallbackQuery, callback_data: EditValCb):
    field, value = callback_data.field, callback_data.value
    if field not in kb.EDIT_CHOICES:
        await answer_quietly(callback)
        return
    stored = value == "1" if field in ("has_lift", "has_pit") else value
    await db.update_garage(callback_data.garage_id, {field: stored})
    await answer_quietly(callback, t.EDIT_DONE)
    await show_card(callback.message, callback_data.garage_id)


@router.message(EditGarage.value, F.text)
async def on_edit_text(message: Message, state: FSMContext):
    edit = (await state.get_data()).get("edit") or {}
    field, gid = edit.get("field"), edit.get("garage_id")
    if not field:
        await reset(state)
        return
    value = message.text.strip()
    fields = {field: value}
    if field == "price_per_hour":
        try:
            price = float(value.replace(",", ".").replace(" ", ""))
            if price <= 0:
                raise ValueError
        except ValueError:
            await message.answer(t.PRICE_INVALID)
            return
        fields = {field: price}
    elif field == "working_hours":
        hours = parse_working_hours(value)
        open_h, close_h = hours or (DEFAULT_OPEN_HOUR, DEFAULT_CLOSE_HOUR)
        fields.update(open_hour=open_h, close_hour=close_h)
        await message.answer(t.hours_parsed(*(hours or (None, None))))

    await db.update_garage(gid, fields)
    await reset(state)
    await message.answer(t.EDIT_DONE, reply_markup=kb.admin_menu())
    await show_card(message, gid, edit=False)


# ─── Фото ───────────────────────────────────────────────────

@router.callback_query(PhotoEditCb.filter())
async def on_photos(callback: CallbackQuery, callback_data: PhotoEditCb, state: FSMContext):
    action, gid = callback_data.action, callback_data.garage_id
    g = await db.garage(gid)
    if not g:
        await answer_quietly(callback, "Объект не найден.", alert=True)
        return
    current = g.get("photos") or []

    if action == "menu":
        await answer_quietly(callback)
        await reset(state)
        await edit_or_send(callback.message, t.photos_edit_intro(len(current)), kb.photos_edit(gid, bool(current)))
        if len(current) == 1:
            await callback.message.answer_photo(photo=media.photo(current[0]))
        elif current:
            await callback.message.answer_media_group([InputMediaPhoto(media=media.photo(p)) for p in current[:MAX_PHOTOS]])
        return

    if action in ("add", "replace"):
        await answer_quietly(callback)
        keep = current if action == "add" else []
        if len(keep) >= MAX_PHOTOS:
            await callback.message.answer(f"Уже {MAX_PHOTOS} фото — сначала выберите «Заменить все».")
            return
        await state.set_state(EditGarage.photos)
        await state.update_data(photo_edit={"garage_id": gid, "keep": keep, "new": []}, photo_counter_id=None)
        await callback.message.answer(t.PHOTOS_SEND, reply_markup=kb.photos_edit_done(gid))
        return

    if action == "done":
        async with user_lock(callback.from_user.id):
            pe = (await state.get_data()).get("photo_edit")
            await reset(state)
        await answer_quietly(callback)
        if not pe or not pe["new"]:
            await callback.message.answer(t.PHOTOS_UNCHANGED, reply_markup=kb.admin_menu())
        else:
            photos = (pe["keep"] + pe["new"])[:MAX_PHOTOS]
            await db.update_garage(gid, {"photos": photos})
            await callback.message.answer(t.PHOTOS_SAVED.format(n=len(photos)), reply_markup=kb.admin_menu())
        await show_card(callback.message, gid, edit=False)


@router.message(EditGarage.photos, F.photo)
async def edit_photo(message: Message, state: FSMContext):
    async with user_lock(message.from_user.id):
        pe = (await state.get_data()).get("photo_edit")
        if not pe:
            return
        if len(pe["keep"]) + len(pe["new"]) >= MAX_PHOTOS:
            return
        pe["new"].append(message.photo[-1].file_id)
        await state.update_data(photo_edit=pe)
        await counter(message, state, len(pe["keep"]) + len(pe["new"]), kb.photos_edit_done(pe["garage_id"]))


@router.message(EditGarage.photos)
async def edit_photo_wrong(message: Message, state: FSMContext):
    pe = (await state.get_data()).get("photo_edit") or {}
    await message.answer(t.PHOTO_EXPECTED, reply_markup=kb.photos_edit_done(pe.get("garage_id", 0)))
