from __future__ import annotations
from homeassistant.components.sensor import SensorEntity, SensorDeviceClass
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.const import PERCENTAGE
from . import MyQConfigEntry
from .const import DOMAIN, MANUFACTURER


async def async_setup_entry(hass, entry: MyQConfigEntry, async_add_entities):
    entities = []
    for device in entry.runtime_data.coordinator.data:
        entities.extend([MyQSensor(entry, device, "wifi_signal_strength", "Wi-Fi signal strength", "mdi:wifi", "dBm"), MyQSensor(entry, device, "state", "Door state", "mdi:garage")])
    async_add_entities(entities)


class MyQSensor(CoordinatorEntity, SensorEntity):
    _attr_has_entity_name = True
    def __init__(self, entry, device, key, name, icon, unit=None):
        self.coordinator = entry.runtime_data.coordinator; super().__init__(self.coordinator); self._initial_device = device; self._serial_number = device.serial_number; self.key = key
        self._attr_unique_id = f"{DOMAIN}_{device.serial_number}_{key}"; self._attr_name = name; self._attr_icon = icon; self._attr_native_unit_of_measurement = unit
        self._attr_device_info = {"identifiers": {(DOMAIN, device.serial_number)}, "name": device.name, "manufacturer": MANUFACTURER, "serial_number": device.serial_number}
    @property
    def device(self): return next((item for item in self.coordinator.data if item.serial_number == self._serial_number), self._initial_device)
    @property
    def native_value(self): return getattr(self.device, self.key)
    @property
    def available(self): return self.coordinator.last_update_success
