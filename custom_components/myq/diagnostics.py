from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.redact import async_redact_data

TO_REDACT = {"username", "password", "access_token", "refresh_token", "authorization"}


async def async_get_config_entry_diagnostics(hass: HomeAssistant, entry: ConfigEntry):
    runtime = entry.runtime_data
    return async_redact_data({"entry": dict(entry.data), "options": dict(entry.options), "devices": [device.attributes for device in runtime.coordinator.data or []]}, TO_REDACT)
