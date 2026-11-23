"""Real local database: only private capabilities reveal website bookings."""
import asyncio
from datetime import timedelta
import os
from pathlib import Path
import sys
from uuid import uuid4
sys.path.insert(0,str(Path(__file__).resolve().parent))
from local_guard import require_local_database
require_local_database()
os.environ.update(TELEGRAM_BOT_TOKEN='',MINIAPP_DEV='')
sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
from aiohttp.test_utils import TestClient,TestServer
from api.server import build_app
from core import db,config
from core.utils import now
from tools.local_db import sql

async def main():
 g=await db.create_garage({'name':'ТЕСТ Мои брони','price_per_hour':500,'is_active':True,
  'open_hour':9,'close_hour':22,'demo_payment':True,'owner_phone':'PRIVATE_OWNER',
  'arrival_instructions':'PRIVATE_ENTRY_CODE','address':'Тестовый адрес'})
 day=str(now().date()+timedelta(days=1))
 async with TestClient(TestServer(build_app(website=True))) as c:
  async def book(hour,garage=g):
   r=await c.post('/api/public/bookings',headers={'Idempotency-Key':str(uuid4())},json={
    'garage_id':garage['id'],'date':day,'hours':[hour],'name':'SECRET_CLIENT',
    'phone':'+79001112233','consent':True})
   assert r.status==201,await r.text()
   return await r.json()
  async def mine(tokens):
   r=await c.post('/api/public/my-bookings',json={'tokens':tokens})
   assert r.status==200,await r.text()
   return (await r.json())['bookings']
  a,b=await book(10),await book(11)
  assert a['access_token']!=b['access_token']
  assert not (await mine([])) and not (await mine([str(uuid4())]))
  rows=await mine([a['access_token']])
  assert [x['id'] for x in rows]==[a['id']] and 'arrival' not in rows[0]
  assert len(await mine([a['access_token'],b['access_token'],a['access_token']]))==2
  text=str(rows)
  assert 'PRIVATE_OWNER' not in text and 'PRIVATE_ENTRY_CODE' not in text and 'SECRET_CLIENT' not in text and '+79001112233' not in text
  for tokens in ([None],['invalid'],[[]],{},[str(uuid4()) for _ in range(101)]):
   r=await c.post('/api/public/my-bookings',json={'tokens':tokens})
   assert r.status==400,(tokens,r.status)
  r=await c.post('/api/public/my-bookings',json={'tokens':[a['access_token']]},headers={'Origin':'https://evil.test'})
  assert r.status==403
  r=await c.get('/api/public/my-bookings')
  assert r.status==401
  r=await c.get('/api/bookings')
  assert r.status==401
  r=await c.post('/api/public/demo-payment',headers={'Authorization':'Demo '+a['payment']['token']},json={'action':'pay'})
  confirmed=await r.json()
  assert confirmed['status']=='confirmed' and confirmed['access_token']==a['access_token']
  assert 'arrival' in confirmed and not confirmed['arrival']['can_show_route']
  assert 'демонстрационная' in confirmed['arrival']['instructions'].lower()
  assert confirmed['support']['message']=='Поддержка скоро появится'
  r=await c.get('/api/garages')
  public=await r.text()
  assert a['access_token'] not in public and a['payment']['token'] not in public and 'PRIVATE_ENTRY_CODE' not in public
  real=await db.create_garage({'name':'ТЕСТ Инструкция','price_per_hour':600,'is_active':True,
   'open_hour':9,'close_hour':22,'address':'Москва, Тестовая 1','arrival_instructions':'PRIVATE_REAL_ENTRY','owner_phone':'PRIVATE_REAL_OWNER'})
  real_booking=await book(14,real)
  assert 'arrival' not in (await mine([real_booking['access_token']]))[0]
  await db.decide_booking(real_booking['id'],'confirmed',config.ADMIN_ID)
  r=(await mine([real_booking['access_token']]))[0]
  assert r['arrival']['can_show_route'] and r['arrival']['instructions']=='PRIVATE_REAL_ENTRY'
  assert 'PRIVATE_REAL_OWNER' not in str(r)
  sql("UPDATE bookings SET start_time=now()-interval '2 hours',end_time=now()-interval '1 hour' WHERE id=%d"%real_booking['id'])
  r=(await mine([real_booking['access_token']]))[0]
  assert r['is_past'] and 'arrival' not in r
  tg=await db.create_booking({'garage_id':real['id'],'client_telegram_id':900000088,
    'start_time':(now()+timedelta(days=2)).isoformat(),'end_time':(now()+timedelta(days=2,hours=1)).isoformat(),
    'total_price':600,'status':'pending'})
  assert not await mine([tg['web_booking_token']])
 print('PASS My bookings: capability isolation, no phone lookup, privacy, paid details, history, Telegram isolation')

asyncio.run(main())
