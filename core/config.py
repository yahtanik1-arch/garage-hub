import os
import re
from pathlib import Path
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

import httpx
from dotenv import load_dotenv
from supabase import ClientOptions, create_client

BASE_DIR = Path(__file__).resolve().parent.parent   # корень проекта (config.py лежит в core/)
load_dotenv(BASE_DIR / ".env")

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_PROXY_URL = os.getenv("TELEGRAM_PROXY_URL", "").strip()
if TELEGRAM_PROXY_URL:
    try:
        proxy_endpoint = urlsplit(TELEGRAM_PROXY_URL)
        proxy_valid = (proxy_endpoint.scheme in ("socks5", "http") and proxy_endpoint.hostname
            and proxy_endpoint.port is not None and proxy_endpoint.port > 0
            and proxy_endpoint.path in ("", "/")
            and not proxy_endpoint.query and not proxy_endpoint.fragment)
    except ValueError:
        proxy_valid = False
    if not proxy_valid:
        # В URL могут быть логин/пароль: не включать его в исключение.
        raise RuntimeError("TELEGRAM_PROXY_URL: требуется socks5://host:port или http://host:port")
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SECRET_KEY")
APP_ENV = os.getenv("APP_ENV", "production")
# Собственная PostgreSQL доступна приложению через PostgREST на loopback.
POSTGREST_URL = os.getenv("POSTGREST_URL", "").strip()
LOCK_PROVIDER = os.getenv("LOCK_PROVIDER", "disabled")
MANUAL_UNLOCK_ADMIN_IDS = [int(x) for x in re.split(r"[,\s]+", os.getenv("MANUAL_UNLOCK_ADMIN_IDS", ""))
                           if x.strip().isdigit()]
if APP_ENV not in ("local", "production") or LOCK_PROVIDER not in ("disabled", "simulator"):
    raise RuntimeError("Некорректные APP_ENV/LOCK_PROVIDER")
if LOCK_PROVIDER == "simulator" and APP_ENV != "local":
    raise RuntimeError("Имитаторы разрешены только с APP_ENV=local")
if POSTGREST_URL:
    endpoint = urlsplit(POSTGREST_URL)
    allowed_hosts = ("localhost", "127.0.0.1", "::1") + (("rest",) if APP_ENV == "local" else ())
    if (endpoint.scheme != "http" or endpoint.hostname not in allowed_hosts
            or endpoint.username is not None or endpoint.password is not None
            or endpoint.query or endpoint.fragment or endpoint.path not in ("", "/")):
        raise RuntimeError("PostgREST должен быть на loopback; имя rest разрешено только локально")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))   # указать владельца в .env; 0 не соответствует пользователю Telegram
# Дополнительные админы с теми же правами: EXTRA_ADMIN_IDS=111,222 в .env. ID узнать командой /id в боте.
EXTRA_ADMIN_IDS = [int(x) for x in re.split(r"[,\s]+", os.getenv("EXTRA_ADMIN_IDS", "")) if x.strip().isdigit()]
ADMIN_IDS = [ADMIN_ID] + [x for x in EXTRA_ADMIN_IDS if x != ADMIN_ID]
ADMIN_TG = os.getenv("ADMIN_TG", "")
ADMIN_PHONE = os.getenv("ADMIN_PHONE", "")

if not TOKEN:
    raise RuntimeError("TELEGRAM_BOT_TOKEN не найден в .env")
if not SUPABASE_URL and not POSTGREST_URL:
    raise RuntimeError("SUPABASE_URL не найден в .env")
if not SUPABASE_KEY:
    raise RuntimeError("SUPABASE_SECRET_KEY не найден в .env")

# Запросы к базе идут из разных потоков одновременно. Общее HTTP/2-соединение для этого не рассчитано
# (случайные ReadError под нагрузкой), поэтому берём HTTP/1.1 с пулом соединений.
_http = httpx.Client(
    http2=False,
    timeout=httpx.Timeout(15.0, connect=10.0),
    limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
)
if POSTGREST_URL:
    from postgrest import SyncPostgrestClient
    supabase = SyncPostgrestClient(POSTGREST_URL, http_client=_http,
        headers={"Authorization": "Bearer " + SUPABASE_KEY})
else:
    supabase = create_client(SUPABASE_URL, SUPABASE_KEY, options=ClientOptions(httpx_client=_http))

LOCAL_TZ = ZoneInfo("Europe/Moscow")
ASSETS_DIR = BASE_DIR / "assets"
LOGS_DIR = BASE_DIR / "logs"

# Часы бронирования по умолчанию (если у объекта не распознаны свои)
DEFAULT_OPEN_HOUR = 9
DEFAULT_CLOSE_HOUR = 22
BOOKING_WINDOW_DAYS = 7
MAX_PHOTOS = 5

# Напоминания (в минутах)
ADMIN_REMIND_AFTER_MIN = 15      # заявка висит без ответа → напомнить админу
CLIENT_WAIT_NOTICE_MIN = 60      # клиенту: «заявка ещё на согласовании»
VISIT_REMIND_BEFORE_MIN = 120    # клиенту: «через 2 часа ждём вас»
REMINDER_INTERVAL_SEC = 60

# ─── Mini App (веб-интерфейс) ────────────────────────────────
WEBAPP_DIR = BASE_DIR / "webapp"
CACHE_DIR = BASE_DIR / "cache"
WEB_HOST = os.getenv("WEB_HOST", "127.0.0.1")
WEB_PORT = int(os.getenv("WEB_PORT", "8080"))
# Публичный HTTPS-адрес Mini App (нужен Telegram). Берётся из .env (WEBAPP_URL) или, если там пусто,
# из файла, который пишет tunnel.py (адрес временного туннеля меняется при переподключении).
WEBAPP_URL = (os.getenv("WEBAPP_URL") or "").strip().rstrip("/")
WEBAPP_URL_FILE = CACHE_DIR / "webapp_url.txt"


def webapp_url() -> str:
    if WEBAPP_URL:
        return WEBAPP_URL
    try:
        return WEBAPP_URL_FILE.read_text(encoding="utf-8").strip().rstrip("/")
    except OSError:
        return ""
# Режим разработки: вход без Telegram, только с локального адреса. На сервере НЕ включать.
MINIAPP_DEV = os.getenv("MINIAPP_DEV", "").lower() in ("1", "true", "yes")
if MINIAPP_DEV and APP_ENV != "local":
    raise RuntimeError("MINIAPP_DEV разрешён только с APP_ENV=local")
if LOCK_PROVIDER == "simulator" and WEB_HOST not in ("127.0.0.1", "localhost", "::1"):
    raise RuntimeError("Имитатор разрешён только на локальном HTTP-интерфейсе")
INIT_DATA_MAX_AGE_SEC = 24 * 3600

# Пустой URL сохраняет long polling для локальной разработки.
TELEGRAM_WEBHOOK_URL = (os.getenv("TELEGRAM_WEBHOOK_URL") or "").strip()
TELEGRAM_WEBHOOK_SECRET = (os.getenv("TELEGRAM_WEBHOOK_SECRET") or "").strip()
