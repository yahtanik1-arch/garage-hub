"""python -m core.worker. PostgREST RPC: ограниченный опрос, backoff, без LISTEN-соединения."""
import argparse
import asyncio
import logging
import signal
import time

from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from core import db, notifications
from core.config import APP_ENV, LOCK_PROVIDER, TOKEN

log = logging.getLogger(__name__)


class Worker:
    def __init__(self, bot, adapter=None):
        self.bot, self.adapter = bot, adapter
        self.last_recovery = None

    async def step(self):
        current = time.monotonic()
        if self.last_recovery is None or current - self.last_recovery >= 15:
            await db.worker_tick()
            count = await db.recover_lock_commands()
            if count:
                log.warning("lock_commands_recovered_unknown count=%s", count)
            self.last_recovery = current
        busy = False
        if self.adapter is not None:
            c = await db.claim_lock_command()
            if c:
                busy = True
                d = await db.lock_device(c["device_id"])
                # До внешнего вызова проверяется тип адаптера; чужой замок не обслуживаем.
                if not d or d["provider"] != "simulator" or not c["simulated"]:
                    await db.finish_lock_command(c["id"], "failed", "adapter_unavailable")
                else:
                    try:
                        state, code, events = await asyncio.wait_for(self.adapter.send(c, d), timeout=10)
                    except asyncio.CancelledError:
                        # dispatching останется для восстановления в unknown; повторной отправки нет.
                        raise
                    except Exception:
                        await db.finish_lock_command(c["id"], "unknown", "transport_uncertain")
                    else:
                        await db.finish_lock_command(c["id"], state, code)
                        for event in events:
                            await db.record_device_event(**event)
                log.info("lock_command_processed id=%s", c["id"])
        sent = await notifications.deliver_one(self.bot)
        return busy or sent is not None

    async def run(self, stopped):
        delay = 1
        while not stopped.is_set():
            try:
                busy = await self.step()
                delay = 0.1 if busy else min(5, max(1, delay * 2))
            except Exception:
                # Не выводим содержимое ответа провайдера/секреты.
                log.error("worker_step_failed; durable queue retained")
                delay = min(30, max(2, delay * 2))
            try:
                await asyncio.wait_for(stopped.wait(), timeout=delay)
            except asyncio.TimeoutError:
                pass


async def main(scenario="success"):
    adapter = None
    if LOCK_PROVIDER == "simulator":
        from core.lock_simulator import LockSimulator
        adapter = LockSimulator(scenario)
    if APP_ENV == "local":
        from tools.local_bot import LocalBot
        bot = LocalBot(TOKEN)
    else:
        bot = Bot(TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    stopped = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stopped.set)
    try:
        if not await db.readiness():
            raise RuntimeError("Схема базы не готова")
        await Worker(bot, adapter).run(stopped)
    finally:
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.remove_signal_handler(sig)
        await bot.session.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--scenario", default="success")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main(args.scenario))
