"""Local listing images work without Telegram; paths stay inside the image directory."""
import asyncio
import os
from pathlib import Path
import sys
from unittest.mock import AsyncMock, patch
os.environ.update(POSTGREST_URL='http://127.0.0.1:3000', SUPABASE_SECRET_KEY='test-key',
                  APP_ENV='production', LOCK_PROVIDER='disabled', MINIAPP_DEV='', TELEGRAM_PROXY_URL='')
sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from api import photos

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

asyncio.run(main())
