"""Сборка и запуск бота: логи, порядок обработчиков, ошибки, профиль, напоминания, веб-часть Mini App.
Запускается из корня проекта через bot.py или start.sh."""
import asyncio
import logging
import signal
import time
from logging.handlers import RotatingFileHandler

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.storage.memory import MemoryStorage, SimpleEventIsolation
from aiogram.types import (
    BotCommand, BotCommandScopeChat, BotCommandScopeDefault, ErrorEvent, MenuButtonDefault,
    MenuButtonWebApp, WebAppInfo,
)

from core import service
from tgbot import keyboards as kb
from tgbot import reminders
from tgbot import texts as t
from api.server import start_web
from core.config import (
    ADMIN_IDS, LOGS_DIR, TELEGRAM_WEBHOOK_SECRET, TELEGRAM_WEBHOOK_URL, TELEGRAM_PROXY_URL, TOKEN,
    WEB_PORT, supabase, webapp_url,
)
from tgbot.webhook import validate_config
from tgbot.handlers import admin_bookings, admin_objects, booking, common, locks, owners, search

log = logging.getLogger("garagehub")


def setup_logging() -> None:
    LOGS_DIR.mkdir(exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s", "%Y-%m-%d %H:%M:%S")
    file_handler = RotatingFileHandler(LOGS_DIR / "bot.log", maxBytes=2_000_000, backupCount=3, encoding="utf-8")
    file_handler.setFormatter(fmt)
    console = logging.StreamHandler()
    console.setFormatter(fmt)
    logging.basicConfig(level=logging.INFO, handlers=[console, file_handler])
    for noisy in ("httpx", "httpcore", "hpack"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def build_dispatcher() -> Dispatcher:
    dp = Dispatcher(storage=MemoryStorage(), events_isolation=SimpleEventIsolation())
    # Порядок важен: сначала меню (срабатывает в любом состоянии), потом сценарии.
    dp.include_routers(
        common.router,
        admin_bookings.router,
        admin_objects.router,
        booking.router,
        locks.router,
        search.router,
        owners.router,
    )
    dp.errors.register(on_error)
    return dp


_last_error_report = None


async def on_error(event: ErrorEvent) -> bool:
    global _last_error_report
    error = event.exception
    if isinstance(error, TelegramBadRequest) and (
        "not modified" in str(error) or "query is too old" in str(error)
    ):
        return True
    log.exception("Ошибка при обработке обновления", exc_info=error)

    update = event.update
    bot = update.bot if hasattr(update, "bot") else None
    message = update.message or (update.callback_query.message if update.callback_query else None)
    where = "сообщение" if update.message else "кнопка" if update.callback_query else "обновление"
    if update.callback_query:
        where += f" «{update.callback_query.data}»"
    elif update.message and update.message.text:
        where += f" «{t.clip(update.message.text, 40)}»"

    try:
        if update.callback_query:
            await update.callback_query.answer("Что-то пошло не так. Попробуйте ещё раз.", show_alert=True)
        elif message:
            await message.answer("Что-то пошло не так. Попробуйте ещё раз или начните с /start.")
    except Exception:
        pass

    if message and (_last_error_report is None or time.monotonic() - _last_error_report > 60):
        _last_error_report = time.monotonic()
        try:
            await service.notify_admins(message.bot, t.error_for_admin(where, error))
        except Exception:
            pass
    return True


def check_schema() -> bool:
    try:
        supabase.table("garages").select("is_active, open_hour, close_hour").limit(1).execute()
        supabase.table("bookings").select("admin_reminded, client_wait_notified, visit_reminded").limit(1).execute()
        supabase.table("owner_requests").select("id").limit(1).execute()
        if not supabase.rpc("gh_readiness").execute().data:
            return False
        if TELEGRAM_WEBHOOK_URL:
            supabase.table("telegram_updates").select("bot_id, update_id").limit(1).execute()
        return True
    except Exception as e:
        log.error("База не обновлена: %s", e)
        return False


async def setup_commands(bot: Bot) -> None:
    common_commands = [
        BotCommand(command="start", description="Главное меню"),
        BotCommand(command="find", description="Найти бокс"),
        BotCommand(command="my", description="Мои заявки"),
        BotCommand(command="contacts", description="Контакты"),
        BotCommand(command="app", description="Открыть приложение"),
    ]
    await bot.set_my_commands(common_commands, scope=BotCommandScopeDefault())
    for admin_id in ADMIN_IDS:
        try:
            await bot.set_my_commands(
                common_commands + [
                    BotCommand(command="admin", description="Панель управления"),
                    BotCommand(command="client", description="Посмотреть глазами клиента"),
                ],
                scope=BotCommandScopeChat(chat_id=admin_id),
            )
        except Exception as e:   # админ ещё ни разу не писал боту
            log.warning("Команды админа %s не выставлены: %s", admin_id, e)


async def setup_profile(bot: Bot) -> None:
    """Описание бота — то, что человек видит в пустом чате до нажатия кнопки запуска.
    Меняем, только если текст отличается: у Telegram лимит на частоту таких запросов."""
    try:
        if (await bot.get_my_description()).description != t.BOT_DESCRIPTION:
            await bot.set_my_description(t.BOT_DESCRIPTION)
            log.info("Описание бота обновлено")
        if (await bot.get_my_short_description()).short_description != t.BOT_SHORT_DESCRIPTION:
            await bot.set_my_short_description(t.BOT_SHORT_DESCRIPTION)
            log.info("Короткое описание бота обновлено")
    except Exception as e:
        log.warning("Не удалось обновить описание бота: %s", e)


async def sync_menu_button(bot: Bot) -> None:
    """Кнопка «Открыть» слева от поля ввода всегда ведёт на текущий адрес приложения.
    Адрес туннеля может смениться — проверяем раз в 30 секунд. Нет адреса — убираем кнопку.

    Telegram на телефоне держит старую кнопку, пока чат не переоткроют, поэтому при каждой смене
    адреса админу приходит свежая кнопка «Открыть приложение», а прошлая такая же удаляется."""
    current = None
    admin_messages = {}
    while True:
        url = webapp_url()
        if url != current:
            try:
                if url:
                    await bot.set_chat_menu_button(
                        menu_button=MenuButtonWebApp(text="Открыть", web_app=WebAppInfo(url=url + "/")),
                    )
                    log.info("Кнопка приложения → %s", url)
                else:
                    await bot.set_chat_menu_button(menu_button=MenuButtonDefault())
                    log.info("Адреса приложения нет — кнопка «Открыть» убрана")
                current = url
            except Exception as e:
                log.warning("Не удалось обновить кнопку приложения: %s", e)
            for admin_id in ADMIN_IDS:
                admin_messages[admin_id] = await refresh_admin_button(bot, admin_id, admin_messages.get(admin_id))
        await asyncio.sleep(30)


async def refresh_admin_button(bot: Bot, admin_id: int, previous_id):
    if previous_id:
        try:
            await bot.delete_message(admin_id, previous_id)
        except Exception:
            pass
    markup = kb.open_app()
    if not markup:
        return None
    try:
        sent = await bot.send_message(admin_id, t.APP_LINK_UPDATED, reply_markup=markup, disable_notification=True)
        return sent.message_id
    except Exception as e:
        log.warning("Не удалось прислать админу %s кнопку приложения: %s", admin_id, e)
        return None


async def wait_for_shutdown() -> None:
    stopped = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stopped.set)
    try:
        await stopped.wait()
    finally:
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.remove_signal_handler(sig)


async def main() -> None:
    setup_logging()
    validate_config(TELEGRAM_WEBHOOK_URL, TELEGRAM_WEBHOOK_SECRET)
    if not check_schema():
        log.error("Примените миграции sql/000–003 по README и перезапустите бота.")
        raise RuntimeError("База или её схема недоступна")

    bot = Bot(token=TOKEN, session=AiohttpSession(proxy=TELEGRAM_PROXY_URL or None),
              default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = build_dispatcher()
    tasks = []
    web_runner = None
    webhook_started = False
    try:
        me = await bot.get_me()
        await setup_commands(bot)
        await setup_profile(bot)
        try:
            web_runner = await start_web(bot, dp)
        except OSError as e:
            if TELEGRAM_WEBHOOK_URL:
                raise  # Без HTTP-сервера webhook принимать некому.
            log.warning("Веб-часть Mini App не запущена (порт %s занят?): %s", WEB_PORT, e)

        tasks = [asyncio.create_task(reminders.run(bot)), asyncio.create_task(sync_menu_button(bot))]
        if TELEGRAM_WEBHOOK_URL:
            await dp.emit_startup(bot=bot)
            webhook_started = True
            await bot.set_webhook(
                TELEGRAM_WEBHOOK_URL, secret_token=TELEGRAM_WEBHOOK_SECRET,
                allowed_updates=dp.resolve_used_update_types(), drop_pending_updates=False,
            )
            log.info("GarageHub запущен: @%s, webhook", me.username)
            await wait_for_shutdown()
        else:
            await bot.delete_webhook(drop_pending_updates=False)
            log.info("GarageHub запущен: @%s, long polling", me.username)
            await dp.start_polling(
                bot, allowed_updates=dp.resolve_used_update_types(), close_bot_session=False,
            )
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        if web_runner:
            await web_runner.cleanup()
        if webhook_started:
            await dp.emit_shutdown(bot=bot)
        await bot.session.close()
        # Webhook оставляем зарегистрированным: Telegram доставит события после перезапуска.
        log.info("GarageHub остановлен")


def run() -> None:
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
