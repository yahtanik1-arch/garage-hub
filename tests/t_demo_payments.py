"""Isolated real PostgreSQL: demo checkout, no gateway, money or Telegram."""
import asyncio
from datetime import timedelta
import os
from pathlib import Path
import sys
from uuid import uuid4
sys.path.insert(0,str(Path(__file__).resolve().parent))
from local_guard import require_local_database
require_local_database()
os.environ['MINIAPP_DEV']=''
os.environ['TELEGRAM_BOT_TOKEN']=''
sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
from aiohttp.test_utils import TestClient, TestServer
from api.server import build_app
from core import db
from core.utils import now, at_hour
from tools.local_db import sql

async def main():
    g=await db.create_garage({'name':'ТЕСТ Demo payment','price_per_hour':500,'is_active':True,
        'open_hour':9,'close_hour':22,'demo_payment':True})
    tomorrow=now().date()+timedelta(days=1)
    async with TestClient(TestServer(build_app(website=True))) as c:
        async def book(hour, key=None):
            r=await c.post('/api/public/bookings',json={'garage_id':g['id'], 'date':str(tomorrow),
                'hours':[hour],'name':'Тест оплаты','phone':'+70000000000','consent':True},
                headers={'Idempotency-Key':key or str(uuid4())})
            return r.status,await r.json()
        async def payment(b,action=None):
            headers={'Authorization':'Demo '+b['payment']['token']}
            r=await (c.post('/api/public/demo-payment',headers=headers,json={'action':action}) if action
                     else c.get('/api/public/demo-payment',headers=headers))
            return r.status,await r.json()
        key=str(uuid4())
        code,b=await book(10,key)
        assert code==201 and b['status']=='pending' and b['payment']['status']=='awaiting',b
        assert b['total']==500
        code,again=await book(10,key)
        assert again['id']==b['id'] and again['payment']['token']==b['payment']['token']
        assert (await book(10))[0]==409
        code,read=await payment(b)
        assert code==200 and read['status']=='pending'
        rows=await asyncio.gather(*(payment(b,'pay') for _ in range(8)))
        assert all(code==200 and x['status']=='confirmed' and x['payment']['status']=='paid' for code,x in rows),rows
        audit=await db._run(db.supabase.table('audit_log').select('*').eq('booking_id',b['id']).eq('action','demo_payment_paid'))
        assert len(audit)==1
        assert not await db._run(db.supabase.table('access_grants').select('*').eq('booking_id',b['id']))
        assert (await payment(b,'cancel'))[1]['status']=='confirmed'
        assert (await book(10))[0]==409
        code,cancel=await book(11)
        assert code==201
        assert (await payment(cancel,'cancel'))[1]['status']=='cancelled'
        assert (await payment(cancel,'pay'))[1]['status']=='cancelled'
        code,released=await book(11)
        assert code==201
        await payment(released,'cancel')
        code,expired=await book(12)
        sql('UPDATE bookings SET demo_payment_expires_at=now()-interval \'1 second\' WHERE id=%d'%expired['id'])
        assert (await payment(expired,'pay'))[1]['payment']['status']=='expired'
        code,lazy=await book(13)
        sql('UPDATE bookings SET demo_payment_expires_at=now()-interval \'1 second\' WHERE id=%d'%lazy['id'])
        await db.busy_bookings(g['id'],at_hour(tomorrow,9),at_hour(tomorrow,22))
        assert (await db.booking(lazy['id']))['status']=='cancelled'
        assert (await book(13))[0]==201
        for headers in ({},{'Authorization':'Demo '+str(uuid4())},{'Authorization':'Demo nope'}):
            assert (await c.get('/api/public/demo-payment',headers=headers)).status==404
        r=await c.post('/api/public/demo-payment',headers={'Authorization':'Demo '+b['payment']['token']},json={'action':'refund'})
        assert r.status==400
        r=await c.post('/api/public/demo-payment',headers={'Authorization':'Demo '+b['payment']['token'],'Origin':'https://evil.test'},json={'action':'pay'})
        assert r.status==403
        r=await c.get('/api/garages')
        assert b['payment']['token'] not in await r.text()
        # A real/unflagged box cannot be confirmed by this demo API.
        await db.update_garage(g['id'],{'demo_payment':False})
        assert (await payment(b,'pay'))[0]==404
        code,normal=await book(15)
        assert code==201 and 'payment' not in normal and normal['status']=='pending'
    print('PASS demo payments: hold, retry, concurrent pay, cancel, expiry, no grants, no real-box confirmation')

asyncio.run(main())
