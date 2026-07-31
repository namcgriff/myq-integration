from __future__ import annotations

from typing import Any
from urllib.parse import urljoin

from aiohttp import ClientResponseError

from .exceptions import MyQApiError, MyQAuthError
from .models import MyQDevice

CLIENT_ID = "ANDROID_CGI_MYQ"
SCOPE = "MyQ_Residential offline_access"


class MyQCloudClient:
    """Small async client for the API used by the official myQ APK."""

    def __init__(self, session: Any, username: str, password: str, host: str, api_version: str = "v6.0") -> None:
        self.session = session
        self.username = username
        self.password = password
        self.host = host.rstrip("/") + "/"
        self.api_version = api_version
        self.access_token: str | None = None
        self.refresh_token: str | None = None
        self.account_id: str | None = None

    async def authenticate(self) -> None:
        data = {
            "grant_type": "refresh_token" if self.refresh_token else "password",
            "client_id": CLIENT_ID,
            "scope": SCOPE,
        }
        if self.refresh_token:
            data["refresh_token"] = self.refresh_token
        else:
            data.update({"username": self.username, "password": self.password})
        try:
            async with self.session.post(urljoin(self.host, "connect/token"), data=data) as response:
                if response.status in (400, 401, 403):
                    if self.refresh_token:
                        self.refresh_token = None
                        return await self.authenticate()
                    raise MyQAuthError("myQ rejected the credentials")
                response.raise_for_status()
                payload = await response.json()
        except MyQAuthError:
            raise
        except Exception as err:
            raise MyQApiError(f"Unable to reach myQ: {err}") from err
        self.access_token = payload.get("access_token")
        self.refresh_token = payload.get("refresh_token") or self.refresh_token
        if not self.access_token:
            raise MyQAuthError("myQ did not return an access token")

    async def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        if not self.access_token:
            await self.authenticate()
        headers = {"Authorization": f"Bearer {self.access_token}", "Accept": "application/json"}
        headers.update(kwargs.pop("headers", {}))
        try:
            async with self.session.request(method, urljoin(self.host, path.lstrip("/")), headers=headers, **kwargs) as response:
                if response.status in (401, 403):
                    self.access_token = None
                    await self.authenticate()
                    headers["Authorization"] = f"Bearer {self.access_token}"
                    async with self.session.request(method, urljoin(self.host, path.lstrip("/")), headers=headers, **kwargs) as retry:
                        if retry.status in (401, 403):
                            raise MyQAuthError("myQ authentication expired or was rejected")
                        if retry.status >= 400:
                            raise MyQApiError(f"myQ API returned HTTP {retry.status}")
                        if retry.content_length == 0:
                            return {}
                        return await retry.json(content_type=None)
                if response.status >= 400:
                    raise MyQApiError(f"myQ API returned HTTP {response.status}")
                if response.content_length == 0:
                    return {}
                return await response.json(content_type=None)
        except (MyQAuthError, MyQApiError):
            raise
        except ClientResponseError as err:
            raise MyQApiError(str(err)) from err
        except Exception as err:
            raise MyQApiError(f"myQ request failed: {err}") from err

    async def get_accounts(self) -> list[dict[str, Any]]:
        payload = await self._request("GET", f"api/{self.api_version}/accounts")
        if isinstance(payload, list):
            return payload
        return payload.get("accounts", payload.get("items", [])) if isinstance(payload, dict) else []

    async def get_devices(self) -> list[MyQDevice]:
        if not self.account_id:
            accounts = await self.get_accounts()
            if not accounts:
                raise MyQApiError("The myQ account has no accessible homes")
            self.account_id = str(accounts[0].get("id") or accounts[0].get("accountId"))
        payload = await self._request("GET", f"api/{self.api_version}/accounts/{self.account_id}/devices")
        raw = payload if isinstance(payload, list) else payload.get("devices", payload.get("items", [])) if isinstance(payload, dict) else []
        devices = [MyQDevice.from_json(item) for item in raw if isinstance(item, dict)]
        return [device for device in devices if device is not None and (device.attributes.get("productType") or "gdo" in str(device.attributes).lower())]

    async def command(self, device: MyQDevice, action: str) -> None:
        if action not in ("open", "close"):
            raise MyQApiError(f"Unsupported myQ command: {action}")
        if not self.account_id:
            await self.get_devices()
        await self._request("PUT", f"api/{self.api_version}/accounts/{self.account_id}/door_openers/{device.serial_number}/{action}")
