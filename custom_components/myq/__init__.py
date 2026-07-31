from __future__ import annotations

from dataclasses import dataclass
from homeassistant.config_entries import ConfigEntry
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from .cloud import MyQCloudClient
from .const import CONF_API_HOST, CONF_API_VERSION, DOMAIN, PLATFORMS, DEFAULT_API_HOST, DEFAULT_API_VERSION
from .coordinator import MyQCoordinator


@dataclass
class MyQRuntimeData:
    client: MyQCloudClient
    coordinator: MyQCoordinator


MyQConfigEntry = ConfigEntry[MyQRuntimeData]


async def async_setup_entry(hass: HomeAssistant, entry: MyQConfigEntry) -> bool:
    settings = {**entry.data, **entry.options}
    client = MyQCloudClient(async_get_clientsession(hass), settings[CONF_USERNAME], settings[CONF_PASSWORD], settings.get(CONF_API_HOST, DEFAULT_API_HOST), settings.get(CONF_API_VERSION, DEFAULT_API_VERSION))
    coordinator = MyQCoordinator(hass, client)
    try:
        await client.authenticate()
        await coordinator.async_config_entry_first_refresh()
    except Exception as err:
        from .exceptions import MyQAuthError
        if isinstance(err, MyQAuthError):
            raise ConfigEntryAuthFailed from err
        raise
    entry.runtime_data = MyQRuntimeData(client, coordinator)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: MyQConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
