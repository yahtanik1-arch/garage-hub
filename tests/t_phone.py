"""Russian website phone validation; no DB or Telegram calls."""
import os
import sys
from pathlib import Path
os.environ.update(POSTGREST_URL='http://127.0.0.1:3000',SUPABASE_SECRET_KEY='test-key',
                  APP_ENV='production',LOCK_PROVIDER='disabled',MINIAPP_DEV='',TELEGRAM_PROXY_URL='')
sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
from api.website import russian_phone
from core.service import ServiceError
for value in ('7777777777','777-777-77-77','87777777777','77777777777','+7-777-777-77-77','+7 (777) 777-77-77'):
    assert russian_phone(value)=='+77777777777',value
for value in ('+787879676767878679867967','+15551234567','+87777777777','777777777','123456789012','abc','+7abc7777777777',None,7777777777,'+1234567890','++77777777777'):
    try:russian_phone(value)
    except ServiceError as e:assert e.code=='bad_phone'
    else:raise AssertionError(value)
print('PASS Russian phone: normalization, length, country, invalid characters')
