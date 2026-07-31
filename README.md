# myQ Home Assistant custom integration

This repository contains a cloud-polling Home Assistant custom integration for compatible Chamberlain/LiftMaster myQ garage-door openers.

## Installation

[![Add to HACS](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=dmckeown257&repository=myq-integration&category=integration)

Use the button above to add this repository to HACS, then install **myQ** and restart Home Assistant. Alternatively, add this repository manually as a HACS custom repository with category **Integration**, or copy `custom_components/myq` into the Home Assistant configuration directory as `custom_components/myq`. Add **myQ** from Settings → Devices & services and use the same account as the official myQ application. The API host and version fields are advanced escape hatches for vendor endpoint changes; the APK identifies `https://api.myqdevice.com` and API versions through `v7.0`.

The integration creates a garage cover per GDO and diagnostic sensors for door state and Wi-Fi signal strength. It polls every 30 seconds and uses the official cloud operations for open and close.

## Protocol findings

See [PROJECT_STATE.md](PROJECT_STATE.md). Static analysis of the supplied APK found OAuth at `/connect/token`, production hosts `api.myqdevice.com`/`api.myq-cloud.com`, device listing at `/api/{version}/accounts/{accountId}/devices`, and GDO commands at `/api/{version}/accounts/{accountId}/door_openers/{serialNumber}/open` and `/close`. No stable local LAN protocol was found. BLE code is present for pairing/provisioning and is not exposed as a Home Assistant transport.

The cloud API and APK are vendor-controlled undocumented interfaces. Pin the configured version and host in deployments, expect vendor changes, and do not use the integration to bypass account authorization or garage safety features.
