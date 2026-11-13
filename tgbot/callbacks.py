"""Данные inline-кнопок.

У каждого сценария свой префикс, поэтому кнопки разных сценариев
не перехватывают друг друга (та самая грабля с tools_* / etools_*).
Новый сценарий = новый класс с новым префиксом.
"""
from aiogram.filters.callback_data import CallbackData


class Noop(CallbackData, prefix="noop"):
    """Кнопка-надпись: ничего не делает, только показывает подсказку."""
    hint: str = ""


# ─── Клиент: поиск ───────────────────────────────────────────

class SearchCb(CallbackData, prefix="s"):
    action: str          # start / type / district / lift / pit / tools / next / back / show
    value: str = ""


class CarouselCb(CallbackData, prefix="c"):
    action: str          # page / photos
    index: int = 0


class BookCb(CallbackData, prefix="b"):
    action: str          # start / date / slot / dates / send
    value: str = ""


# ─── Клиент: мои заявки ─────────────────────────────────────

class MyCb(CallbackData, prefix="my"):
    action: str          # list / view / cancel / cancel_yes
    booking_id: int = 0


# ─── Владелец ───────────────────────────────────────────────

class OwnerCb(CallbackData, prefix="ow"):
    value: str           # garage / detailing


# ─── Админ: объекты ─────────────────────────────────────────

class ObjCb(CallbackData, prefix="o"):
    action: str          # list / archive / add / view / preview / edit / delete / delete_yes / restore
    garage_id: int = 0


class AddCb(CallbackData, prefix="a"):
    field: str           # type / lift / pit / tools / size / photos_done / skip / publish / discard
    value: str = ""


class EditCb(CallbackData, prefix="e"):
    field: str
    garage_id: int


class EditValCb(CallbackData, prefix="ev"):
    field: str           # has_lift / has_pit / tool_availability / box_size / type
    value: str
    garage_id: int


class PhotoEditCb(CallbackData, prefix="ep"):
    action: str          # menu / add / replace / done
    garage_id: int


# ─── Админ: заявки ──────────────────────────────────────────

class DecideCb(CallbackData, prefix="d"):
    action: str          # ok / no / reason / back / open
    booking_id: int
    reason: str = ""


class DayCb(CallbackData, prefix="day"):
    offset: int


class LeadCb(CallbackData, prefix="lead"):
    action: str          # done
    request_id: int


class UnlockCb(CallbackData, prefix="lk"):
    action: str
    value: str
