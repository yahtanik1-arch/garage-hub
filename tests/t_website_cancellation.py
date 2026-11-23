"""Real isolated DB: private website cancellation, idempotency and payment races."""
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
from core import db
from core.utils import now

async def main():
 g=await db.create_garage({'name':'ТЕСТ Отмена','price_per_hour':500,'is_active':True,'open_hour':9,'close_hour':22,'demo_payment':True})
 day=str(now().date()+timedelta(days=1))
 async with TestClient(TestServer(build_app(website=True))) as c:
  async def book(hour):
   r=await c.post('/api/public/bookings',headers={'Idempotency-Key':str(uuid4()),'X-Real-IP':'203.0.113.77'},json={
    'garage_id':g['id'],'date':day,'hours':[hour],'name':'ТЕСТ отмены','phone':'+79002221100','consent':True})
   b=await r.json();assert r.status==201,b
   return b
  async def cancel(b):
   r=await c.post('/api/public/cancel-booking',headers={'Authorization':'Booking '+b['access_token']},json={'confirm':True})
   return r.status,await r.json()
  async def pay(b):
   r=await c.post('/api/public/demo-payment',headers={'Authorization':'Demo '+b['payment']['token']},json={'action':'pay'})
   return r.status,await r.json()
  pending=await book(10)
  assert pending['can_cancel']
  code,b=await cancel(pending)
  assert code==200 and b['status']=='cancelled' and b['payment']['status']=='cancelled' and not b['can_cancel']
  assert (await pay(pending))[1]['status']=='cancelled'
  # The time can immediately be booked again.
  assert (await book(10))['id']!=pending['id']
  paid=await book(11)
  assert (await pay(paid))[1]['status']=='confirmed'
  results=await asyncio.gather(*(cancel(paid) for _ in range(8)))
  assert all(code==200 and b['status']=='cancelled' for code,b in results)
  assert all(b['payment']['status']=='paid' for _,b in results) # retain payment history, no fake refund
  audit=await db._run(db.supabase.table('audit_log').select('*').eq('booking_id',paid['id']).eq('action','booking_cancelled'))
  assert len(audit)==1 and audit[0]['reason']=='website_client'
  assert not await db._run(db.supabase.table('access_grants').select('*').eq('booking_id',paid['id']))
  active=await book(12)
  for header in ('','Booking nope','Booking '+str(uuid4()),'Booking '+active['payment']['token']):
   r=await c.post('/api/public/cancel-booking',headers={'Authorization':header},json={'confirm':True})
   assert r.status==404
  r=await c.post('/api/public/cancel-booking',headers={'Authorization':'Booking '+active['access_token']},json={'confirm':False})
  assert r.status==400
  r=await c.post('/api/public/cancel-booking',headers={'Authorization':'Booking '+active['access_token'],'Origin':'https://evil.test'},json={'confirm':True})
  assert r.status==403 and (await db.booking(active['id']))['status']=='pending'
  # Guessing IDs or supplying another ID in the body does not change token scope.
  r=await c.post('/api/public/cancel-booking',headers={'Authorization':'Booking '+pending['access_token']},json={'confirm':True,'booking_id':active['id']})
  assert r.status==200 and (await db.booking(active['id']))['status']=='pending'
  r=await c.get('/api/public/cancel-booking');assert r.status==401
  for hour in (15,16,17):
   race=await book(hour)
   results=await asyncio.gather(pay(race),cancel(race))
   assert all(code==200 for code,_ in results),results
   assert (await db.booking(race['id']))['status']=='cancelled'
  ordinary=await db.create_garage({'name':'ТЕСТ Начавшаяся бронь','price_per_hour':500,'is_active':True})
  for started,ended in ((True,False),(True,True)):
   data={'garage_id':ordinary['id'],'client_telegram_id':None,'start_time':(now()-timedelta(hours=3 if ended else 1)).isoformat(),
    'end_time':(now()-timedelta(hours=2) if ended else now()+timedelta(hours=1)).isoformat(),
    'total_price':500,'status':'confirmed','web_client_name':'ТЕСТ','web_client_phone':'+79001112233',
    'web_request_id':str(uuid4()),'web_client_hash':'test'}
   existing=await db.create_booking(data)
   code,response=await cancel({'access_token':existing['web_booking_token']})
   assert code==409 and response['error']['code']=='cancellation_closed'
   assert (await db.booking(existing['id']))['status']=='confirmed'
  tg=await db.create_booking({'garage_id':ordinary['id'],'client_telegram_id':900000088,
    'start_time':(now()+timedelta(days=2)).isoformat(),'end_time':(now()+timedelta(days=2,hours=1)).isoformat(),
    'total_price':500,'status':'pending'})
  assert (await cancel({'access_token':tg['web_booking_token']}))[0]==404
 print('PASS website cancellation: own token only, unpaid/paid, retries, freed time, payment race, deadline, Telegram isolation')

asyncio.run(main())
