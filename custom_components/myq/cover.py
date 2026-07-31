from __future__ import annotations

from homeassistant.components.cover import CoverEntity, CoverEntityFeature, CoverDeviceClass
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from . import MyQConfigEntry
from .const import DOMAIN, MANUFACTURER


async def async_setup_entry(hass, entry: MyQConfigEntry, async_add_entities):
    async_add_entities([MyQCover(entry, device) for device in entry.runtime_data.coordinator.data])


class MyQCover(CoordinatorEntity, CoverEntity):
    _attr_device_class = CoverDeviceClass.GARAGE
    _attr_supported_features = CoverEntityFeature.OPEN | CoverEntityFeature.CLOSE
    _attr_has_entity_name = True

    def __init__(self, entry, device):
        self.coordinator = entry.runtime_data.coordinator
        super().__init__(self.coordinator)
        self.client = entry.runtime_data.client
        self._initial_device = device
        self._serial_number = device.serial_number
        self._attr_unique_id = f"{DOMAIN}_{device.serial_number}"
        self._attr_name = device.name
        self._attr_device_info = {"identifiers": {(DOMAIN, device.serial_number)}, "name": device.name, "manufacturer": MANUFACTURER, "serial_number": device.serial_number}

    @property
    def device(self):
        return next((item for item in self.coordinator.data if item.serial_number == self._serial_number), self._initial_device)

    @property
    def available(self): return self.coordinator.last_update_success and self.device.online is not False
    @property
    def is_open(self): return self.device.normalized_state in ("open", "opening", "closing")
    @property
    def is_opening(self): return self.device.normalized_state == "opening"
    @property
    def is_closing(self): return self.device.normalized_state == "closing"
    async def async_open_cover(self): await self._command("open")
    async def async_close_cover(self): await self._command("close")
    async def _command(self, action):
        await self.client.command(self.device, action)
        await self.coordinator.async_request_refresh()
    async def async_update(self):
        await self.coordinator.async_request_refresh()
    @property
    def extra_state_attributes(self):
        return {"serial_number": self.device.serial_number, "door_state": self.device.state, "online": self.device.online, "wifi_signal_strength": self.device.wifi_signal_strength, "fault_codes": self.device.fault_codes}
