from __future__ import annotations

from homeassistant.components.cover import (
    CoverDeviceClass,
    CoverEntity,
    CoverEntityFeature,
)
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import MyQConfigEntry
from .const import DOMAIN, MANUFACTURER


async def async_setup_entry(
    hass,
    entry: MyQConfigEntry,
    async_add_entities,
) -> None:
    """Set up myQ cover entities."""
    async_add_entities(
        [
            MyQCover(entry, device)
            for device in entry.runtime_data.coordinator.data
        ]
    )


class MyQCover(CoordinatorEntity, CoverEntity):
    """Representation of a myQ garage door."""

    _attr_device_class = CoverDeviceClass.GARAGE
    _attr_supported_features = (
        CoverEntityFeature.OPEN
        | CoverEntityFeature.CLOSE
    )
    _attr_has_entity_name = True

    def __init__(
        self,
        entry: MyQConfigEntry,
        device,
    ) -> None:
        """Initialize the myQ cover."""
        self.coordinator = entry.runtime_data.coordinator

        super().__init__(self.coordinator)

        self.client = entry.runtime_data.client

        self._initial_device = device
        self._serial_number = device.serial_number

        self._attr_unique_id = (
            f"{DOMAIN}_{device.serial_number}"
        )

        self._attr_name = device.name

        self._attr_device_info = {
            "identifiers": {
                (DOMAIN, device.serial_number)
            },
            "name": device.name,
            "manufacturer": MANUFACTURER,
            "serial_number": device.serial_number,
        }

    @property
    def device(self):
        """Return the current device data."""
        return next(
            (
                item
                for item in self.coordinator.data
                if item.serial_number == self._serial_number
            ),
            self._initial_device,
        )

    @property
    def available(self) -> bool:
        """Return whether the device is available."""
        return (
            self.coordinator.last_update_success
            and self.device.online is not False
        )

    @property
    def is_closed(self) -> bool | None:
        """Return whether the garage door is closed."""
        state = self.device.normalized_state

        if state == "closed":
            return True

        if state in (
            "open",
            "opening",
            "closing",
            "stopped",
        ):
            return False

        return None

    @property
    def is_open(self) -> bool | None:
        """Return whether the garage door is open."""
        state = self.device.normalized_state

        if state == "open":
            return True

        if state in (
            "closed",
            "opening",
            "closing",
            "stopped",
        ):
            return False

        return None

    @property
    def is_opening(self) -> bool:
        """Return whether the garage door is opening."""
        return self.device.normalized_state == "opening"

    @property
    def is_closing(self) -> bool:
        """Return whether the garage door is closing."""
        return self.device.normalized_state == "closing"

    async def async_open_cover(self) -> None:
        """Open the garage door."""
        await self._command("open")

    async def async_close_cover(self) -> None:
        """Close the garage door."""
        await self._command("close")

    async def _command(self, action: str) -> None:
        """Send a command to the garage door."""
        await self.client.command(
            self.device,
            action,
        )

        await self.coordinator.async_request_refresh()

    async def async_update(self) -> None:
        """Request a device refresh."""
        await self.coordinator.async_request_refresh()

    @property
    def extra_state_attributes(self) -> dict:
        """Return additional device attributes."""
        return {
            "serial_number": self.device.serial_number,
            "door_state": self.device.state,
            "online": self.device.online,
            "wifi_signal_strength": (
                self.device.wifi_signal_strength
            ),
            "fault_codes": self.device.fault_codes,
        }