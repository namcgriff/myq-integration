from __future__ import annotations

import logging
import re

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.config_entries import ConfigFlowResult
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .cloud import MyQCloudClient, MyQVerificationRequired
from .const import (
    CONF_ACCESS_TOKEN,
    CONF_API_HOST,
    CONF_API_VERSION,
    CONF_PASSWORD,
    CONF_REFRESH_TOKEN,
    CONF_USERNAME,
    DEFAULT_API_HOST,
    DEFAULT_API_VERSION,
    DOMAIN,
)
from .exceptions import MyQApiError, MyQAuthError

_LOGGER = logging.getLogger(__name__)


class MyQConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a MyQ config flow."""

    VERSION = 1

    def __init__(self) -> None:
        self._client: MyQCloudClient | None = None
        self._username: str | None = None
        self._password: str | None = None
        self._api_host: str = DEFAULT_API_HOST
        self._api_version: str = DEFAULT_API_VERSION

    async def async_step_user(
        self,
        user_input: dict | None = None,
    ) -> ConfigFlowResult:
        """Handle the initial credentials step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            self._username = user_input[CONF_USERNAME].strip()
            self._password = user_input[CONF_PASSWORD]
            self._api_host = user_input.get(CONF_API_HOST, DEFAULT_API_HOST)
            self._api_version = user_input.get(CONF_API_VERSION, DEFAULT_API_VERSION)

            self._client = MyQCloudClient(
                async_get_clientsession(self.hass),
                self._username,
                self._password,
                self._api_host,
                self._api_version,
            )

            try:
                _LOGGER.info("Starting myQ authentication")
                await self._client.authenticate()
                _LOGGER.info("myQ authentication succeeded")
                return await self._finish()

            except MyQVerificationRequired:
                _LOGGER.info("myQ requires a verification code")
                return await self.async_step_verification()

            except MyQAuthError:
                _LOGGER.exception("myQ authentication failed")
                errors["base"] = "invalid_auth"

            except MyQApiError:
                _LOGGER.exception("myQ API error during authentication")
                errors["base"] = "cannot_connect"

            except Exception:
                _LOGGER.exception("Unexpected myQ authentication error")
                errors["base"] = "unknown"

        schema = vol.Schema(
            {
                vol.Required(CONF_USERNAME): str,
                vol.Required(CONF_PASSWORD): str,
                vol.Optional(CONF_API_HOST, default=DEFAULT_API_HOST): str,
                vol.Optional(CONF_API_VERSION, default=DEFAULT_API_VERSION): str,
            }
        )

        return self.async_show_form(
            step_id="user",
            data_schema=schema,
            errors=errors,
        )

    async def async_step_verification(
        self,
        user_input: dict | None = None,
    ) -> ConfigFlowResult:
        """Handle the MyQ six-digit verification code."""
        errors: dict[str, str] = {}

        if self._client is None:
            return self.async_abort(reason="authentication_expired")

        if user_input is not None:
            code = str(user_input["verification_code"]).strip()

            if not re.fullmatch(r"\d{6}", code):
                errors["base"] = "invalid_mfa"
            else:
                try:
                    _LOGGER.info("Submitting myQ verification code")
                    await self._client.submit_verification_code(code)
                    _LOGGER.info("myQ verification succeeded")
                    return await self._finish()

                except MyQAuthError:
                    _LOGGER.exception("myQ verification code rejected")
                    errors["base"] = "invalid_mfa"

                except MyQApiError:
                    _LOGGER.exception("myQ API error during verification")
                    errors["base"] = "cannot_connect"

                except Exception:
                    _LOGGER.exception("Unexpected error during myQ verification")
                    errors["base"] = "unknown"

        schema = vol.Schema(
            {
                vol.Required("verification_code"): str,
            }
        )

        return self.async_show_form(
            step_id="verification",
            data_schema=schema,
            errors=errors,
        )

    async def _finish(self) -> ConfigFlowResult:
        """Validate the authenticated account and create the entry."""
        if self._client is None:
            return self.async_abort(reason="authentication_expired")

        client = self._client

        try:
            _LOGGER.info("myQ: validating authenticated client by retrieving devices")
            devices = await client.get_devices()

            _LOGGER.info(
                "myQ device retrieval succeeded; found %d devices",
                len(devices),
            )

            if not devices:
                return self.async_abort(reason="no_devices")

            await self.async_set_unique_id(self._username or "myq")
            self._abort_if_unique_id_configured()

            if not client.access_token:
                raise MyQAuthError("MyQ authentication produced no access token")

            data = {
                CONF_USERNAME: self._username,
                CONF_PASSWORD: self._password,
                CONF_API_HOST: self._api_host,
                CONF_API_VERSION: self._api_version,
                CONF_ACCESS_TOKEN: client.access_token,
            }

            if client.refresh_token:
                data[CONF_REFRESH_TOKEN] = client.refresh_token

            _LOGGER.info(
                "myQ: creating config entry for %s with saved OAuth tokens",
                self._username,
            )

            result = self.async_create_entry(
                title=self._username or "MyQ",
                data=data,
            )

            # The config entry will create its own permanent runtime client
            # from the saved tokens. Close the temporary config-flow client.
            await client.close()
            self._client = None

            return result

        except MyQAuthError:
            _LOGGER.exception(
                "myQ authentication failed while validating the account"
            )
            return self.async_abort(reason="invalid_auth")

        except MyQApiError:
            _LOGGER.exception("myQ device retrieval failed")
            return self.async_abort(reason="cannot_connect")

        except Exception:
            _LOGGER.exception("Unexpected myQ device validation failure")
            return self.async_abort(reason="unknown")
