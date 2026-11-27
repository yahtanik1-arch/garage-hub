"""Translate messenger I/O; the existing handlers keep ownership of every conversation.

Aiogram is used as an installed dispatcher/FSM, not as a Telegram connection.
VK users are negative internal actor keys. Only VKClient receives their real positive IDs.
"""
import base64
import json
from contextvars import ContextVar
from datetime import datetime, timezone

from aiogram.client.session.base import BaseSession
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import Chat, Message, PhotoSize, Update, User

from core.actors import event_key
from vkbot.client import VKError

send_counter = ContextVar("vk_send_counter", default=0)


def callback_id(event):
    data = {key: event[key] for key in ("event_id", "user_id", "peer_id")}
    return base64.urlsafe_b64encode(json.dumps(data).encode()).decode()


class VKSession(BaseSession):
    platform = "vk"

    def __init__(self, client, group_id):
        super().__init__()
        self.vk, self.group_id = client, group_id
        self.messages = {}

    async def close(self):
        await self.vk.close()
        if self.vk.telegram is not None:
            await self.vk.telegram.session.close()

    async def stream_content(self, *args, **kwargs):
        raise RuntimeError("VK media is handled by VKClient")
        yield b""  # BaseSession requires an async iterator.

    def message(self, bot, mid, actor_id, text, photo=False):
        kwargs = {"message_id": mid, "date": datetime.now(timezone.utc),
                  "chat": Chat(id=actor_id, type="private"),
                  "from_user": User(id=self.group_id, is_bot=True, first_name="GarageHub")}
        if photo:
            kwargs.update(caption=text or "", photo=[PhotoSize(
                file_id="vk-outgoing", file_unique_id="vk-outgoing", width=1, height=1)])
        else:
            kwargs["text"] = text or ""
        return Message(**kwargs).as_(bot)

    async def make_request(self, bot, method, timeout=None):
        name = type(method).__name__
        if name == "GetMe":
            return User(id=self.group_id, is_bot=True, first_name="GarageHub", username="garagehub_vk_bot")
        if name == "AnswerCallbackQuery":
            try:
                data = json.loads(base64.urlsafe_b64decode(method.callback_query_id))
                if method.text:
                    from vkbot.client import plain
                    data["event_data"] = json.dumps({"type": "show_snackbar", "text": plain(method.text)[:90]})
                await self.vk.api("messages.sendMessageEventAnswer", **data)
            except VKError:
                # An expired visual acknowledgement must not cancel the booking action.
                pass
            return True
        actor_id = getattr(method, "chat_id", None)
        if not isinstance(actor_id, int):
            raise RuntimeError("Unsupported VK operation: " + name)
        if actor_id >= 0:
            if self.vk.telegram is None:
                raise RuntimeError("Telegram delivery is not configured")
            return await self.vk.telegram(method)
        user_id = -actor_id
        if name == "DeleteMessage":
            await self.vk.api("messages.delete", peer_id=user_id, cmids=str(method.message_id), delete_for_all=1)
            return True
        if name == "SendMediaGroup":
            result = []
            for media in method.media:
                from aiogram.methods import SendPhoto
                result.append(await self.make_request(bot, SendPhoto(chat_id=actor_id,
                    photo=media.media, caption=media.caption)))
            return result
        if name not in ("SendMessage", "SendPhoto", "EditMessageText", "EditMessageCaption",
                        "EditMessageMedia", "EditMessageReplyMarkup"):
            raise RuntimeError("Unsupported VK operation: " + name)
        old_id = getattr(method, "message_id", None)
        previous = self.messages.get((actor_id, old_id), {})
        photo = getattr(method, "photo", None)
        text = getattr(method, "text", None) or getattr(method, "caption", None)
        if name == "EditMessageMedia":
            photo, text = method.media.media, method.media.caption
        if name == "EditMessageReplyMarkup":
            text = previous.get("text")
            if text is None:
                found = await self.vk.api("messages.getByConversationMessageId", peer_id=user_id,
                                         conversation_message_ids=str(old_id))
                text = found["items"][0].get("text", "")
        counter = send_counter.get()
        send_counter.set(counter + 1)
        seed = "%s:send:%s" % (event_key.get(), counter) if event_key.get() else None
        try:
            mid = await self.vk.send(user_id, text, getattr(method, "reply_markup", None), photo,
                                     edit_id=old_id, seed=seed)
        except VKError as error:
            raise TelegramBadRequest(method=method, message=str(error)) from None
        if len(self.messages) > 512:
            self.messages.clear()
        self.messages[(actor_id, mid)] = {"text": text}
        return self.message(bot, mid, actor_id, text, photo is not None or bool(previous.get("photo")))


def event_update(bot, event, event_row_id, saved_photo=None):
    """Only private, incoming messages are accepted. Payload never supplies the actor identity."""
    kind, obj = event["type"], event["object"]
    message = obj["message"] if kind == "message_new" else obj
    uid = message["from_id"] if kind == "message_new" else message["user_id"]
    user = {"id": -uid, "is_bot": False, "first_name": "Клиент ВК"}
    common = {"date": int(datetime.now(timezone.utc).timestamp()),
              "chat": {"id": -uid, "type": "private"}}
    if kind == "message_event":
        data = obj["payload"]["gh"]
        return Update.model_validate({"update_id": event_row_id, "callback_query": {
            "id": callback_id(obj), "from": user, "chat_instance": str(uid), "data": data,
            "message": {**common, "message_id": obj["conversation_message_id"],
                        "from": {"id": bot.id, "is_bot": True, "first_name": "GarageHub"}, "text": ""},
        }}, context={"bot": bot})
    text = message.get("text", "")
    if text.strip().lower() in ("начать", "start", "меню", "/menu") or message.get("payload") == '{"command":"start"}':
        text = "/start"
    # Reply buttons with payloads remain normal text; callbacks only come from message_event.
    msg = {**common, "message_id": message.get("conversation_message_id") or message["id"],
           "from": user, "text": text[:4096]}
    if text.startswith("/"):
        msg["entities"] = [{"type": "bot_command", "offset": 0, "length": len(text.split()[0])}]
    if saved_photo:
        msg.pop("text", None)
        msg["photo"] = [{"file_id": saved_photo, "file_unique_id": saved_photo, "width": 1, "height": 1}]
    return Update.model_validate({"update_id": event_row_id, "message": msg}, context={"bot": bot})
