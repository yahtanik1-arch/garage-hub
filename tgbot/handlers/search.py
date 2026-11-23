"""Поиск боксов: пошаговые фильтры в одном сообщении + карусель результатов."""
import logging
from typing import List, Optional

from aiogram import Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InputMediaPhoto, Message

from core import db
from tgbot import keyboards as kb
from tgbot import media
from tgbot import texts as t
from tgbot.callbacks import CarouselCb, SearchCb
from core.utils import answer_quietly, edit_or_send

log = logging.getLogger(__name__)
router = Router(name="search")

CAPTION_LIMIT = 1024


# ─── Логика фильтров ────────────────────────────────────────

def steps_for(s: dict) -> List[str]:
    steps = ["type"]
    if s["districts"]:
        steps.append("district")
    if s["filters"].get("type") != "detailing":
        steps += ["lift", "pit", "tools"]
    return steps


def matching(garages: List[dict], filters: dict, ignore: Optional[str] = None) -> List[dict]:
    result = []
    for g in garages:
        if filters.get("type") and (g.get("type") or "garage") != filters["type"]:
            continue
        if ignore != "districts" and filters.get("districts") and (g.get("district") or "").strip() not in filters["districts"]:
            continue
        if filters.get("lift") and not g.get("has_lift"):
            continue
        if filters.get("pit") and not g.get("has_pit"):
            continue
        if filters.get("tools") and g.get("tool_availability") != "all":
            continue
        result.append(g)
    return result


def screen(s: dict):
    steps = steps_for(s)
    step = s["step"]
    filters = s["filters"]
    found = len(matching(s["garages"], filters))
    text = t.search_screen(step, steps.index(step) + 1, len(steps), filters, found)
    if step == "type":
        markup = kb.search_type(found)
    elif step == "district":
        pool = matching(s["garages"], filters, ignore="districts")
        counts = {}
        for g in pool:
            name = (g.get("district") or "").strip()
            counts[name] = counts.get(name, 0) + 1
        markup = kb.search_districts(s["districts"], filters.get("districts", []), counts, found)
    else:
        markup = kb.search_need(step, found)
    return text, markup


# ─── Вход в поиск ───────────────────────────────────────────

async def start_search(message: Message, state: FSMContext, edit: bool = False) -> None:
    garages = await db.garages(active=True)
    if not garages:
        await message.answer(t.EMPTY_CATALOG)
        return
    districts = sorted({(g.get("district") or "").strip() for g in garages} - {""})
    s = {"filters": {}, "step": "type", "garages": garages, "districts": districts}
    await state.update_data(search=s)
    text, markup = screen(s)
    if edit:
        await edit_or_send(message, text, markup)
    else:
        await message.answer(text, reply_markup=markup)


@router.callback_query(SearchCb.filter())
async def on_search(callback: CallbackQuery, callback_data: SearchCb, state: FSMContext):
    action, value = callback_data.action, callback_data.value
    if action == "start":
        await answer_quietly(callback)
        await start_search(callback.message, state, edit=True)
        return

    data = await state.get_data()
    s = data.get("search")
    if not s:
        await answer_quietly(callback, t.SESSION_EXPIRED, alert=True)
        return
    filters = s["filters"]

    if action == "type":
        if value == "any":
            filters.pop("type", None)
        else:
            filters["type"] = value
        if value == "detailing":
            for key in ("lift", "pit", "tools"):
                filters.pop(key, None)
        await go_next(callback, state, s)
        return

    if action == "district":
        try:
            name = s["districts"][int(value)]
        except (ValueError, IndexError):
            await answer_quietly(callback)
            return
        chosen = filters.setdefault("districts", [])
        if name in chosen:
            chosen.remove(name)
        else:
            chosen.append(name)
        await state.update_data(search=s)
        await answer_quietly(callback)
        text, markup = screen(s)
        await edit_or_send(callback.message, text, markup)
        return

    if action in ("lift", "pit", "tools"):
        if value == "1":
            filters[action] = True
        else:
            filters.pop(action, None)
        await go_next(callback, state, s)
        return

    if action == "next":
        await go_next(callback, state, s)
        return

    if action == "back":
        steps = steps_for(s)
        index = steps.index(s["step"]) if s["step"] in steps else 0
        s["step"] = steps[max(0, index - 1)]
        await state.update_data(search=s)
        await answer_quietly(callback)
        text, markup = screen(s)
        await edit_or_send(callback.message, text, markup)
        return

    if action == "show":
        await answer_quietly(callback)
        await show_results(callback.message, state, s)


async def go_next(callback: CallbackQuery, state: FSMContext, s: dict) -> None:
    await answer_quietly(callback)
    steps = steps_for(s)
    index = steps.index(s["step"]) if s["step"] in steps else 0
    if index + 1 >= len(steps) or not matching(s["garages"], s["filters"]):
        await show_results(callback.message, state, s)
        return
    s["step"] = steps[index + 1]
    await state.update_data(search=s)
    text, markup = screen(s)
    await edit_or_send(callback.message, text, markup)


# ─── Результаты: карусель ───────────────────────────────────

def card_caption(g: dict) -> str:
    for limit in (350, 150, 0):
        text = t.garage_card(g, description_limit=limit) if limit else t.garage_card({**g, "description": ""})
        if len(text) <= CAPTION_LIMIT:
            return text
    return text[:CAPTION_LIMIT]


async def show_results(message: Message, state: FSMContext, s: dict) -> None:
    found = sorted(matching(s["garages"], s["filters"]), key=lambda g: float(g.get("price_per_hour") or 0))
    if not found:
        await edit_or_send(message, t.NOT_FOUND, kb.search_again())
        return
    await edit_or_send(message, t.search_summary(s["filters"], len(found)), kb.search_again())
    await state.update_data(results=found, search=None)
    g = found[0]
    sent = await message.answer_photo(
        photo=media.garage_photo(g), caption=card_caption(g), reply_markup=kb.carousel(g, 0, len(found)),
    )
    if media.uses_placeholder(g):
        media.remember("placeholder", sent)


@router.callback_query(CarouselCb.filter())
async def on_carousel(callback: CallbackQuery, callback_data: CarouselCb, state: FSMContext):
    results = (await state.get_data()).get("results") or []
    if not results:
        await answer_quietly(callback, t.SESSION_EXPIRED, alert=True)
        return
    index = callback_data.index % len(results)
    g = results[index]

    if callback_data.action == "photos":
        await answer_quietly(callback)
        photos = (g.get("photos") or [])[:5]
        if len(photos) > 1:
            await callback.message.answer_media_group([InputMediaPhoto(media=media.photo(p)) for p in photos])
        return

    await answer_quietly(callback)
    edited = await callback.message.edit_media(
        media=InputMediaPhoto(media=media.garage_photo(g), caption=card_caption(g)),
        reply_markup=kb.carousel(g, index, len(results)),
    )
    if media.uses_placeholder(g) and not isinstance(edited, bool):
        media.remember("placeholder", edited)
