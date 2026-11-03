"""Все тексты бота и оформление карточек. Тексты правим здесь, а не в хендлерах."""
from datetime import date, datetime
from html import escape as h
from typing import Iterable, List, Optional
from urllib.parse import quote_plus

from core.config import ADMIN_PHONE, ADMIN_TG

LOGO = "GarageHub"
DIVIDER = "──────────────"
NBSP = " "

WEEKDAYS = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]
MONTHS = ["января", "февраля", "марта", "апреля", "мая", "июня",
          "июля", "августа", "сентября", "октября", "ноября", "декабря"]

STATUS = {
    "pending": ("🟡", "на согласовании"),
    "confirmed": ("🟢", "подтверждена"),
    "rejected": ("🔴", "отклонена"),
    "cancelled": ("⚪️", "отменена"),
}

REJECT_REASONS = {
    "busy": "Это время уже занято",
    "closed": "Площадка в этот день не работает",
    "tech": "На площадке технические работы",
}


# ─── Мелкие помощники ───────────────────────────────────────

def money(value) -> str:
    number = round(float(value or 0), 2)
    if number.is_integer():
        return f"{int(number):,}".replace(",", NBSP) + f"{NBSP}₽"
    return f"{number:,.2f}".rstrip("0").replace(",", NBSP).replace(".", ",") + f"{NBSP}₽"


def plural(n: int, one: str, few: str, many: str) -> str:
    if n % 10 == 1 and n % 100 != 11:
        return one
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return few
    return many


def clip(text: str, limit: int) -> str:
    text = (text or "").strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def bar(step: int, total: int, width: int = 10) -> str:
    """Шкала прогресса: ▰▰▰▱▱▱▱▱▱▱"""
    filled = round(width * step / total) if total else 0
    return "▰" * filled + "▱" * (width - filled)


def hhmm(hour: int) -> str:
    return f"{hour % 24:02d}:00" if hour != 24 else "24:00"


def span(start_hour: int, end_hour: int) -> str:
    return f"{hhmm(start_hour)}–{hhmm(end_hour)}"


def day_long(d: date) -> str:
    """«пт, 17 октября»"""
    return f"{WEEKDAYS[d.weekday()]}, {d.day} {MONTHS[d.month - 1]}"


def day_short(d: date) -> str:
    """«пт 17.10»"""
    return f"{WEEKDAYS[d.weekday()]} {d.strftime('%d.%m')}"


def time_span(start: datetime, end: datetime) -> str:
    end_label = "24:00" if end.hour == 0 and end.date() > start.date() else end.strftime("%H:%M")
    return f"{start.strftime('%H:%M')}–{end_label}"


def hours_between(start: datetime, end: datetime) -> int:
    return max(1, round((end - start).total_seconds() / 3600))


def object_type(value: Optional[str]) -> str:
    return "Детейлинг-бокс" if value == "detailing" else "Гараж самообслуживания"


def tools(value: Optional[str]) -> str:
    return {"all": "есть на площадке", "none": "нужен свой", "partial": "частично"}.get(value or "", "не указано")


def size(value: Optional[str]) -> str:
    return {
        "small": "до 4,5 м — седаны",
        "medium": "до 5,5 м — внедорожники",
        "large": "до 7 м — Газели",
    }.get(value or "", "не указан")


def mark(flag) -> str:
    return "✓" if flag else "✕"


def status_line(status: str) -> str:
    icon, label = STATUS.get(status, ("•", status))
    return f"{icon} {label}"


def map_url(g: dict) -> Optional[str]:
    address = (g.get("address") or "").strip()
    if not address:
        return None
    district = (g.get("district") or "").strip()
    query = f"{district}, {address}" if district and district.lower() not in address.lower() else address
    if "москв" not in query.lower() and "обл" not in query.lower():
        query = f"Москва, {query}"
    return "https://yandex.ru/maps/?text=" + quote_plus(query)


def location(g: dict) -> str:
    district = (g.get("district") or "").strip()
    address = (g.get("address") or "").strip()
    if district and address:
        return f"{h(district)} · {h(address)}"
    return h(district or address or "—")


def username_label(username: Optional[str], user_id) -> str:
    return f"@{username}" if username else f"id {user_id}"


# ─── Старт ──────────────────────────────────────────────────

START = (
    f"<b>{LOGO}</b>\n"
    "Почасовая аренда боксов в Москве и области\n"
    f"{DIVIDER}\n"
    "◆ <b>Гаражи самообслуживания</b> — подъёмник, яма, инструмент\n"
    "◆ <b>Детейлинг-боксы</b> — мойка и уход своими руками\n\n"
    "<b>Как это работает</b>\n"
    "1 · Выберите бокс и удобные часы\n"
    "2 · Дождитесь подтверждения — пришлём сюда\n"
    "3 · Приезжайте и работайте\n\n"
    "<i>Оплата на месте, напрямую площадке. Без предоплаты.</i>"
)


def start_text(first_name: str = "") -> str:
    """Приветствие после нажатия кнопки запуска — с обращением по имени, если оно есть."""
    name = clip((first_name or "").strip(), 40)
    return (f"Здравствуйте, {h(name)}!\n\n" if name else "") + START


# ─── Профиль бота (видно в пустом чате до нажатия кнопки запуска) ───
# Обычный текст без разметки. Описание — до 512 символов, короткое — до 120.

BOT_DESCRIPTION = (
    "GarageHub — почасовая аренда гаражей самообслуживания и детейлинг-боксов "
    "в Москве и области.\n\n"
    "◆ Гаражи с подъёмником, ямой и инструментом\n"
    "◆ Детейлинг-боксы для мойки и ухода\n"
    "◆ Бронь от одного часа — без предоплаты\n\n"
    "Выберите бокс и удобное время, дождитесь подтверждения и приезжайте. "
    "Оплата на месте, напрямую площадке.\n\n"
    "Нажмите кнопку внизу, чтобы начать."
)

BOT_SHORT_DESCRIPTION = (
    "Почасовая аренда гаражей и детейлинг-боксов в Москве и области. Оплата на месте. "
    f"Связь: {ADMIN_TG}"
)


APP_HINT = (
    "\n\n<b>Приложение</b>\n"
    "Кнопка «Открыть» слева от поля ввода — тот же поиск и бронирование в удобном виде: "
    "фото, расписание, избранное."
)

def your_id(user_id: int) -> str:
    return (
        f"Ваш Telegram ID: <code>{user_id}</code>\n\n"
        "Перешлите это сообщение администратору, если он попросил."
    )


APP_LINK_UPDATED = "Адрес приложения обновился. Открывайте по этой кнопке — старые больше не работают."

APP_OPEN = "Откройте приложение — поиск, расписание и бронирование в удобном виде."


def admin_dashboard(active: int, archived: int, pending: int, today: int, leads: int) -> str:
    def row(label: str, value: str) -> str:
        return f"{label}: <b>{value}</b>"

    lines = [
        f"<b>{LOGO} · Панель управления</b>",
        DIVIDER,
        row("Объектов в каталоге", str(active)) + (f" · в архиве {archived}" if archived else ""),
        row("Ждут решения", str(pending)) + (" ←" if pending else ""),
        row("Броней на сегодня", str(today)),
        row("Заявки владельцев", str(leads)) + (" ←" if leads else ""),
        DIVIDER,
        "<i>/client — посмотреть бота глазами клиента</i>",
    ]
    return "\n".join(lines)


CLIENT_MODE_ON = (
    "<b>Режим клиента</b>\n"
    "Меню как у обычного пользователя. Админ-кнопки скрыты, "
    "но заявки по-прежнему приходят вам.\n\n"
    "Вернуться: /admin"
)

CONTACT = (
    f"<b>{LOGO}</b> · связь с нами\n"
    f"{DIVIDER}\n"
    f"Telegram: {ADMIN_TG}\n"
    f"Телефон: {ADMIN_PHONE}\n\n"
    "<i>Ответим на вопросы по бронированию и площадкам.</i>"
)

SESSION_EXPIRED = "Эта кнопка устарела. Начните заново через меню."
CANCELLED = "Действие отменено."


# ─── Поиск ──────────────────────────────────────────────────

SEARCH_STEP_TITLES = {
    "type": "Что ищете?",
    "district": "Выберите районы. Можно несколько — или пропустите шаг.",
    "lift": "Нужен подъёмник?",
    "pit": "Нужна смотровая яма?",
    "tools": "Нужен инструмент на площадке?",
}

TYPE_FILTER_LABELS = {"garage": "гараж самообслуживания", "detailing": "детейлинг-бокс"}


def search_screen(step: str, step_no: int, total: int, filters: dict, matches: int) -> str:
    chosen = []
    if filters.get("type"):
        chosen.append(f"Тип: {TYPE_FILTER_LABELS[filters['type']]}")
    if filters.get("districts"):
        chosen.append(f"Район: {h(', '.join(filters['districts']))}")
    if filters.get("lift"):
        chosen.append("Подъёмник: нужен")
    if filters.get("pit"):
        chosen.append("Яма: нужна")
    if filters.get("tools"):
        chosen.append("Инструмент: на площадке")

    lines = [
        f"<b>Найти бокс</b>   {bar(step_no, total, total)}  {step_no}/{total}",
        DIVIDER,
    ]
    if chosen:
        lines += chosen + [DIVIDER]
    noun = plural(matches, "бокс", "бокса", "боксов")
    lines.append(f"Подходит: <b>{matches}</b> {noun}" if matches else "Подходит: <b>0</b> — попробуйте смягчить фильтры")
    lines.append("")
    lines.append(SEARCH_STEP_TITLES[step])
    return "\n".join(lines)


def search_summary(filters: dict, matches: int) -> str:
    parts = []
    if filters.get("type"):
        parts.append(TYPE_FILTER_LABELS[filters["type"]])
    if filters.get("districts"):
        parts.append(", ".join(filters["districts"]))
    if filters.get("lift"):
        parts.append("подъёмник")
    if filters.get("pit"):
        parts.append("яма")
    if filters.get("tools"):
        parts.append("инструмент")
    what = h(" · ".join(parts)) if parts else "без фильтров"
    noun = plural(matches, "бокс", "бокса", "боксов")
    return f"<b>Найти бокс</b>\n{DIVIDER}\n{what}\nНайдено: <b>{matches}</b> {noun}"


NOT_FOUND = (
    "<b>Ничего не нашлось</b>\n"
    f"{DIVIDER}\n"
    "По этим фильтрам свободных площадок нет.\n"
    "Попробуйте другой район или уберите часть условий."
)

EMPTY_CATALOG = "Каталог пока пуст. Новые площадки появятся совсем скоро."


# ─── Карточка объекта ───────────────────────────────────────

def features(g: dict) -> str:
    if g.get("type") == "detailing":
        return ""
    return (
        f"Подъёмник {mark(g.get('has_lift'))}  ·  Яма {mark(g.get('has_pit'))}\n"
        f"Инструмент: {tools(g.get('tool_availability'))}\n"
    )


def garage_card(g: dict, *, position: str = "", description_limit: int = 350) -> str:
    """Карточка для клиента. Без контактов владельца."""
    header = f"<b>{h(g.get('name') or '')}</b>"
    if position:
        header += f"   <i>{position}</i>"
    text = (
        f"{header}\n"
        f"<i>{object_type(g.get('type'))}</i>\n"
        f"{DIVIDER}\n"
        f"Цена: <b>{money(g.get('price_per_hour'))}</b> / час\n"
        f"Адрес: {location(g)}\n"
        f"{features(g)}"
        f"Размер: {size(g.get('box_size'))}\n"
    )
    if g.get("working_hours"):
        text += f"Работает: {h(g['working_hours'])}\n"
    if g.get("description"):
        text += f"<blockquote expandable>{h(clip(g['description'], description_limit))}</blockquote>"
    return text.rstrip()


# ─── Бронирование ───────────────────────────────────────────

def booking_header(g: dict) -> str:
    return f"<b>{h(g.get('name') or '')}</b> · {money(g.get('price_per_hour'))} / час\n{DIVIDER}"


def dates_screen(g: dict) -> str:
    return (
        f"{booking_header(g)}\n"
        f"Шаг 1 из 2   {bar(1, 2, 6)}\n\n"
        "Выберите дату. Рядом — сколько часов свободно."
    )


def slots_screen(g: dict, day: date, selected: List[int], price: float) -> str:
    lines = [
        booking_header(g),
        f"Шаг 2 из 2   {bar(2, 2, 6)}",
        f"<b>{day_long(day).capitalize()}</b>",
        "",
    ]
    if selected:
        hours = len(selected)
        lines += [
            f"Выбрано: <b>{span(selected[0], selected[-1] + 1)}</b> · {hours} ч",
            f"Итого: <b>{money(hours * price)}</b>",
        ]
    else:
        lines += [
            "Нажмите на первый и последний час —",
            "всё между ними выберется само.",
        ]
    lines += ["", "<i>✕ — занято или уже прошло</i>"]
    return "\n".join(lines)


def timeline(*steps) -> str:
    """Шкала статуса заявки: ✓ пройдено, ◌ впереди."""
    return "\n".join(f"{'✓' if done else '◌'} {label}" for done, label in steps)


def booking_block(name: str, start: datetime, end: datetime, total) -> str:
    return (
        f"<b>{h(name)}</b>\n"
        f"{day_long(start.date()).capitalize()} · {time_span(start, end)}\n"
        f"{hours_between(start, end)} ч · {money(total)}"
    )


def request_sent(booking_id, name: str, start: datetime, end: datetime, total) -> str:
    return (
        f"<b>Заявка №{booking_id} отправлена</b>\n"
        f"{DIVIDER}\n"
        f"{booking_block(name, start, end, total)}\n"
        f"{DIVIDER}\n"
        f"{timeline((True, 'Заявка отправлена'), (False, 'Подтверждение'), (False, 'Визит'))}\n\n"
        "Как только заявка будет подтверждена, мы напишем сюда.\n"
        "<i>Оплата на месте, напрямую площадке.</i>"
    )


def confirmed_for_client(booking_id, g: dict, start: datetime, end: datetime, total) -> str:
    visit = f"Визит — ждём вас {start.strftime('%d.%m')} в {start.strftime('%H:%M')}"
    return (
        f"<b>Заявка №{booking_id} подтверждена</b>\n"
        f"{DIVIDER}\n"
        f"{booking_block(g.get('name') or '', start, end, total)}\n"
        f"Адрес: {location(g)}\n"
        f"{DIVIDER}\n"
        f"{timeline((True, 'Заявка отправлена'), (True, 'Подтверждена'), (False, visit))}\n\n"
        "<i>Оплата на месте, напрямую площадке.</i>"
    )


def rejected_for_client(booking_id, name: str, start: datetime, end: datetime, reason: str) -> str:
    return (
        f"<b>Заявка №{booking_id} отклонена</b>\n"
        f"{DIVIDER}\n"
        f"<b>{h(name)}</b>\n"
        f"{day_long(start.date()).capitalize()} · {time_span(start, end)}\n"
        f"Причина: {h(reason)}\n\n"
        "Попробуйте выбрать другое время или другую площадку."
    )


def visit_reminder(g: dict, start: datetime, end: datetime, today: date) -> str:
    when = {0: "Сегодня", 1: "Завтра"}.get((start.date() - today).days, day_long(start.date()).capitalize())
    return (
        f"<b>Скоро ваш визит</b>\n"
        f"{DIVIDER}\n"
        f"<b>{h(g.get('name') or '')}</b>\n"
        f"{when} · {time_span(start, end)}\n"
        f"Адрес: {location(g)}\n\n"
        "Ждём вас. Если планы изменились — отмените заявку в «Мои заявки»."
    )


def wait_notice(booking_id) -> str:
    return (
        f"Заявка №{booking_id} ещё на согласовании — ответим в ближайшее время.\n"
        "Если планы изменились, её можно отменить в «Мои заявки»."
    )


SLOT_TAKEN = "Пока вы выбирали, часть этого времени заняли. Выберите другие часы."


# ─── Мои заявки ─────────────────────────────────────────────

NO_BOOKINGS = (
    "<b>Мои заявки</b>\n"
    f"{DIVIDER}\n"
    "Заявок пока нет. Начните с «Найти бокс»."
)


def my_bookings(upcoming: List[tuple], history: List[tuple]) -> str:
    """upcoming / history — списки (booking, garage_name, start, end)."""
    lines = ["<b>Мои заявки</b>", DIVIDER]
    if upcoming:
        lines.append("<b>Предстоящие</b>")
        for b, name, start, end in upcoming:
            icon, _ = STATUS.get(b["status"], ("•", ""))
            lines.append(f"{icon} {day_short(start.date())} · {time_span(start, end)}")
            lines.append(f"      {h(name)}")
    if history:
        if upcoming:
            lines.append("")
        lines.append("<b>История</b>")
        for b, name, start, end in history:
            icon, label = STATUS.get(b["status"], ("•", b["status"]))
            lines.append(f"{icon} {start.strftime('%d.%m')} · {h(clip(name, 24))} · {label}")
    if upcoming:
        lines += ["", "<i>Нажмите на заявку, чтобы открыть или отменить.</i>"]
    return "\n".join(lines)


def my_booking_card(b: dict, g: Optional[dict], start: datetime, end: datetime) -> str:
    name = (g or {}).get("name") or "Площадка недоступна"
    text = (
        f"<b>Заявка №{b['id']}</b> · {status_line(b['status'])}\n"
        f"{DIVIDER}\n"
        f"{booking_block(name, start, end, b['total_price'])}\n"
    )
    if g and b["status"] == "confirmed":
        text += f"Адрес: {location(g)}\n"
    if b["status"] == "rejected" and b.get("admin_comment"):
        text += f"Причина: {h(b['admin_comment'])}\n"
    return text.rstrip()


CANCEL_ASK = "Отменить заявку? Время освободится для других клиентов."
CANCEL_DONE = "Заявка отменена."


# ─── Владельцы ──────────────────────────────────────────────

def owner_step(step: int, question: str) -> str:
    return f"<i>Шаг {step} из 4</i>   {bar(step, 4, 4)}\n{question}"


OWNER_INTRO = (
    "<b>Разместить бокс</b>\n"
    f"{DIVIDER}\n"
    "GarageHub — сервис почасовой аренды гаражей самообслуживания "
    "и детейлинг-боксов в Москве и области.\n\n"
    "◆ Размещение бесплатное\n"
    "◆ Клиенты бронируют по часам, оплата — вам на месте\n"
    "◆ Расписание и заявки ведём мы\n\n"
    "Оставьте заявку — свяжемся, обсудим условия и добавим объект в каталог."
)

OWNER_NAME = "Как к вам обращаться?"
OWNER_PHONE = "Номер телефона для связи:"
OWNER_TYPE = "Что сдаёте?"
OWNER_ADDRESS = "Адрес площадки:"

OWNER_DONE = (
    "<b>Заявка принята</b>\n"
    f"{DIVIDER}\n"
    "Свяжемся с вами в ближайшее время.\n\n"
    f"Если удобнее написать самим: {ADMIN_TG}\n"
    f"Телефон: {ADMIN_PHONE}"
)


def owner_lead(r: dict) -> str:
    return (
        f"<b>Заявка на размещение №{r['id']}</b>\n"
        f"{DIVIDER}\n"
        f"Имя: {h(r.get('name') or '—')}\n"
        f"Телефон: {h(r.get('phone') or '—')}\n"
        f"Тип: {h(r.get('object_type') or '—')}\n"
        f"Адрес: {h(r.get('address') or '—')}\n"
        f"Telegram: {h(r.get('telegram_username') or '—')}"
    )


NO_LEADS = "Новых заявок от владельцев нет."


# ─── Админ: объекты ─────────────────────────────────────────

def objects_list(active: int, archived: int, archive_view: bool = False) -> str:
    if archive_view:
        return (
            f"<b>Архив</b> · {archived}\n{DIVIDER}\n"
            "Скрытые объекты: клиенты их не видят, история заявок сохранена."
        )
    return (
        f"<b>Объекты</b> · в каталоге {active}"
        + (f" · в архиве {archived}" if archived else "")
        + f"\n{DIVIDER}\nВыберите объект."
    )


def admin_object_card(g: dict, upcoming: int = 0) -> str:
    photos = len(g.get("photos") or [])
    state = "в каталоге" if g.get("is_active", True) else "в архиве — клиенты не видят"
    open_h, close_h = g.get("open_hour"), g.get("close_hour")
    slots = span(open_h, close_h) if isinstance(open_h, int) and isinstance(close_h, int) else "—"
    return (
        f"<b>{h(g.get('name') or '')}</b>   <i>№{g['id']}</i>\n"
        f"<i>{object_type(g.get('type'))} · {state}</i>\n"
        f"{DIVIDER}\n"
        f"Район: {h(g.get('district') or '—')}\n"
        f"Адрес: {h(g.get('address') or '—')}\n"
        f"Цена: {money(g.get('price_per_hour'))} / час\n"
        f"{features(g)}"
        f"Размер: {size(g.get('box_size'))}\n"
        f"Работает: {h(g.get('working_hours') or '—')}\n"
        f"Слоты брони: {slots}\n"
        f"Фото: {photos} из 5 · Активных броней: {upcoming}\n"
        f"{DIVIDER}\n"
        f"Владелец: {h(g.get('owner_name') or '—')}\n"
        f"Телефон: {h(g.get('owner_phone') or '—')}\n"
        f"Telegram: {h(g.get('owner_contact') or '—')}"
    )


def delete_ask(g: dict, upcoming: int) -> str:
    text = (
        f"<b>Удалить «{h(g.get('name') or '')}»?</b>\n"
        f"{DIVIDER}\n"
        "Объект пропадёт из поиска. История заявок сохранится, "
        "вернуть объект можно из архива."
    )
    if upcoming:
        text += (
            f"\n\n⚠ Активных броней: <b>{upcoming}</b>. "
            "Клиенты их не потеряют, но решить по ним нужно вам."
        )
    return text


DELETED = "Объект перенесён в архив."
RESTORED = "Объект снова в каталоге."


# ─── Админ: добавление ──────────────────────────────────────

ADD_STEPS = [
    "type", "name", "district", "address", "photos", "price", "lift", "pit",
    "tools", "size", "hours", "description", "owner_name", "owner_phone", "owner_contact",
]

ADD_QUESTIONS = {
    "type": "Тип объекта:",
    "name": "Название площадки (так его увидят клиенты):",
    "district": "Район — например: Марьино, Химки, Одинцово.\nПо нему клиенты фильтруют поиск.",
    "address": "Полный адрес:",
    "photos": "Пришлите до 5 фото — можно альбомом.\nКогда закончите, нажмите «Готово».",
    "price": "Цена за час, только число. Например: 800",
    "lift": "Есть подъёмник?",
    "pit": "Есть смотровая яма?",
    "tools": "Инструмент на площадке:",
    "size": "Размер бокса:",
    "hours": "Часы работы, например: «ежедневно 9:00–22:00» или «круглосуточно».\nИз них бот возьмёт слоты для брони.",
    "description": "Описание для клиентов: что есть на площадке, как заехать, правила.",
    "owner_name": "Имя владельца <i>(видите только вы)</i>:",
    "owner_phone": "Телефон владельца <i>(видите только вы)</i>:",
    "owner_contact": "Telegram владельца <i>(видите только вы)</i>:",
}


def add_step(field: str, steps: List[str]) -> str:
    number = steps.index(field) + 1
    return (
        f"<b>Новый объект</b>   <i>шаг {number} из {len(steps)}</i>\n"
        f"{bar(number, len(steps))}\n\n"
        f"{ADD_QUESTIONS[field]}"
    )


def hours_parsed(open_h: Optional[int], close_h: Optional[int]) -> str:
    if open_h is None:
        return "Не понял часы — для брони будут слоты 09:00–22:00. Поправить можно в редактировании."
    return f"Слоты для брони: {span(open_h, close_h)}"


ADD_PREVIEW = "<b>Так объект увидят клиенты.</b> Публикуем?"


def published(name: str) -> str:
    return f"Объект <b>{h(name)}</b> опубликован."


PHOTOS_COUNTER = "Фото получено: {n} из {max}"
PHOTOS_LIMIT = "Это уже {max} фото — больше не поместится. Нажмите «Готово»."
PHOTO_EXPECTED = "Жду фото. Если фото не будет — нажмите «Готово»."
PRICE_INVALID = "Нужно число больше нуля, например: 800"

# ─── Админ: редактирование ──────────────────────────────────

EDIT_CHOOSE = "Что изменить?"
EDIT_DONE = "Сохранено."

EDIT_PROMPTS = {
    "name": "Новое название",
    "district": "Новый район",
    "address": "Новый адрес",
    "price_per_hour": "Новая цена за час (число)",
    "working_hours": "Новые часы работы",
    "description": "Новое описание",
    "owner_name": "Имя владельца",
    "owner_phone": "Телефон владельца",
    "owner_contact": "Telegram владельца",
}


def edit_prompt(field: str, current) -> str:
    if current in (None, ""):
        shown = "—"
    elif field == "price_per_hour":
        shown = money(current)
    else:
        shown = h(clip(str(current), 300))
    return f"<b>{EDIT_PROMPTS[field]}</b>\nСейчас: {shown}\n\nОтправьте новое значение:"


def photos_edit_intro(count: int) -> str:
    return (
        f"<b>Фото объекта</b> · сейчас {count} из 5\n"
        f"{DIVIDER}\n"
        "Добавить к текущим или заменить все?"
    )


PHOTOS_SEND = "Пришлите фото — можно альбомом. Потом нажмите «Готово»."
PHOTOS_SAVED = "Фото сохранены: {n} из 5."
PHOTOS_UNCHANGED = "Новых фото нет — оставил как было."


# ─── Админ: заявки ──────────────────────────────────────────

NO_PENDING = "Все заявки разобраны — новых нет."


def ago(dt: datetime, current: datetime) -> str:
    minutes = int((current - dt).total_seconds() // 60)
    if minutes < 1:
        return "только что"
    if minutes < 60:
        return f"{minutes} мин назад"
    hours = minutes // 60
    if hours < 24:
        return f"{hours} ч назад"
    return dt.strftime("%d.%m %H:%M")


def admin_booking_card(b: dict, g: Optional[dict], start: datetime, end: datetime,
                       created: Optional[datetime], current: datetime, title: str = "") -> str:
    g = g or {}
    name = g.get("name") or "Объект недоступен"
    client = username_label(b.get("client_username"), b["client_telegram_id"])
    lines = [
        f"<b>{title or 'Заявка'} №{b['id']}</b> · {status_line(b['status'])}",
        DIVIDER,
        f"<b>{h(name)}</b>",
        f"{day_long(start.date()).capitalize()} · {time_span(start, end)} · {hours_between(start, end)} ч",
        f"Сумма: {money(b['total_price'])}",
        f"Клиент: {h(client)}",
    ]
    if created:
        lines.append(f"<i>Создана {ago(created, current)}</i>")
    if b["status"] == "rejected" and b.get("admin_comment"):
        lines.append(f"Причина: {h(b['admin_comment'])}")
    lines += [
        DIVIDER,
        f"Владелец: {h(g.get('owner_name') or '—')}",
        f"Телефон: {h(g.get('owner_phone') or '—')}",
        f"Telegram: {h(g.get('owner_contact') or '—')}",
    ]
    return "\n".join(lines)


def admin_reminder(b: dict, name: str, start: datetime, end: datetime) -> str:
    return (
        f"<b>Напоминание</b> · заявка №{b['id']} ждёт решения\n"
        f"{h(name)} · {day_short(start.date())} {time_span(start, end)}"
    )


def day_schedule(day: date, rows: List[tuple], offset: int) -> str:
    """rows — список (booking, garage_name, start, end)."""
    title = {0: "Сегодня", 1: "Завтра"}.get(offset, day_short(day))
    lines = [f"<b>{title}</b> · {day_long(day)}", DIVIDER]
    if not rows:
        lines.append("Броней нет.")
    for b, name, start, end in rows:
        icon, _ = STATUS.get(b["status"], ("•", ""))
        client = username_label(b.get("client_username"), b["client_telegram_id"])
        lines.append(f"{icon} <b>{time_span(start, end)}</b>  {h(clip(name, 22))}")
        lines.append(f"      {h(client)} · {money(b['total_price'])}")
    if rows:
        total = sum(float(b["total_price"] or 0) for b, *_ in rows if b["status"] == "confirmed")
        lines += [DIVIDER, f"Подтверждено на сумму: <b>{money(total)}</b>"]
    return "\n".join(lines)


def decided(status: str, extra: str = "") -> str:
    return f"\n{DIVIDER}\n<b>Решение:</b> {status_line(status)}" + (f"\nПричина: {h(extra)}" if extra else "")


ALREADY_DECIDED = "Эта заявка уже обработана: {status}."
REJECT_ASK = "Причина отказа — клиент её увидит:"
REJECT_CUSTOM = "Напишите причину отказа одним сообщением:"


def client_cancelled_for_admin(b: dict, name: str, start: datetime, end: datetime) -> str:
    client = username_label(b.get("client_username"), b["client_telegram_id"])
    return (
        f"<b>Клиент отменил заявку №{b['id']}</b>\n"
        f"{DIVIDER}\n"
        f"{h(name)}\n"
        f"{day_long(start.date()).capitalize()} · {time_span(start, end)}\n"
        f"Клиент: {h(client)}"
    )


def error_for_admin(where: str, error: Exception) -> str:
    return (
        "<b>⚠ Ошибка в боте</b>\n"
        f"{DIVIDER}\n"
        f"Где: {h(where)}\n"
        f"<code>{h(clip(f'{type(error).__name__}: {error}', 600))}</code>\n\n"
        "<i>Бот продолжает работать. Подробности — в logs/bot.log.</i>"
    )

def lock_result(command):
    labels = {
        "queued": "Запрос на открытие зарегистрирован.",
        "dispatching": "Команда отправляется. Дождитесь результата.",
        "cloud_accepted": "Команда принята облаком. Подтверждения замка пока нет.",
        "device_confirmed": "Получено подтверждение разблокировки замка.",
        "failed": "Открытие не выполнено. Обратитесь к администратору.",
        "expired": "Время ожидания истекло. Команда не отправлена.",
        "cancelled": "Открытие отменено до отправки команды.",
        "unknown": "Результат неизвестен. Повторная отправка заблокирована. Требуется проверка администратора.",
    }
    text = labels[command["state"]]
    if command.get("simulated"):
        text = "Тестовый замок. " + text
    if command.get("result_code") == "operator_resolution":
        text += " Результат зафиксирован оператором."
    return text
