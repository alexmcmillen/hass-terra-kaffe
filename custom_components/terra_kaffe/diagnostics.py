"""Diagnostics for Terra Kaffe.

The device twin shape is reverse-engineered, so a redacted dump of what the
cloud actually returns is the fastest way to fix a wrong or missing entity.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant

from .const import CONF_DEVICE_ID, CONF_REFRESH_TOKEN
from .coordinator import TerraKaffeConfigEntry

TO_REDACT = {
    CONF_USERNAME,
    CONF_PASSWORD,
    CONF_REFRESH_TOKEN,
    CONF_DEVICE_ID,
    "device_id",
    "serial",
    "code",
    "pin_cd",
    "ssid",
    "wifi_ssid",
    "ip_address",
    "wifi_ip",
    "friendly_name",
    "qr_code",
    "settings.pin.code",
    "wifi.ssid",
    "wifi.ip_address",
    "device.serial",
    "device.friendly_name",
}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: TerraKaffeConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    data = entry.runtime_data.data
    return {
        "entry": async_redact_data(dict(entry.data), TO_REDACT),
        "flat_keys": data.flat_keys if data else None,
        "connection_state": data.connection_state if data else None,
        "last_activity_time": data.last_activity_time if data else None,
        "normalized_twin": async_redact_data(data.twin, TO_REDACT) if data else None,
        "raw_twin": async_redact_data(data.raw, TO_REDACT) if data else None,
    }
