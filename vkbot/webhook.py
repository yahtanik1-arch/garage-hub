"""Authenticated Callback API -> durable inbox -> shared conversation handlers.

One active VK consumer per community, like the existing single Telegram/FSM process.
HTTP acknowledges storage, not completion. No outbound VK calls inside the webhook request.
"""
import asyncio
import hmac
import logging
import re
from uuid import NAMESPACE_URL, uuid5

from aiohttp import web

from core import db
from core.actors import event_key
from core.utils import is_admin, parse_dt
from vkbot.transport import event_update, send_counter

log = logging.getLogger(__name__)
WEBHOOK_PATH = "/vk/callback"


def validate_config(group_id, token, secret, confirmation):
    if group_id <= 0 or not token or not confirmation or not re.fullmatch(r"[A-Za-z0-9_-]{16,128}", secret):
        raise ValueError("Заполните VK_GROUP_ID, VK_GROUP_TOKEN, VK_CONFIRMATION_CODE и VK_CALLBACK_SECRET (16–128 A-Z/a-z/0-9/_/-)")


def private_event(data):
    obj = data.get("object")
    if not isinstance(obj, dict):
        raise ValueError()
    kind = data["type"]
    msg = obj.get("message") if kind == "message_new" else obj
    if not isinstance(msg, dict):
        raise ValueError()
    uid = msg.get("from_id") if kind == "message_new" else msg.get("user_id")
    if type(uid) is not int or uid <= 0 or msg.get("peer_id") != uid or type(msg.get("peer_id")) is not int:
        return False  # No groups, communities, outgoing messages or impersonated payload IDs.
    if kind == "message_new":
        if msg.get("out", 0):
            return False
        if type(msg.get("id")) is not int or not isinstance(msg.get("text", ""), str):
            raise ValueError()
        attachments = msg.get("attachments", [])
        if not isinstance(attachments, list) or len(attachments) > 10:
            raise ValueError()
        if any(not isinstance(item, dict) for item in attachments):
            raise ValueError()
    else:
        payload = msg.get("payload")
        if (not isinstance(payload, dict) or not isinstance(payload.get("gh"), str)
                or not 1 <= len(payload["gh"]) <= 64
                or not isinstance(msg.get("event_id"), str)
                or type(msg.get("conversation_message_id")) is not int):
            raise ValueError()
    return True


class VKWebhook:
    def __init__(self, bot, dispatcher, group_id, secret, confirmation):
        self.bot, self.dispatcher, self.group_id = bot, dispatcher, group_id
        self.secret, self.confirmation = secret.encode(), confirmation
        self.stopped = asyncio.Event()
        self.wake = asyncio.Event()

    async def handle(self, request):
        try:
            data = await request.json()
        except (ValueError, TypeError):
            raise web.HTTPBadRequest()
        if not isinstance(data, dict) or not isinstance(data.get("secret"), str):
            raise web.HTTPForbidden()
        if (type(data.get("group_id")) is not int or data["group_id"] != self.group_id
                or not hmac.compare_digest(data["secret"].encode(), self.secret)):
            raise web.HTTPForbidden()
        kind = data.get("type")
        if kind == "confirmation":
            return web.Response(text=self.confirmation)
        if kind not in ("message_new", "message_event"):
            return web.Response(text="ok")
        event_id = data.get("event_id")
        if not isinstance(event_id, str) or not 1 <= len(event_id) <= 200:
            raise web.HTTPBadRequest()
        try:
            accepted = private_event(data)
        except (ValueError, KeyError):
            raise web.HTTPBadRequest()
        if not accepted:
            return web.Response(text="ok")
        # Never persist the callback secret or arbitrary unneeded top-level fields.
        await db.vk_event_save(self.group_id, event_id, {"type": kind, "object": data["object"]})
        self.wake.set()
        return web.Response(text="ok")

    async def process(self, row):
        key = str(uuid5(NAMESPACE_URL, "garagehub:vk:%s:%s" % (row["group_id"], row["event_id"])))
        context = event_key.set(key)
        counter = send_counter.set(0)
        try:
            # A booking transaction may have committed before an outgoing API response was lost.
            existing = await db.booking_by_vk_request(key)
            event = row["payload"]
            obj = event["object"]
            uid = obj["message"]["from_id"] if event["type"] == "message_new" else obj["user_id"]
            if existing:
                from tgbot import texts, keyboards
                g = await db.garage(existing["garage_id"]) or {}
                await self.bot.send_message(-uid, texts.my_booking_card(existing, g,
                    parse_dt(existing["start_time"]), parse_dt(existing["end_time"])),
                    reply_markup=keyboards.my_bookings_link())
                return
            photos = []
            if event["type"] == "message_new" and is_admin(-uid):
                photos = [a["photo"] for a in obj["message"].get("attachments", [])
                          if a.get("type") == "photo" and isinstance(a.get("photo"), dict)][:5]
            if photos:
                for i, photo in enumerate(photos):
                    saved = await self.bot.session.vk.save_incoming_photo(photo, key + ":" + str(i))
                    await self.dispatcher.feed_update(self.bot, event_update(self.bot, event, row["id"], saved))
            else:
                await self.dispatcher.feed_update(self.bot, event_update(self.bot, event, row["id"]))
        finally:
            event_key.reset(context)
            send_counter.reset(counter)

    async def run(self):
        while not self.stopped.is_set():
            self.wake.clear()
            try:
                row = await db.vk_event_claim(self.group_id)
                if row:
                    try:
                        await self.process(row)
                    except Exception:
                        # Never log event text, tokens, URLs or provider response bodies.
                        log.warning("vk_event_failed id=%s attempt=%s", row["id"], row["attempts"])
                        await db.vk_event_finish(row, False)
                    else:
                        await db.vk_event_finish(row, True)
                    continue
            except Exception:
                log.warning("vk_inbox_unavailable; event retained")
            try:
                await asyncio.wait_for(self.wake.wait(), timeout=1)
            except asyncio.TimeoutError:
                pass
