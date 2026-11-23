"""Local listing images work without Telegram; paths stay inside the image directory."""
import asyncio
import os
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
os.environ.update(POSTGREST_URL='http://127.0.0.1:3000', SUPABASE_SECRET_KEY='test-key',
                  APP_ENV='production', LOCK_PROVIDER='disabled', MINIAPP_DEV='', TELEGRAM_PROXY_URL='')
sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from api import photos
from aiogram.types import FSInputFile
from tgbot import media
from tgbot.callbacks import CarouselCb, ObjCb, PhotoEditCb
from tgbot.handlers import admin_objects, search


async def check_bot_photos():
    local = 'local:test-overview-v1.jpg'
    upload = media.photo(local)
    assert isinstance(upload, FSInputFile)
    chunks = [chunk async for chunk in upload.read(None)]
    assert b''.join(chunks) == (photos.ASSETS_DIR/'garages'/'test-overview-v1.jpg').read_bytes()
    for invalid in ('local:../placeholder.png', 'local:/etc/passwd', 'local:missing.jpg', 'local:..%2Fsecret.jpg'):
        fallback = media.photo(invalid)
        assert isinstance(fallback, FSInputFile) and Path(fallback.path) == photos.ASSETS_DIR/'placeholder.png'

    def check(source, actual):
        if source is None or source.startswith('local:'):
            assert isinstance(actual, FSInputFile), 'Telegram must receive an upload, not a local: URL'
        else:
            assert actual == source, 'Existing Telegram file IDs and HTTP URLs must still work'

    for sources in ([local, 'local:test-workbench-v1.jpg'], ['TELEGRAM_FILE_ID', local],
                    ['https://example.com/photo.jpg', 'TELEGRAM_FILE_ID'], [local], []):
        g = {'id':1, 'name':'Test', 'type':'garage', 'price_per_hour':500, 'photos':sources}
        message = SimpleNamespace(photo=None, edit_text=AsyncMock(), answer_photo=AsyncMock(return_value=None),
                                  answer_media_group=AsyncMock(), edit_media=AsyncMock(return_value=True))
        callback = SimpleNamespace(message=message, answer=AsyncMock())
        state = AsyncMock()
        state.get_data.return_value = {'results':[g]}
        with patch.object(admin_objects.db, 'garage', AsyncMock(return_value=g)):
            await admin_objects.on_object(callback, ObjCb(action='preview', garage_id=1), state)
            check(sources[0] if sources else None, message.answer_photo.call_args.kwargs['photo'])
            await search.show_results(message, state, {'garages':[g], 'filters':{}})
            check(sources[0] if sources else None, message.answer_photo.call_args.kwargs['photo'])
            await search.on_carousel(callback, CarouselCb(action='page', index=0), state)
            check(sources[0] if sources else None, message.edit_media.call_args.kwargs['media'].media)
            await search.on_carousel(callback, CarouselCb(action='photos', index=0), state)
            if len(sources)>1:
                for source, item in zip(sources, message.answer_media_group.call_args.args[0]):
                    check(source, item.media)
            message.answer_photo.reset_mock()
            message.answer_media_group.reset_mock()
            await admin_objects.on_photos(callback, PhotoEditCb(action='menu', garage_id=1), state)
            if len(sources)==1:
                check(sources[0], message.answer_photo.call_args.kwargs['photo'])
                message.answer_media_group.assert_not_awaited()
            elif sources:
                for source, item in zip(sources, message.answer_media_group.call_args.args[0]):
                    check(source, item.media)
            else:
                message.answer_photo.assert_not_awaited()
                message.answer_media_group.assert_not_awaited()
    print('PASS: bot preview, search, carousel, client/admin albums, single photo, uploads and unsafe paths')

async def main():
    app=web.Application()
    app['bot']=None
    app.router.add_get('/api/photo/{file_id}',photos.photo)
    async with TestClient(TestServer(app)) as c:
        with patch.object(photos,'is_known',AsyncMock(return_value=True)):
            for name in ('test-overview-v1.jpg','test-workbench-v1.jpg'):
                r=await c.get('/api/photo/local:'+name)
                assert r.status==200 and r.content_type=='image/jpeg'
                assert await r.read()==(photos.ASSETS_DIR/'garages'/name).read_bytes()
                assert 'immutable' in r.headers['Cache-Control']
            for name in ('.env','missing.jpg','..%2Fplaceholder.png'):
                r=await c.get('/api/photo/local:'+name)
                assert r.status==404
        with patch.object(photos,'is_known',AsyncMock(return_value=False)):
            assert (await c.get('/api/photo/local:test-overview-v1.jpg')).status==404
    print('PASS: local images, MIME, cache, missing/unknown IDs, path traversal')
    await check_bot_photos()

asyncio.run(main())
