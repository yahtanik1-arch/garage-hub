"""Настоящий HTTP CONNECT: API и фото через прокси, без сети Telegram и рабочей БД."""
import asyncio
from io import BytesIO
import unittest

from aiogram import Bot
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.client.telegram import TelegramAPIServer
from aiogram.exceptions import TelegramNetworkError
from aiohttp import web
from aiohttp.test_utils import TestServer

TOKEN = "123456:TESTTOKENTESTTOKENTESTTOKEN"


class ProxyTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.calls, self.tunnels, self.tasks = [], [], set()

        async def get_me(request):
            self.calls.append("api")
            return web.json_response({"ok": True, "result": {
                "id": 123456, "is_bot": True, "first_name": "Test", "username": "test_proxy_bot"}})

        async def photo(request):
            self.calls.append("photo")
            return web.Response(body=b"test-photo-bytes")

        app = web.Application()
        app.router.add_post("/bot" + TOKEN + "/getMe", get_me)
        app.router.add_get("/file/bot" + TOKEN + "/photo.jpg", photo)
        self.target = TestServer(app)
        await self.target.start_server()

        async def relay(reader, writer):
            try:
                while True:
                    data = await reader.read(65536)
                    if not data:
                        break
                    writer.write(data)
                    await writer.drain()
            finally:
                writer.close()
                await writer.wait_closed()

        async def tunnel(reader, writer):
            task = asyncio.current_task()
            self.tasks.add(task)
            try:
                header = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), timeout=3)
                # .invalid cannot resolve locally: success proves proxy-side routing.
                expected = "CONNECT telegram-proxy-test.invalid:%s HTTP/1.1" % self.target.port
                self.assertEqual(header.split(b"\r\n", 1)[0].decode(), expected)
                self.tunnels.append("connect")
                remote_reader, remote_writer = await asyncio.open_connection("127.0.0.1", self.target.port)
                writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
                await writer.drain()
                await asyncio.gather(relay(reader, remote_writer), relay(remote_reader, writer))
            finally:
                writer.close()
                self.tasks.discard(task)

        self.proxy = await asyncio.start_server(tunnel, "127.0.0.1", 0)
        self.port = self.proxy.sockets[0].getsockname()[1]

    async def asyncTearDown(self):
        self.proxy.close()
        await self.proxy.wait_closed()
        await asyncio.gather(*list(self.tasks), return_exceptions=True)
        await self.target.close()

    async def test_api_and_photo_use_proxy_with_remote_resolution(self):
        session = AiohttpSession(proxy="http://127.0.0.1:%s" % self.port, api=
            TelegramAPIServer.from_base("http://telegram-proxy-test.invalid:%s" % self.target.port))
        bot = Bot(TOKEN, session=session)
        try:
            self.assertEqual((await bot.get_me()).username, "test_proxy_bot")
            output = BytesIO()
            await bot.download_file("photo.jpg", destination=output)
            self.assertEqual(output.getvalue(), b"test-photo-bytes")
            self.assertEqual(self.calls, ["api", "photo"])
            self.assertTrue(self.tunnels)
        finally:
            await bot.session.close()

    async def test_proxy_failure_does_not_fall_back_to_direct_connection(self):
        self.proxy.close()
        await self.proxy.wait_closed()
        session = AiohttpSession(proxy="http://127.0.0.1:%s" % self.port, timeout=2,
            api=TelegramAPIServer.from_base("http://127.0.0.1:%s" % self.target.port))
        bot = Bot(TOKEN, session=session)
        try:
            with self.assertRaises(TelegramNetworkError):
                await bot.get_me()
            self.assertEqual(self.calls, [])
        finally:
            await bot.session.close()


if __name__ == "__main__":
    unittest.main()
