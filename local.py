from .exceptions import MyQUnsupportedError


class MyQLocalTransport:
    """Reserved local transport boundary; the APK showed BLE provisioning, not LAN control."""

    async def discover(self):
        raise MyQUnsupportedError("No stable local LAN protocol was found in the supplied APK")
