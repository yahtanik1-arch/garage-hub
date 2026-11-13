"""Только локальные испытания. Ни одного вызова Tuya или физического устройства."""
import asyncio
from datetime import datetime, timezone
from uuid import uuid4

from core.config import APP_ENV, LOCK_PROVIDER


class LockSimulator:
    def __init__(self, scenario="success"):
        if APP_ENV != "local" or LOCK_PROVIDER != "simulator":
            raise RuntimeError("Имитатор запрещён вне локального стенда")
        if scenario not in ("success", "reject", "timeout", "offline", "no_event", "duplicate", "old_event"):
            raise ValueError("Неизвестный сценарий имитатора")
        self.scenario = scenario
        self.calls = []

    async def send(self, command, device):
        if device["provider"] != "simulator" or not command["simulated"]:
            raise RuntimeError("Имитатор не обслуживает реальные устройства")
        self.calls.append(command["id"])
        if self.scenario == "timeout":
            raise asyncio.TimeoutError()
        if self.scenario in ("reject", "offline"):
            return "failed", "simulated_" + self.scenario, []
        event = {"p_device_id": device["id"], "p_event_id": str(uuid4()), "p_kind": "unlocked",
            "p_occurred_at": datetime.now(timezone.utc).isoformat(), "p_command_id": command["id"],
            "p_data": {"simulated": True, "unlocked": True}}
        if self.scenario == "old_event":
            event["p_occurred_at"] = "2020-01-01T00:00:00+00:00"
        events = [] if self.scenario == "no_event" else [event]
        if self.scenario == "duplicate":
            events.append(dict(event))
        return "cloud_accepted", "simulated_accepted", events
