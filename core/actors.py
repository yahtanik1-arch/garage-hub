"""Internal actor keys: Telegram > 0, VK < 0. External IDs stay in separate columns.

Only trusted messenger adapters produce these keys; never accept them from client JSON.
This is not an account link: the same person on two platforms has two accounts.
"""
from contextvars import ContextVar

event_key = ContextVar("messenger_event_key", default=None)


def booking_actor(booking):
    return booking.get("client_telegram_id") or (-booking["client_vk_id"] if booking.get("client_vk_id") else None)


def booking_identity(actor_id):
    return {"client_telegram_id": actor_id if actor_id and actor_id > 0 else None,
            "client_vk_id": -actor_id} if actor_id and actor_id < 0 else {"client_telegram_id": actor_id}


def admin_actor_ids():
    from core.config import ADMIN_IDS, VK_ADMIN_IDS
    return [actor for actor in ADMIN_IDS if actor > 0] + [-uid for uid in VK_ADMIN_IDS]
