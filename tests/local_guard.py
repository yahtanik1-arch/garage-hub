"""Запрещает записывающим тестам использовать облачную или рабочую базу."""
import os


def require_local_database():
    if (os.getenv("GARAGEHUB_LOCAL_TEST") != "1" or os.getenv("APP_ENV") != "local"
            or os.getenv("POSTGREST_URL") != "http://127.0.0.1:55440"):
        raise RuntimeError("Запустите python -m tools.local_db test: тесты разрешены только на изолированном локальном стенде")
