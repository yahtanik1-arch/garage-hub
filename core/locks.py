"""Один сервис доступа для Telegram, Mini App и оператора. Сеть замка здесь не вызывается."""
import re
from uuid import UUID

from postgrest.exceptions import APIError

from core import db
from core.config import APP_ENV, LOCK_PROVIDER, MANUAL_UNLOCK_ADMIN_IDS, ADMIN_IDS
from core.service import ServiceError
from core.utils import now, parse_dt
from tgbot import texts as t

KEY = re.compile(r"^[A-Za-z0-9_-]{8,128}$")
TERMINAL = {"device_confirmed", "failed", "expired", "cancelled"}


def uuid_value(value):
    try:
        return str(UUID(str(value)))
    except (ValueError, TypeError, AttributeError):
        raise ServiceError("bad_id", "Некорректный идентификатор.")


def allow_simulator():
    return APP_ENV == "local" and LOCK_PROVIDER == "simulator"


def require_operator(user_id):
    if user_id not in ADMIN_IDS or user_id not in MANUAL_UNLOCK_ADMIN_IDS:
        raise ServiceError("forbidden", "Нет права на ручное управление замком.", 403)


def database_error(error):
    message = error.message or ""
    codes = {
        "idempotency_conflict": ("Ключ уже использован для другого действия.", 409),
        "device_busy": ("Предыдущая команда ещё не завершена. Проверьте её результат.", 409),
        "rate_limited": ("Подождите несколько секунд перед следующим открытием.", 429),
        "device_in_use": ("Замок связан с действующими правами или командами.", 409),
        "not_found": ("Бронь или замок не найдены.", 404),
        "access_denied": ("Открытие доступно только во время подтверждённой брони.", 403),
        "action_expired": ("Кнопка устарела. Откройте свою бронь заново.", 403),
        "device_unavailable": ("Замок сейчас недоступен. Свяжитесь с администратором.", 409),
    }
    for code, (text, status) in codes.items():
        if code in message:
            return ServiceError(code, text, status)
    if error.code == "23505":
        return ServiceError("idempotency_conflict", "Ключ уже использован для другого действия.", 409)
    return ServiceError("lock_error", "Не удалось выполнить действие с замком.", 400 if error.code == "22023" else 503)


async def action_for_booking(booking_id, user_id):
    if LOCK_PROVIDER == "disabled":
        return None
    return await db.issue_unlock_action(int(booking_id), user_id)


async def request_open(user_id, booking_id, action_key, source="miniapp"):
    if source not in ("bot", "miniapp"):
        raise ServiceError("bad_source", "Некорректное действие.")
    if not isinstance(action_key, str) or not KEY.fullmatch(action_key):
        raise ServiceError("bad_key", "Нужен корректный ключ действия.")
    if LOCK_PROVIDER == "disabled":
        raise ServiceError("device_unavailable", "Дистанционное открытие ещё не подключено.", 409)
    try:
        return await db.register_unlock(p_actor_id=user_id, p_booking_id=int(booking_id),
            p_action_key=action_key, p_source=source, p_allow_simulator=allow_simulator())
    except APIError as error:
        raise database_error(error) from None


async def command_for_user(command_id, user_id, is_admin=False):
    c = await db.lock_command(uuid_value(command_id))
    if not c or (c["actor_id"] != user_id and not is_admin):
        raise ServiceError("not_found", "Команда не найдена.", 404)
    return c


def command_json(c):
    return {
        "id": c["id"], "booking_id": c.get("booking_id"), "state": c["state"],
        "created_at": c["created_at"], "updated_at": c["updated_at"],
        "simulated": c["simulated"], "message": t.lock_result(c), "terminal": c["state"] in TERMINAL,
    }


def reason_value(reason):
    if not isinstance(reason, str) or not 3 <= len(reason.strip()) <= 300:
        raise ServiceError("reason_required", "Укажите причину: от 3 до 300 символов.")
    return reason.strip()


async def manual_open(user_id, device_id, key, reason):
    require_operator(user_id)
    if LOCK_PROVIDER == "disabled":
        raise ServiceError("device_unavailable", "Дистанционное открытие ещё не подключено.", 409)
    if not isinstance(key, str) or not KEY.fullmatch(key):
        raise ServiceError("bad_key", "Нужен корректный ключ действия.")
    try:
        return await db.register_unlock(p_actor_id=user_id, p_device_id=uuid_value(device_id),
            p_action_key=key, p_source="admin", p_reason=reason_value(reason), p_allow_simulator=allow_simulator())
    except APIError as error:
        raise database_error(error) from None


async def resolve_unknown(user_id, command_id, state, reason):
    require_operator(user_id)
    if state not in ("device_confirmed", "failed"):
        raise ServiceError("bad_state", "Выберите подтверждённый результат.")
    c = await db.resolve_lock_command(p_actor_id=user_id, p_command_id=uuid_value(command_id),
        p_state=state, p_reason=reason_value(reason))
    if not c:
        raise ServiceError("changed", "Команда уже разрешена или не найдена.", 409)
    return c


async def configure_device(user_id, garage_id, provider, provider_device_id, enabled, reason):
    require_operator(user_id)
    if provider not in ("tuya", "simulator") or (provider == "simulator" and not allow_simulator()):
        raise ServiceError("bad_provider", "Этот тип устройства сейчас недоступен.")
    if provider == "tuya" and enabled:
        raise ServiceError("adapter_missing", "Сначала требуется подключить и проверить адаптер Tuya.", 409)
    if not isinstance(provider_device_id, str) or not 1 <= len(provider_device_id.strip()) <= 128:
        raise ServiceError("bad_device", "Проверьте идентификатор устройства.")
    if type(enabled) is not bool:
        raise ServiceError("bad_enabled", "Некорректный признак включения.")
    try:
        return await db.set_lock_device(p_actor_id=user_id, p_garage_id=int(garage_id),
            p_provider=provider, p_provider_device_id=provider_device_id.strip(), p_enabled=enabled,
            p_reason=reason_value(reason))
    except APIError as error:
        raise database_error(error) from None


async def devices_overview():
    devices = await db.lock_devices()
    for d in devices:
        d["stale"] = not d["observed_at"] or (now() - parse_dt(d["observed_at"])).total_seconds() > 120
        d["commands"] = [command_json(c) for c in await db.device_commands(d["id"])]
    return devices
