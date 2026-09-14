from __future__ import annotations

from dataclasses import dataclass

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .cloud import MyQCloudClient
from .const import (
    CONF_ACCESS_TOKEN,
    CONF_API_HOST,
    CONF_API_VERSION,
    CONF_REFRESH_TOKEN,
    DEFAULT_API_HOST,
    DEFAULT_API_VERSION,
    DOMAIN,
    PLATFORMS,
)
from .coordinator import MyQCoordinator
from .exceptions import MyQAuthError


@dataclass
class MyQRuntimeData:
    client: MyQCloudClient
    coordinator: MyQCoordinator


MyQConfigEntry = ConfigEntry[MyQRuntimeData]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: MyQConfigEntry,
) -> bool:
    """Set up myQ from a saved config entry."""
    settings = {**entry.data, **entry.options}

    async def _save_tokens(
        access_token: str | None,
        refresh_token: str | None,
    ) -> None:
        """Persist tokens whenever the cloud client obtains new ones."""
        data = dict(entry.data)

        if access_token:
            data[CONF_ACCESS_TOKEN] = access_token

        if refresh_token:
            data[CONF_REFRESH_TOKEN] = refresh_token

        hass.config_entries.async_update_entry(
            entry,
            data=data,
        )

    client = MyQCloudClient(
        async_get_clientsession(hass),
        settings[CONF_USERNAME],
        settings[CONF_PASSWORD],
        settings.get(CONF_API_HOST, DEFAULT_API_HOST),
        settings.get(CONF_API_VERSION, DEFAULT_API_VERSION),
        access_token=settings.get(CONF_ACCESS_TOKEN),
        refresh_token=settings.get(CONF_REFRESH_TOKEN),
        token_update_callback=_save_tokens,
    )

    coordinator = MyQCoordinator(hass, client)

    try:
        # If a saved access token exists, use it directly. If it has expired,
        # the cloud client will use the saved refresh token instead.
        if client.access_token:
            try:
                await coordinator.async_config_entry_first_refresh()
            except MyQAuthError:
                client.access_token = None
                await client.authenticate()
                await coordinator.async_config_entry_first_refresh()
        else:
            await client.authenticate()
            await coordinator.async_config_entry_first_refresh()

    except MyQAuthError as err:
        raise ConfigEntryAuthFailed from err

    except Exception:
        await client.close()
        raise

    entry.runtime_data = MyQRuntimeData(
        client,
        coordinator,
    )

    await hass.config_entries.async_forward_entry_setups(
        entry,
        PLATFORMS,
    )

    return True


async def async_unload_entry(
    hass: HomeAssistant,
    entry: MyQConfigEntry,
) -> bool:
    """Unload myQ."""
    unloaded = await hass.config_entries.async_unload_platforms(
        entry,
        PLATFORMS,
    )

    if unloaded:
        await entry.runtime_data.client.close()

    return unloaded
