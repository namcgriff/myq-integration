from __future__ import annotations

from datetime import timedelta
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from .cloud import MyQCloudClient
from .const import DOMAIN, SCAN_INTERVAL
from .models import MyQDevice
from .exceptions import MyQAuthError
from homeassistant.exceptions import ConfigEntryAuthFailed


class MyQCoordinator(DataUpdateCoordinator[list[MyQDevice]]):
    def __init__(self, hass, client: MyQCloudClient) -> None:
        super().__init__(hass, logger=__import__("logging").getLogger(__name__), name=DOMAIN, update_interval=timedelta(seconds=SCAN_INTERVAL))
        self.client = client

    async def _async_update_data(self):
        try:
            return await self.client.get_devices()
        except MyQAuthError as err:
            raise ConfigEntryAuthFailed from err
