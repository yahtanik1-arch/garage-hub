"""Все клавиатуры бота. Тексты кнопок меню — в классе Btn."""
from datetime import date
from typing import Dict, Iterable, List, Optional, Set

from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
    WebAppInfo,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder

from tgbot import texts as t
from core.config import webapp_url
from core.utils import admin_view
from tgbot.callbacks import (
    AddCb, BookCb, CarouselCb, DayCb, DecideCb, EditCb, EditValCb, LeadCb, MyCb,
    Noop, ObjCb, OwnerCb, PhotoEditCb, SearchCb, UnlockCb,
)


class Btn:
    FIND = "Найти бокс"
    BOOKINGS = "Мои заявки"
    LIST = "Разместить бокс"
    CONTACT = "Контакты"
    ADD = "Добавить объект"
    ALL = "Все объекты"
    INBOX = "Новые заявки"
    TODAY = "Заявки сегодня"
    LEADS = "Заявки владельцев"
    PANEL = "Панель"
    CANCEL = "Отмена"


def _btn(text: str, cb) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=cb.pack())


def _url(text: str, url: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, url=url)


def _markup(*rows) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[list(r) for r in rows if r])


# ─── Нижнее меню ────────────────────────────────────────────

def user_menu() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=Btn.FIND)],
            [KeyboardButton(text=Btn.BOOKINGS), KeyboardButton(text=Btn.CONTACT)],
            [KeyboardButton(text=Btn.LIST)],
        ],
        resize_keyboard=True,
        is_persistent=True,
        input_field_placeholder="Выберите действие в меню",
    )


def admin_menu() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=Btn.INBOX), KeyboardButton(text=Btn.TODAY)],
            [KeyboardButton(text=Btn.ALL), KeyboardButton(text=Btn.ADD)],
            [KeyboardButton(text=Btn.LEADS), KeyboardButton(text=Btn.PANEL)],
        ],
        resize_keyboard=True,
        is_persistent=True,
        input_field_placeholder="Панель управления",
    )


def home_markup(user_id: int) -> ReplyKeyboardMarkup:
    return admin_menu() if admin_view(user_id) else user_menu()


def phone_menu() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="Отправить мой номер", request_contact=True)],
            [KeyboardButton(text=Btn.CANCEL)],
        ],
        resize_keyboard=True,
        input_field_placeholder="+7 ...",
    )


def cancel_menu() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=Btn.CANCEL)]],
        resize_keyboard=True,
        input_field_placeholder="Введите ответ или нажмите «Отмена»",
    )


def open_app() -> Optional[InlineKeyboardMarkup]:
    """Кнопка запуска Mini App. Нужна публичная HTTPS-ссылка (WEBAPP_URL в .env)."""
    url = webapp_url()
    if not url:
        return None
    return _markup([InlineKeyboardButton(text="Открыть приложение", web_app=WebAppInfo(url=url + "/"))])


# ─── Поиск ──────────────────────────────────────────────────

def _search_footer(matches: int, can_back: bool) -> List[List[InlineKeyboardButton]]:
    rows = []
    if matches:
        noun = t.plural(matches, "вариант", "варианта", "вариантов")
        rows.append([_btn(f"Показать {matches} {noun} →", SearchCb(action="show"))])
    if can_back:
        rows.append([_btn("← Назад", SearchCb(action="back"))])
    return rows


def search_type(matches: int) -> InlineKeyboardMarkup:
    rows = [
        [_btn("Гараж самообслуживания", SearchCb(action="type", value="garage"))],
        [_btn("Детейлинг-бокс", SearchCb(action="type", value="detailing"))],
        [_btn("Любой", SearchCb(action="type", value="any"))],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows + _search_footer(matches, False))


def search_districts(districts: List[str], selected: Iterable[str], counts: Dict[str, int],
                     matches: int) -> InlineKeyboardMarkup:
    selected = set(selected)
    kb = InlineKeyboardBuilder()
    for i, name in enumerate(districts):
        if name not in selected and not counts.get(name):
            continue
        label = f"✓ {name}" if name in selected else f"{name} · {counts[name]}"
        kb.add(_btn(label, SearchCb(action="district", value=str(i))))
    kb.adjust(2)
    rows = kb.export()
    rows.append([_btn("Далее →" if selected else "Любой район →", SearchCb(action="next"))])
    return InlineKeyboardMarkup(inline_keyboard=rows + _search_footer(matches, True))


def search_need(step: str, matches: int) -> InlineKeyboardMarkup:
    yes = {"lift": "Нужен", "pit": "Нужна", "tools": "Нужен"}[step]
    rows = [[
        _btn(yes, SearchCb(action=step, value="1")),
        _btn("Неважно", SearchCb(action=step, value="0")),
    ]]
    return InlineKeyboardMarkup(inline_keyboard=rows + _search_footer(matches, True))


def search_again() -> InlineKeyboardMarkup:
    return _markup([_btn("Изменить фильтры", SearchCb(action="start"))])


def carousel(g: dict, index: int, total: int) -> InlineKeyboardMarkup:
    rows = []
    if total > 1:
        rows.append([
            _btn("◀", CarouselCb(action="page", index=(index - 1) % total)),
            _btn(f"{index + 1} / {total}", Noop(hint="pos")),
            _btn("▶", CarouselCb(action="page", index=(index + 1) % total)),
        ])
    extra = []
    photos = len(g.get("photos") or [])
    if photos > 1:
        extra.append(_btn(f"Все фото · {photos}", CarouselCb(action="photos", index=index)))
    url = t.map_url(g)
    if url:
        extra.append(_url("На карте ↗", url))
    rows.append(extra)
    rows.append([_btn("Забронировать", BookCb(action="start", value=str(g["id"])))])
    return _markup(*rows)


# ─── Бронирование ───────────────────────────────────────────

def booking_dates(days: List[date], free: Dict[date, int]) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for i, day in enumerate(days):
        name = {0: "Сегодня", 1: "Завтра"}.get(i, t.day_short(day))
        hours = free.get(day, 0)
        if hours:
            kb.add(_btn(f"{name} · {hours} ч", BookCb(action="date", value=day.isoformat())))
        else:
            kb.add(_btn(f"{name} · {'недоступно' if i == 0 else 'занято'}", Noop(hint="day")))
    kb.adjust(2)
    return kb.as_markup()


def booking_slots(open_h: int, close_h: int, blocked: Set[int], selected: List[int],
                  total: Optional[float]) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for hour in range(open_h, close_h):
        label = t.hhmm(hour)
        if hour in selected:
            kb.add(_btn(f"● {label}", BookCb(action="slot", value=str(hour))))
        elif hour in blocked:
            kb.add(_btn(f"✕ {label}", Noop(hint="slot")))
        else:
            kb.add(_btn(label, BookCb(action="slot", value=str(hour))))
    kb.adjust(3)
    rows = kb.export()
    bottom = [_btn("← Даты", BookCb(action="dates"))]
    if selected and total is not None:
        bottom.append(_btn("Отправить заявку", BookCb(action="send")))
    rows.append(bottom)
    return InlineKeyboardMarkup(inline_keyboard=rows)


def after_confirm(g: dict) -> InlineKeyboardMarkup:
    url = t.map_url(g)
    return _markup(
        [_url("Открыть на карте ↗", url)] if url else None,
        [_btn("Мои заявки", MyCb(action="list"))],
    )


def after_reject() -> InlineKeyboardMarkup:
    return _markup([_btn("Найти другой бокс", SearchCb(action="start"))])


def my_bookings_link() -> InlineKeyboardMarkup:
    return _markup([_btn("Мои заявки", MyCb(action="list"))])


# ─── Мои заявки ─────────────────────────────────────────────

def my_bookings(upcoming: List[tuple]) -> Optional[InlineKeyboardMarkup]:
    rows = []
    for b, name, start, _ in upcoming:
        icon, _ = t.STATUS.get(b["status"], ("•", ""))
        label = f"{icon} {t.day_short(start.date())} {start.strftime('%H:%M')} · {t.clip(name, 20)}"
        rows.append([_btn(label, MyCb(action="view", booking_id=b["id"]))])
    return _markup(*rows) if rows else None


def my_booking_card(b: dict, g: Optional[dict], can_cancel: bool, action_id=None) -> InlineKeyboardMarkup:
    url = t.map_url(g) if g and b["status"] == "confirmed" else None
    return _markup(
        [_btn("Открыть бокс", UnlockCb(action="open", value=action_id))] if action_id else None,
        [_url("На карте ↗", url)] if url else None,
        [_btn("Отменить заявку", MyCb(action="cancel", booking_id=b["id"]))] if can_cancel else None,
        [_btn("← Все заявки", MyCb(action="list"))],
    )


def lock_command(command_id: str) -> InlineKeyboardMarkup:
    return _markup([_btn("Проверить результат", UnlockCb(action="status", value=command_id))],
                   [_btn("Мои заявки", MyCb(action="list"))])


def cancel_confirm(booking_id: int) -> InlineKeyboardMarkup:
    return _markup([
        _btn("Да, отменить", MyCb(action="cancel_yes", booking_id=booking_id)),
        _btn("Нет", MyCb(action="view", booking_id=booking_id)),
    ])


# ─── Владельцы ──────────────────────────────────────────────

def owner_type() -> InlineKeyboardMarkup:
    return _markup(
        [_btn("Гараж самообслуживания", OwnerCb(value="garage"))],
        [_btn("Детейлинг-бокс", OwnerCb(value="detailing"))],
    )


def lead_actions(request_id: int) -> InlineKeyboardMarkup:
    return _markup([_btn("Обработано ✓", LeadCb(action="done", request_id=request_id))])


# ─── Админ: объекты ─────────────────────────────────────────

def objects_list(items: List[dict], archived: int, archive_view: bool = False) -> InlineKeyboardMarkup:
    rows = []
    for g in items:
        photos = len(g.get("photos") or [])
        label = f"{t.clip(g.get('name') or '', 26)} · {t.money(g.get('price_per_hour'))}"
        if not photos:
            label += " · без фото"
        rows.append([_btn(label, ObjCb(action="view", garage_id=g["id"]))])
    if archive_view:
        rows.append([_btn("← К каталогу", ObjCb(action="list"))])
    else:
        bottom = [_btn("+ Добавить", ObjCb(action="add"))]
        if archived:
            bottom.insert(0, _btn(f"Архив · {archived}", ObjCb(action="archive")))
        rows.append(bottom)
    return InlineKeyboardMarkup(inline_keyboard=rows)


def object_actions(g: dict) -> InlineKeyboardMarkup:
    gid = g["id"]
    photos = len(g.get("photos") or [])
    active = g.get("is_active", True)
    return _markup(
        [_btn("Редактировать", ObjCb(action="edit", garage_id=gid)),
         _btn(f"Фото · {photos}", PhotoEditCb(action="menu", garage_id=gid))],
        [_btn("Как видит клиент", ObjCb(action="preview", garage_id=gid))],
        [_btn("Удалить", ObjCb(action="delete", garage_id=gid))] if active
        else [_btn("Вернуть в каталог", ObjCb(action="restore", garage_id=gid))],
        [_btn("← К списку", ObjCb(action="list" if active else "archive"))],
    )


def delete_confirm(garage_id: int) -> InlineKeyboardMarkup:
    return _markup([
        _btn("Да, удалить", ObjCb(action="delete_yes", garage_id=garage_id)),
        _btn("Отмена", ObjCb(action="view", garage_id=garage_id)),
    ])


EDIT_FIELDS = [
    ("name", "Название"), ("type", "Тип"),
    ("district", "Район"), ("address", "Адрес"),
    ("price_per_hour", "Цена"), ("working_hours", "Часы работы"),
    ("has_lift", "Подъёмник"), ("has_pit", "Яма"),
    ("tool_availability", "Инструмент"), ("box_size", "Размер"),
    ("description", "Описание"), ("owner_name", "Имя владельца"),
    ("owner_phone", "Телефон владельца"), ("owner_contact", "Telegram владельца"),
]


def edit_menu(garage_id: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for field, label in EDIT_FIELDS:
        kb.add(_btn(label, EditCb(field=field, garage_id=garage_id)))
    kb.adjust(2)
    rows = kb.export()
    rows.append([_btn("← Назад", ObjCb(action="view", garage_id=garage_id))])
    return InlineKeyboardMarkup(inline_keyboard=rows)


EDIT_CHOICES = {
    "has_lift": [("1", "Есть"), ("0", "Нет")],
    "has_pit": [("1", "Есть"), ("0", "Нет")],
    "tool_availability": [("all", "Есть на площадке"), ("none", "Нужен свой"), ("partial", "Частично")],
    "box_size": [("small", "до 4,5 м — седаны"), ("medium", "до 5,5 м — внедорожники"),
                 ("large", "до 7 м — Газели")],
    "type": [("garage", "Гараж самообслуживания"), ("detailing", "Детейлинг-бокс")],
}


def edit_choice(field: str, garage_id: int) -> InlineKeyboardMarkup:
    rows = [[_btn(label, EditValCb(field=field, value=value, garage_id=garage_id))]
            for value, label in EDIT_CHOICES[field]]
    rows.append([_btn("← Назад", ObjCb(action="edit", garage_id=garage_id))])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def photos_edit(garage_id: int, has_photos: bool) -> InlineKeyboardMarkup:
    return _markup(
        [_btn("Добавить к текущим", PhotoEditCb(action="add", garage_id=garage_id)),
         _btn("Заменить все", PhotoEditCb(action="replace", garage_id=garage_id))] if has_photos
        else [_btn("Загрузить фото", PhotoEditCb(action="replace", garage_id=garage_id))],
        [_btn("← Назад", ObjCb(action="view", garage_id=garage_id))],
    )


def photos_edit_done(garage_id: int) -> InlineKeyboardMarkup:
    return _markup([_btn("Готово", PhotoEditCb(action="done", garage_id=garage_id))])


# ─── Админ: добавление ──────────────────────────────────────

def add_choice(field: str) -> InlineKeyboardMarkup:
    options = {
        "type": EDIT_CHOICES["type"],
        "lift": EDIT_CHOICES["has_lift"],
        "pit": EDIT_CHOICES["has_pit"],
        "tools": EDIT_CHOICES["tool_availability"],
        "size": EDIT_CHOICES["box_size"],
    }[field]
    if field in ("lift", "pit"):
        return _markup([_btn(label, AddCb(field=field, value=value)) for value, label in options])
    return _markup(*[[_btn(label, AddCb(field=field, value=value))] for value, label in options])


def add_photos_done() -> InlineKeyboardMarkup:
    return _markup([_btn("Готово", AddCb(field="photos_done"))])


def add_skip() -> InlineKeyboardMarkup:
    return _markup([_btn("Пропустить", AddCb(field="skip"))])


def add_preview() -> InlineKeyboardMarkup:
    return _markup([
        _btn("Опубликовать", AddCb(field="publish")),
        _btn("Отменить", AddCb(field="discard")),
    ])


# ─── Админ: заявки ──────────────────────────────────────────

def decide(booking_id: int) -> InlineKeyboardMarkup:
    return _markup([
        _btn("Подтвердить", DecideCb(action="ok", booking_id=booking_id)),
        _btn("Отказать", DecideCb(action="no", booking_id=booking_id)),
    ])


def reject_reasons(booking_id: int) -> InlineKeyboardMarkup:
    rows = [[_btn(label, DecideCb(action="reason", booking_id=booking_id, reason=code))]
            for code, label in t.REJECT_REASONS.items()]
    rows.append([_btn("Другая причина…", DecideCb(action="reason", booking_id=booking_id, reason="custom"))])
    rows.append([_btn("← Назад", DecideCb(action="back", booking_id=booking_id))])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def day_schedule(offset: int, pending: List[tuple]) -> InlineKeyboardMarkup:
    rows = [[_btn(f"Решить №{b['id']} · {start.strftime('%H:%M')}", DecideCb(action="open", booking_id=b["id"]))]
            for b, _, start, _ in pending]
    nav = []
    if offset > 0:
        nav.append(_btn("◀", DayCb(offset=offset - 1)))
    nav.append(_btn("Сегодня" if offset else "Обновить", DayCb(offset=0)))
    if offset < 6:
        nav.append(_btn("▶", DayCb(offset=offset + 1)))
    rows.append(nav)
    return InlineKeyboardMarkup(inline_keyboard=rows)
