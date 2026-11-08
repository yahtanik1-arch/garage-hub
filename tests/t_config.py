"""Проверка серверной конфигурации без сети и рабочих секретов."""
import os
from pathlib import Path
import subprocess
import sys
import unittest


class ConfigurationTests(unittest.TestCase):
    def test_missing_admin_settings_grant_no_user_and_have_no_personal_contacts(self):
        env = dict(os.environ, TELEGRAM_BOT_TOKEN="123456:TESTTOKEN",
            SUPABASE_URL="https://example.supabase.co", SUPABASE_SECRET_KEY="test-key",
            APP_ENV="production", LOCK_PROVIDER="disabled", POSTGREST_URL="",
            TELEGRAM_PROXY_URL="", MINIAPP_DEV="")
        for name in ("ADMIN_ID", "EXTRA_ADMIN_IDS", "ADMIN_TG", "ADMIN_PHONE"):
            env.pop(name, None)
        script = "from unittest.mock import patch\nwith patch('dotenv.load_dotenv'):\n import core.config as c\n assert c.ADMIN_IDS == [0] and c.ADMIN_TG == '' and c.ADMIN_PHONE == ''\n"
        p = subprocess.run([sys.executable, "-c", script],
            cwd=Path(__file__).resolve().parent.parent, env=env,
            capture_output=True, text=True, timeout=15)
        self.assertEqual(p.returncode, 0, p.stderr)

    def test_telegram_check_hides_credentials_for_invalid_proxy(self):
        env = dict(os.environ, TELEGRAM_PROXY_URL="invalid://user:private-password@host:1080")
        p = subprocess.run([sys.executable, "-m", "tools.check_telegram"],
            cwd=Path(__file__).resolve().parent.parent, env=env,
            capture_output=True, text=True, timeout=15)
        self.assertEqual(p.returncode, 1)
        self.assertIn("Telegram check FAILED", p.stdout)
        self.assertNotIn("private-password", p.stdout + p.stderr)

    def test_proxy_url_validation_does_not_expose_credentials(self):
        for url, expected in [
            ("", True), ("socks5://user:private-password@127.0.0.1:1080", True),
            ("http://127.0.0.1:3128", True), ("mtproto://host:443", False),
            ("socks5://user:private-password@host:no-port", False),
            ("socks5://user:private-password@host:1080?x=1", False),
            ("socks5://host", False), ("socks5://host:0", False),
        ]:
            with self.subTest(expected=expected):
                env = dict(os.environ, TELEGRAM_PROXY_URL=url, APP_ENV="production",
                    POSTGREST_URL="http://127.0.0.1:3000", LOCK_PROVIDER="disabled",
                    MINIAPP_DEV="", TELEGRAM_BOT_TOKEN="123456:TESTTOKEN",
                    SUPABASE_SECRET_KEY="test-key", ADMIN_ID="1")
                p = subprocess.run([sys.executable, "-c", "import core.config"],
                    cwd=Path(__file__).resolve().parent.parent, env=env,
                    capture_output=True, text=True, timeout=15)
                self.assertEqual(p.returncode == 0, expected)
                self.assertNotIn("private-password", p.stderr)

    def test_database_and_development_boundaries(self):
        cases = [
            ("production", "http://127.0.0.1:3000", "disabled", "", True),
            ("production", "http://[::1]:3000", "disabled", "", True),
            ("production", "", "disabled", "", True),
            ("production", "http://rest:3000", "disabled", "", False),
            ("production", "http://example.com:3000", "disabled", "", False),
            ("production", "http://secret@127.0.0.1:3000", "disabled", "", False),
            ("production", "http://127.0.0.1:3000?key=secret", "disabled", "", False),
            ("production", "http://127.0.0.1:3000", "simulator", "", False),
            ("production", "http://127.0.0.1:3000", "disabled", "1", False),
            ("local", "http://rest:3000", "simulator", "1", True),
        ]
        for mode, url, provider, dev, expected in cases:
            with self.subTest(mode=mode, url=url, provider=provider, dev=dev):
                env = dict(os.environ, APP_ENV=mode, POSTGREST_URL=url,
                    TELEGRAM_PROXY_URL="",
                    LOCK_PROVIDER=provider, MINIAPP_DEV=dev, WEB_HOST="127.0.0.1",
                    SUPABASE_URL="https://example.supabase.co", SUPABASE_SECRET_KEY="test-key",
                    TELEGRAM_BOT_TOKEN="123456:TESTTOKENTESTTOKENTESTTOKENTESTTOKEN", ADMIN_ID="1")
                p = subprocess.run([sys.executable, "-c", "import core.config"],
                    cwd=Path(__file__).resolve().parent.parent, env=env,
                    capture_output=True, text=True, timeout=15)
                self.assertEqual(p.returncode == 0, expected, p.stderr)


if __name__ == "__main__":
    unittest.main()
