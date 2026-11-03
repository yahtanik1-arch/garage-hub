"""Очищает только выделенную локальную тестовую базу, включая команды/права."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from local_guard import require_local_database
require_local_database()
from tools.local_db import sql

sql("TRUNCATE device_events, audit_log, notification_outbox, lock_commands, unlock_actions, "
    "access_grants, lock_devices, bookings, garages, owner_requests, telegram_updates, service_heartbeats "
    "RESTART IDENTITY CASCADE;")
print("Изолированная локальная база очищена.")
