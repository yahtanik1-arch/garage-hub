# GarageHub

Почасовая аренда гаражей самообслуживания и детейлинг-боксов в Москве и области.
Telegram-бот [@garazh_rent_bot](https://t.me/garazh_rent_bot) + Mini App внутри Telegram + первая версия сайта (`site/`, [план](docs/SITE_PLAN.md)).

Целевая архитектура с Telegram webhook и замками Tuya — [системный дизайн](docs/SYSTEM_DESIGN.md).

Выбранный вариант замка для первого бокса — [TTL M-20T с TTLock G2](docs/TTL_M20T.md):
простое объяснение подключения, открытия из бота, стоимости и оставшихся проверок.

Ядро доступа уже можно проверить локально: настоящая PostgreSQL, очередь команд,
worker и имитатор замка, без VPS, Tuya и реального Telegram.
Запуск и границы проверки — [локальный стенд](docs/LOCAL_CORE.md).

Фактическое состояние сервера, настройки собственной PostgreSQL и Telegram-прокси —
[развёртывание](docs/DEPLOYMENT.md). Инфраструктура установлена, бот пока не запущен:
нужны токен, ID администратора и проверенный исходящий доступ к Telegram.

## Установка с нуля (для разработчика)

Нужен Python 3.9 (проект проверен на 3.9.6: синтаксис 3.10+ не использовать).

```bash
git clone <адрес репозитория> garage_bot && cd garage_bot
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # заполнить токен бота и ключи Supabase
```

Схема базы — файлы в `sql/`: в Supabase через SQL Editor либо в своей PostgreSQL по инструкции развёртывания. Бот при старте проверяет схему и не запустится, если SQL не выполнен.

## Запуск

```bash
cd ~/Desktop/garage_bot && ./start.sh
```

Запускает бота, приложение и временный HTTPS-адрес для приложения. Пока работает — Мак не засыпает. Остановить: Ctrl+C.
Только бот, без приложения: `source venv/bin/activate && python3 bot.py`.

Интерфейс Mini App без Telegram: `WEB_PORT=8081 python3 -m tools.dev_web` → http://localhost:8081/?dev=client (или `?dev=admin`).

## Telegram webhook на сервере

Локальный запуск по умолчанию использует long polling. Для сервера с постоянным HTTPS:

1. После резервной копии выполните миграции `sql/000_base.sql`–`sql/003_access.sql`
   по порядку в Supabase → SQL Editor; перед 003 разберите пересечения по инструкции стенда.
2. Создайте секрет: `python3 -c 'import secrets; print(secrets.token_urlsafe(32))'`.
3. Укажите в `.env`:

```dotenv
WEBAPP_URL=https://example.com
TELEGRAM_WEBHOOK_URL=https://example.com/telegram/webhook
TELEGRAM_WEBHOOK_SECRET=секрет_из_шага_2
```

4. Настройте HTTPS reverse proxy: `/telegram/webhook` (POST), `/api/` и Mini App
   должны пересылаться на `127.0.0.1:8080`. Не удаляйте заголовок
   `X-Telegram-Bot-Api-Secret-Token`. Затем запустите `python3 bot.py`.

Бот сам регистрирует webhook после запуска HTTP-сервера. Webhook требует секретный
заголовок, а маршруты Mini App по-прежнему проверяют подпись Telegram. Тестовый
`tools.dev_web` не регистрирует webhook и не принимает события бота.

Ответ HTTP 200 возвращается после обработки события и сохранения его ID в базе.
Одновременные копии выполняют один обработчик; обработанные ID переживают перезапуск.
При необработанной ошибке возвращается HTTP 500, чтобы Telegram повторил доставку.
Это не гарантия exactly-once: авария после бизнес-действия, но до сохранения ID
может привести к повтору. Критические действия должны иметь собственную идемпотентность;
открытие уже использует отдельную устойчивую команду и бизнес-ключ идемпотентности.
Физическая отправка вынесена в отдельный `python -m core.worker`; настоящий Tuya adapter
пока не подключён. В production оставьте `LOCK_PROVIDER=disabled`.

Запускайте **один экземпляр бота**: состояния диалогов и координация одновременных
событий пока хранятся в памяти. При остановке webhook не удаляется. Для возврата к
polling остановите webhook-экземпляр, удалите обе настройки `TELEGRAM_WEBHOOK_*`
и запустите бота: он снимет webhook, сохранив ожидающие события.
Временный туннель с меняющимся адресом для webhook не используйте.

## Сайт

```bash
source venv/bin/activate && python3 site/serve.py
```

- http://localhost:8090 — настоящий каталог (нужен запущенный бот, он отдаёт данные на 127.0.0.1:8080)
- http://localhost:8090/?demo — примеры боксов, бот не нужен

Подробнее — `site/README.md`.

## Проверка

```bash
source venv/bin/activate
python -m tools.local_db up
python -m tools.local_db test
```

Тесты используют только выделенную локальную базу, которая очищается после прогона;
в настоящий Telegram ничего не уходит. Проверки webhook выполняются отдельно
без `.env`, Telegram и внешней базы: `python3 tests/t_webhook.py`.

## Устройство

```
core/      настройки, БД, брони, права, команды, worker, outbox, имитатор
tgbot/     Telegram-бот: обработчики, кнопки, тексты, напоминания
api/       HTTP API для Mini App и будущего сайта (вход через подпись Telegram)
webapp/    интерфейс Mini App; правила дизайна — webapp/DESIGN.md
site/      сайт (статические страницы + serve.py для просмотра)
assets/    картинки бота (обложка, заглушка, описание)
sql/       изменения базы (выполняются в Supabase → SQL Editor)
tests/     автотесты
tools/     служебное: туннель (tunnel.py), тестовый веб-сервер (dev_web.py)
docs/      документы и планы
deploy/    конфигурации systemd/nginx/PostgreSQL и ежедневного backup установленного сервера
```

Секреты — только в `.env` (в git не попадает, образец — `.env.example`). Подробности для разработки — в `CLAUDE.md`.
