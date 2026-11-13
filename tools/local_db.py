"""Изолированная PostgreSQL + PostgREST. Данные и ключи только в cache/local-db/.

python -m tools.local_db up
python -m tools.local_db test
python -m tools.local_db down
Никогда не использует DATABASE_URL/.env и не подключается к рабочей базе.
"""
import argparse
import base64
import hashlib
import hmac
import json
import os
from pathlib import Path
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import time
from uuid import uuid4
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parent.parent
STATE = ROOT / "cache" / "local-db"
PG_PORT, REST_PORT = 55439, 55440
DATABASE = "garagehub_local"


def binary(name):
    paths = [os.getenv("PG_BIN", ""), "/opt/homebrew/opt/postgresql@17/bin",
             "/usr/local/opt/postgresql@17/bin", "/usr/lib/postgresql/17/bin"]
    found = shutil.which(name)
    if found:
        return found
    for base in paths:
        if base and (Path(base) / name).is_file():
            return str(Path(base) / name)
    raise RuntimeError("Не найден %s. Установите PostgreSQL 17 и PostgREST; см. docs/LOCAL_CORE.md" % name)


def pg(name, *args, **kwargs):
    env = dict(os.environ, PGHOST="127.0.0.1", PGPORT=str(PG_PORT), PGUSER="postgres", PGDATABASE=DATABASE)
    return subprocess.run([binary(name), *args], env=env, check=True, **kwargs)


def sql(text):
    return pg("psql", "-X", "-v", "ON_ERROR_STOP=1", "-q", input=text, text=True, capture_output=True)


def jwt(secret):
    encode = lambda b: base64.urlsafe_b64encode(b).decode().rstrip("=")
    head = encode(b'{"alg":"HS256","typ":"JWT"}')
    payload = encode(json.dumps({"role": "service_role", "exp": int(time.time()) + 365 * 86400}).encode())
    message = head + "." + payload
    return message + "." + encode(hmac.new(secret.encode(), message.encode(), hashlib.sha256).digest())


def settings():
    return json.loads((STATE / "settings.json").read_text())


def own_postgrest(pid):
    result = subprocess.run(["ps", "-p", str(pid), "-o", "command="], capture_output=True, text=True)
    return result.returncode == 0 and str(STATE / "postgrest.conf") in result.stdout and "postgrest" in result.stdout


def environment():
    cfg = settings()
    return dict(os.environ, APP_ENV="local", LOCK_PROVIDER="simulator", MINIAPP_DEV="1",
        POSTGREST_URL="http://127.0.0.1:%s" % REST_PORT, SUPABASE_URL="http://127.0.0.1:%s" % REST_PORT,
        SUPABASE_SECRET_KEY=jwt(cfg["jwt_secret"]),
        TELEGRAM_BOT_TOKEN="123456:LOCALTESTTOKENTESTTOKENTESTTOKENTEST",
        TELEGRAM_WEBHOOK_URL="", TELEGRAM_WEBHOOK_SECRET="",
        ADMIN_ID="900000010", EXTRA_ADMIN_IDS="900000002", MANUAL_UNLOCK_ADMIN_IDS="900000010",
        GARAGEHUB_LOCAL_TEST="1")


def migrate():
    for path in sorted((ROOT / "sql").glob("[0-9][0-9][0-9]_*.sql")):
        pg("psql", "-X", "-v", "ON_ERROR_STOP=1", "-q", "-f", str(path), capture_output=True, text=True)
    sql("GRANT ALL ON ALL TABLES IN SCHEMA public TO service_role; "
        "GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO service_role; NOTIFY pgrst, 'reload schema';")


def up():
    STATE.mkdir(parents=True, exist_ok=True)
    os.chmod(STATE, 0o700)
    cfg_file = STATE / "settings.json"
    if not cfg_file.exists():
        cfg_file.write_text(json.dumps({"jwt_secret": secrets.token_urlsafe(40)}))
        os.chmod(cfg_file, 0o600)
    data = STATE / "pgdata"
    if not (data / "PG_VERSION").exists():
        # Dedicated cluster, loopback only, no host service or user database modified.
        subprocess.run([binary("initdb"), "-D", str(data), "-U", "postgres", "-A", "trust",
            "--encoding=UTF8", "--no-locale"], check=True, capture_output=True)
    status = subprocess.run([binary("pg_ctl"), "-D", str(data), "status"], capture_output=True)
    if status.returncode:
        with socket.socket() as sock:
            if sock.connect_ex(("127.0.0.1", PG_PORT)) == 0:
                raise RuntimeError("Локальный порт PostgreSQL уже занят другим процессом")
        subprocess.run([binary("pg_ctl"), "-D", str(data), "-l", str(STATE / "postgres.log"),
            "-o", "-h 127.0.0.1 -p %s -k %s" % (PG_PORT, STATE), "-w", "start"], check=True, capture_output=True)
    env = dict(os.environ, PGHOST="127.0.0.1", PGPORT=str(PG_PORT), PGUSER="postgres", PGDATABASE="postgres")
    exists = subprocess.check_output([binary("psql"), "-X", "-tAc",
        "SELECT 1 FROM pg_database WHERE datname='garagehub_local'"], env=env, text=True).strip()
    if not exists:
        subprocess.run([binary("createdb"), DATABASE], env=env, check=True)
    sql("DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='service_role') THEN "
        "CREATE ROLE service_role NOLOGIN BYPASSRLS; END IF; "
        "IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='anon') THEN CREATE ROLE anon NOLOGIN; END IF; "
        "IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='authenticator') THEN CREATE ROLE authenticator LOGIN NOINHERIT; END IF; END $$; "
        "GRANT service_role, anon TO authenticator; GRANT USAGE ON SCHEMA public TO service_role, anon;")
    migrate()
    cfg = settings()
    conf = STATE / "postgrest.conf"
    conf.write_text('db-uri = "postgresql://authenticator@127.0.0.1:%s/%s"\n'
        'db-schemas = "public"\ndb-anon-role = "anon"\njwt-secret = "%s"\n'
        'server-host = "127.0.0.1"\nserver-port = %s\ndb-pool = 10\n' %
        (PG_PORT, DATABASE, cfg["jwt_secret"], REST_PORT))
    os.chmod(conf, 0o600)
    pidfile = STATE / "postgrest.pid"
    pid = int(pidfile.read_text()) if pidfile.exists() else None
    running = False
    if pid:
        try:
            os.kill(pid, 0)
            if not own_postgrest(pid):
                raise RuntimeError("PID PostgREST не принадлежит этому стенду; процесс не тронут")
            running = True
        except ProcessLookupError:
            pass
    if not running:
        with (STATE / "postgrest.log").open("ab") as log:
            proc = subprocess.Popen([binary("postgrest"), str(conf)], stdout=log, stderr=log, start_new_session=True)
        pidfile.write_text(str(proc.pid))
    for _ in range(100):
        try:
            with urlopen("http://127.0.0.1:%s/" % REST_PORT, timeout=1):
                print("Локальная база готова (PostgreSQL + PostgREST).")
                return
        except OSError:
            time.sleep(0.1)
    raise RuntimeError("PostgREST не запущен; см. cache/local-db/postgrest.log")


def down():
    pidfile = STATE / "postgrest.pid"
    if pidfile.exists():
        try:
            pid = int(pidfile.read_text())
            os.kill(pid, 0)
            if not own_postgrest(pid):
                raise RuntimeError("PID не принадлежит этому стенду; остановка запрещена")
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        pidfile.unlink()
    data = STATE / "pgdata"
    if data.exists():
        status = subprocess.run([binary("pg_ctl"), "-D", str(data), "status"], capture_output=True)
        if status.returncode == 0:
            subprocess.run([binary("pg_ctl"), "-D", str(data), "-m", "fast", "-w", "stop"], check=True, capture_output=True)
    print("Локальные процессы базы остановлены. Данные сохранены.")


def restore_check():
    """Восстанавливает снимок в НОВУЮ временную базу; исходная база не изменяется."""
    backup = STATE / "restore-check.dump"
    pg("pg_dump", "-Fc", "-f", str(backup))
    temporary = "garagehub_restore_" + uuid4().hex[:12]
    pg("createdb", temporary, capture_output=True)
    try:
        pg("pg_restore", "--exit-on-error", "--no-owner", "-d", temporary, str(backup), capture_output=True)
        result = pg("psql", "-X", "-v", "ON_ERROR_STOP=1", "-d", temporary, "-tAc",
            "SELECT gh_restore_gate(900000010, 'Локальная проверка восстановления'); "
            "SELECT gh_readiness(); "
            "SELECT count(*) FROM lock_commands WHERE state IN ('queued','dispatching','cloud_accepted'); "
            "SELECT count(*) FROM gh_claim_command();", capture_output=True, text=True).stdout.strip().splitlines()
        if result[-3:] != ["t", "0", "0"]:
            raise RuntimeError("Проверка восстановления не пройдена")
        print("Backup восстановлен в отдельную базу; схема готова, старые команды не отправляются.")
    finally:
        pg("dropdb", temporary, capture_output=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("up", "down", "migrate", "test", "web", "worker", "seed", "backup", "restore-check"))
    args = parser.parse_args()
    if args.command == "up": up()
    elif args.command == "down": down()
    elif args.command == "migrate": migrate()
    elif args.command == "restore-check": restore_check()
    elif args.command == "backup":
        pg("pg_dump", "-Fc", "-f", str(STATE / "backup.dump"))
        print("Резервная копия: cache/local-db/backup.dump")
    else:
        module = {"test": "tests/run_all.py", "web": "tools.local_app", "worker": "core.worker", "seed": "tools.local_seed"}[args.command]
        command = [sys.executable, module] if module.endswith(".py") else [sys.executable, "-m", module]
        raise SystemExit(subprocess.call(command, cwd=ROOT, env=environment()))


if __name__ == "__main__":
    main()
