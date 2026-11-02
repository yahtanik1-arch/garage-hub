"""Все проверки одной командой (из корня проекта):

    source venv/bin/activate && python3 tests/run_all.py

Порядок: разбор времени → API (без Telegram) → сценарии бота (фейковый Telegram, настоящая база).
В конце всегда убирает тестовые данные (tests/cleanup.py). В настоящий Telegram ничего не уходит.
"""
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
SUITES = [
    ("Telegram webhook (без внешних сервисов)", "t_webhook.py"),
    ("Разбор времени", "t_utils.py"),
    ("API Mini App", "t_api.py"),
    ("Сценарии бота", "t_all.py"),
]


def run(script: str):
    started = time.monotonic()
    proc = subprocess.run([sys.executable, str(HERE / script)], cwd=HERE.parent,
                          capture_output=True, text=True, timeout=600)
    return proc.returncode, proc.stdout + proc.stderr, time.monotonic() - started


def main() -> int:
    failed = []
    try:
        for title, script in SUITES:
            code, output, took = run(script)
            mark = "✓" if code == 0 else "✗"
            print(f"{mark} {title} ({took:.0f} с)")
            if code != 0:
                failed.append(title)
                tail = [line for line in output.splitlines() if "✗" in line or "Error" in line or "сбой" in line]
                print("   " + "\n   ".join((tail or output.splitlines())[-12:]))
    finally:
        code, output, _ = run("cleanup.py")
        print("Тестовые данные убраны" if code == 0 else "⚠ Не удалось убрать тестовые данные:\n" + output)
    print("\nИТОГ:", "всё прошло" if not failed else f"упало: {', '.join(failed)}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
