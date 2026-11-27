"""VK HTTP API and keyboard/media conversion. No third-party VK framework."""
import asyncio
import hashlib
import json
import secrets
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlsplit

import aiohttp
from aiogram.types import FSInputFile, InlineKeyboardMarkup, ReplyKeyboardMarkup

from core.config import ASSETS_DIR, VK_TOKEN, WEBSITE_URL

MAX_IMAGE = 10 * 1024 * 1024


class VKError(RuntimeError):
    def __init__(self, code=0):
        self.code = code
        super().__init__("VK API failed (code %s)" % code)


class PlainText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts, self.links = [], []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            self.links.append(dict(attrs).get("href", ""))
        if tag == "br":
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag == "a" and self.links:
            self.parts.append(" (" + self.links.pop() + ")")

    def handle_data(self, data):
        self.parts.append(data)


def plain(text):
    parser = PlainText()
    parser.feed(text or "")
    return "".join(parser.parts)


def keyboards(markup):
    """VK: at most 4 buttons/row, 6 inline rows. Split long lists without dropping buttons."""
    inline = isinstance(markup, InlineKeyboardMarkup)
    source = markup.inline_keyboard if inline else markup.keyboard if isinstance(markup, ReplyKeyboardMarkup) else []
    rows = []
    for row in source:
        buttons = []
        for button in row:
            if getattr(button, "request_contact", False):
                continue  # VK has no Telegram contact-sharing button; the phone can be typed.
            url = getattr(button, "url", None)
            if getattr(button, "web_app", None):
                url = WEBSITE_URL or None
                if not url:
                    continue
            if url:
                action = {"type": "open_link", "link": url, "label": button.text[:40]}
            elif getattr(button, "callback_data", None):
                action = {"type": "callback", "label": button.text[:40],
                          "payload": json.dumps({"gh": button.callback_data}, ensure_ascii=False)}
            else:
                action = {"type": "text", "label": button.text[:40]}
            buttons.append({"action": action, **({} if url else {"color": "secondary"})})
        rows.extend(buttons[i:i + 4] for i in range(0, len(buttons), 4))
    limit = 6 if inline else 10
    if not rows:
        return [{"inline": inline, "buttons": []}]
    return [{"inline": inline, "buttons": rows[i:i + limit]} for i in range(0, len(rows), limit)]


def trusted_media_url(url):
    try:
        p = urlsplit(url)
        host = (p.hostname or "").lower()
        return (p.scheme == "https" and p.port in (None, 443) and not p.username and not p.password
                and any(host == domain or host.endswith("." + domain)
                        for domain in ("vk.com", "userapi.com", "vkuserphoto.ru", "vkuser.net")))
    except ValueError:
        return False


class VKClient:
    def __init__(self, token=VK_TOKEN, telegram=None):
        self.token, self.telegram = token, telegram
        self.http = None
        self.photos = {}
        self.send_lock = asyncio.Lock()

    async def close(self):
        if self.http:
            await self.http.close()

    def session(self):
        if self.http is None:
            self.http = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30))
        return self.http

    async def api(self, method, **params):
        if not self.token:
            raise VKError()
        try:
            async with self.session().post("https://api.vk.com/method/" + method,
                    data={"access_token": self.token, "v": "5.199", **params}) as response:
                if response.status != 200:
                    raise VKError()
                data = await response.json()
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError):
            raise VKError() from None
        if "error" in data:
            raise VKError(data["error"].get("error_code", 0))
        if "response" not in data:
            raise VKError()
        return data["response"]

    async def download_image(self, url):
        if not trusted_media_url(url):
            raise VKError()
        async with self.session().get(url, allow_redirects=False) as response:
            if response.status != 200 or not response.headers.get("Content-Type", "").startswith("image/"):
                raise VKError()
            chunks, length = [], 0
            async for chunk in response.content.iter_chunked(65536):
                length += len(chunk)
                if length > MAX_IMAGE:
                    raise VKError()
                chunks.append(chunk)
        return b"".join(chunks)

    async def save_incoming_photo(self, photo, key):
        sizes = photo.get("sizes") or []
        valid = [s for s in sizes if isinstance(s, dict) and trusted_media_url(s.get("url", ""))]
        if not valid:
            raise VKError()
        largest = max(valid, key=lambda s: s.get("width", 0) * s.get("height", 0))
        content = await self.download_image(largest["url"])
        # Stable name makes retried events reuse the same asset and prevents arbitrary paths.
        filename = "vk_" + hashlib.sha256(key.encode()).hexdigest() + ".jpg"
        folder = ASSETS_DIR / "garages"
        folder.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread((folder / filename).write_bytes, content)
        return "local:" + filename

    async def photo(self, source, peer_id):
        cache_key = str(getattr(source, "path", source))
        if cache_key in self.photos:
            return self.photos[cache_key]
        if isinstance(source, FSInputFile):
            content = await asyncio.to_thread(Path(source.path).read_bytes)
        elif isinstance(source, str) and source.startswith("local:"):
            from tgbot.media import photo
            return await self.photo(photo(source), peer_id)
        elif isinstance(source, str) and source.startswith("https://"):
            content = await self.download_image(source)
        elif self.telegram is not None:
            file = await self.telegram.get_file(source)
            if file.file_size and file.file_size > MAX_IMAGE:
                raise VKError()
            result = await self.telegram.download_file(file.file_path)
            content = result.getvalue()
        else:
            raise VKError()
        if not content or len(content) > MAX_IMAGE:
            raise VKError()
        upload = await self.api("photos.getMessagesUploadServer", peer_id=peer_id)
        if not trusted_media_url(upload.get("upload_url", "")):
            raise VKError()
        form = aiohttp.FormData()
        form.add_field("photo", content, filename="garage.jpg", content_type="image/jpeg")
        async with self.session().post(upload["upload_url"], data=form, allow_redirects=False) as response:
            if response.status != 200:
                raise VKError()
            uploaded = await response.json(content_type=None)
        saved = await self.api("photos.saveMessagesPhoto", server=uploaded["server"],
                               photo=uploaded["photo"], hash=uploaded["hash"])
        item = saved[0]
        attachment = "photo%s_%s" % (item["owner_id"], item["id"])
        if item.get("access_key"):
            attachment += "_" + item["access_key"]
        # ponytail: bounded process cache; persistent VK media IDs only if uploads become a bottleneck.
        if len(self.photos) >= 256:
            self.photos.clear()
        self.photos[cache_key] = attachment
        return attachment

    async def send(self, user_id, text, markup=None, photo=None, edit_id=None, seed=None):
        body = plain(text)
        markup_parts = keyboards(markup)
        texts = [body[i:i + 4000] for i in range(0, len(body), 4000)] or [""]
        count = max(len(texts), len(markup_parts))
        attachment = await self.photo(photo, user_id) if photo is not None else None
        result_id = edit_id
        for i in range(count):
            params = {"message": texts[i] if i < len(texts) and texts[i] else "Продолжение списка",
                      "dont_parse_links": 1}
            # No markup on a new message must preserve the persistent bottom menu.
            if markup is not None or edit_id is not None:
                keyboard = markup_parts[i] if markup is not None and i < len(markup_parts) else {"inline": True, "buttons": []}
                params["keyboard"] = json.dumps(keyboard, ensure_ascii=False)
            if i == 0 and attachment:
                params["attachment"] = attachment
            # Serialize calls at VK's community rate. Do not retry ambiguous sends with a new random_id.
            async with self.send_lock:
                if edit_id is not None and i == 0:
                    await self.api("messages.edit", peer_id=user_id, conversation_message_id=edit_id, **params)
                else:
                    random_id = (int(hashlib.sha256((str(seed) + ":" + str(i)).encode()).hexdigest()[:8], 16)
                                 & 0x7fffffff) if seed is not None else secrets.randbelow(0x7ffffffe) + 1
                    sent = await self.api("messages.send", peer_ids=str(user_id), random_id=random_id or 1, **params)
                    item = sent[0]
                    if item.get("error") or item.get("peer_id") != user_id:
                        raise VKError()
                    result_id = item["conversation_message_id"] if i == 0 else result_id
                await asyncio.sleep(0.34)
        return result_id
