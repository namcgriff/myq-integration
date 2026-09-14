from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class MyQDevice:
    serial_number: str
    name: str
    state: str | None = None
    online: bool | None = None
    wifi_signal_strength: int | None = None
    fault_codes: list[str] = field(default_factory=list)
    attributes: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_json(cls, item: dict[str, Any]) -> "MyQDevice | None":
        serial = item.get("serialNumber") or item.get("serial_number") or item.get("deviceId")
        if not serial:
            return None
        state = item.get("doorState") or item.get("state") or item.get("lastStatus")
        name = item.get("name") or item.get("nameLong") or item.get("description") or serial
        faults = item.get("activeFaultCodes") or []
        return cls(str(serial), str(name), state, item.get("isOnline"), item.get("wifiSignalStrength"), faults if isinstance(faults, list) else [], item)

    @property
    def normalized_state(self) -> str:
        value = (self.state or "unknown").lower().replace("_", " ")
        if "open" in value and "clos" not in value:
            return "open"
        if "clos" in value:
            return "closed" if "ing" not in value else "closing"
        if "opening" in value:
            return "opening"
        if "stop" in value or "idle" in value:
            return "stopped"
        return "unknown"
