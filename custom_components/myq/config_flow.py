from __future__ import annotations

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from .cloud import MyQCloudClient
from .const import CONF_API_HOST, CONF_API_VERSION, DEFAULT_API_HOST, DEFAULT_API_VERSION, DOMAIN
from .exceptions import MyQAuthError, MyQError


async def _validate(hass: HomeAssistant, data: dict) -> None:
    client = MyQCloudClient(async_get_clientsession(hass), data[CONF_USERNAME], data[CONF_PASSWORD], data.get(CONF_API_HOST, DEFAULT_API_HOST), data.get(CONF_API_VERSION, DEFAULT_API_VERSION))
    await client.authenticate()
    await client.get_devices()


class MyQConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    @staticmethod
    def async_get_options_flow(config_entry):
        return MyQOptionsFlowHandler()

    async def async_step_user(self, user_input=None):
        errors = {}
        if user_input:
            try:
                await _validate(self.hass, user_input)
            except MyQAuthError:
                errors["base"] = "invalid_auth"
            except MyQError:
                errors["base"] = "cannot_connect"
            else:
                await self.async_set_unique_id(user_input[CONF_USERNAME].lower())
                self._abort_if_unique_id_configured()
                return self.async_create_entry(title=f"myQ ({user_input[CONF_USERNAME]})", data=user_input)
        schema = vol.Schema({vol.Required(CONF_USERNAME): str, vol.Required(CONF_PASSWORD): str, vol.Optional(CONF_API_HOST, default=DEFAULT_API_HOST): str, vol.Optional(CONF_API_VERSION, default=DEFAULT_API_VERSION): str})
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

    async def async_step_reauth(self, entry_data):
        self._reauth_entry = self._get_reauth_entry()
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input=None):
        errors = {}
        if user_input:
            data = {**self._reauth_entry.data, **user_input}
            try:
                await _validate(self.hass, data)
            except MyQAuthError:
                errors["base"] = "invalid_auth"
            except MyQError:
                errors["base"] = "cannot_connect"
            else:
                return self.async_update_reload_and_abort(self._reauth_entry, data=data)
        return self.async_show_form(step_id="reauth_confirm", data_schema=vol.Schema({vol.Required(CONF_PASSWORD): str}), errors=errors)


class MyQOptionsFlowHandler(config_entries.OptionsFlow):
    async def async_step_init(self, user_input=None):
        if user_input:
            return self.async_create_entry(title="", data=user_input)
        schema = vol.Schema({vol.Optional(CONF_API_HOST, default=self.config_entry.data.get(CONF_API_HOST, DEFAULT_API_HOST)): str, vol.Optional(CONF_API_VERSION, default=self.config_entry.data.get(CONF_API_VERSION, DEFAULT_API_VERSION)): str})
        return self.async_show_form(step_id="init", data_schema=schema)
