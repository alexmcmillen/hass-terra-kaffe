"""Data update coordinator for Terra Kaffe."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import TerraKaffeAPI, TerraKaffeAPIError, TerraKaffeAuthError
from .const import (
    CONF_DEVICE_ID,
    DEFAULT_MODEL,
    DOMAIN,
    LOGGER,
    MANUFACTURER,
    UPDATE_INTERVAL,
)
from .twin import (
    BREW_IDLE_STATES,
    POWER_AWAKE,
    POWER_GOING_TO_SLEEP,
    POWER_GOING_TO_WAKE,
    Recipe,
    build_desired_patch,
    normalize_reported,
    to_int,
    uses_flat_keys,
)

type TerraKaffeConfigEntry = ConfigEntry[TerraKaffeDataUpdateCoordinator]


@dataclass
class TerraKaffeData:
    """One snapshot of the machine."""

    twin: dict[str, Any] = field(default_factory=dict)
    flat_keys: bool = False
    connection_state: str | None = None
    last_activity_time: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def power(self) -> int | None:
        """Power status (0 sleep, 1 awake, 2 waking, 3 going to sleep)."""
        return to_int(self.twin.get("device.status"))

    @property
    def brew_status(self) -> int | None:
        """Brewing status (0 ready ... 5 error)."""
        return to_int(self.twin.get("brew.status"))


class TerraKaffeDataUpdateCoordinator(DataUpdateCoordinator[TerraKaffeData]):
    """Polls the machine's device twin."""

    config_entry: TerraKaffeConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        config_entry: TerraKaffeConfigEntry,
        api: TerraKaffeAPI,
    ) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass,
            LOGGER,
            config_entry=config_entry,
            name=DOMAIN,
            update_interval=timedelta(seconds=UPDATE_INTERVAL),
        )
        self.api = api
        self.device_id: str = config_entry.data[CONF_DEVICE_ID]
        # Remote-brew safety: monotonic deadline until which brewing is
        # allowed. None = not armed. Never persisted, so a restart disarms.
        self.brew_armed_until: float | None = None
        self.brew_arm_switch: Any = None  # set by the switch platform

    async def _async_update_data(self) -> TerraKaffeData:
        try:
            raw = await self.api.async_get_twin(self.device_id)
        except TerraKaffeAuthError as err:
            raise ConfigEntryAuthFailed("Authentication failed") from err
        except TerraKaffeAPIError as err:
            raise UpdateFailed(f"Error communicating with Terra Kaffe: {err}") from err

        reported = raw.get("reported")
        twin = normalize_reported(reported)
        data = TerraKaffeData(
            twin=twin,
            flat_keys=uses_flat_keys(reported),
            connection_state=raw.get("connection_state"),
            last_activity_time=raw.get("last_activity_time"),
            raw=raw,
        )
        LOGGER.debug(
            "Poll OK: %d properties (%s keys), connection=%s power=%s brew=%s",
            len(twin),
            "flat" if data.flat_keys else "nested",
            data.connection_state,
            data.power,
            data.brew_status,
        )
        return data

    # ---------------------------------------------------------------- actions

    async def _write(self, path: str, value: Any) -> None:
        flat = self.data.flat_keys if self.data else False
        patch = build_desired_patch(path, value, flat=flat)
        try:
            await self.api.async_update_desired(self.device_id, patch)
        except TerraKaffeAuthError as err:
            self.config_entry.async_start_reauth(self.hass)
            raise HomeAssistantError("Terra Kaffe sign-in expired") from err
        except TerraKaffeAPIError as err:
            raise HomeAssistantError(f"Terra Kaffe rejected the change: {err}") from err

    async def async_wake(self) -> None:
        """Wake the machine."""
        await self._write("device.status", POWER_GOING_TO_WAKE)
        await self.async_request_refresh()

    async def async_sleep(self) -> None:
        """Put the machine to sleep."""
        await self._write("device.status", POWER_GOING_TO_SLEEP)
        await self.async_request_refresh()

    async def async_cancel_brew(self) -> None:
        """Cancel the drink being made."""
        try:
            await self.api.async_cancel_brew(self.device_id)
        except TerraKaffeAPIError as err:
            raise HomeAssistantError(f"Could not cancel: {err}") from err
        await self.async_request_refresh()

    def check_brew_allowed(self) -> None:
        """Raise unless it is safe and permitted to start a drink right now."""
        armed_until = self.brew_armed_until
        if armed_until is None or time.monotonic() >= armed_until:
            raise HomeAssistantError(
                "Remote brewing is not armed. Turn on the 'Arm remote brewing' "
                "switch first, and make sure a cup is under the spout."
            )
        data = self.data
        if data is None:
            raise HomeAssistantError("Machine state is not known yet.")
        if data.connection_state not in (None, "Connected"):
            raise HomeAssistantError("The machine is offline.")
        if data.power != POWER_AWAKE:
            raise HomeAssistantError(
                "The machine is not awake. Wake it and wait until it is ready."
            )
        if data.brew_status not in BREW_IDLE_STATES:
            raise HomeAssistantError("The machine is busy and cannot start a drink.")

    async def async_brew(self, recipe: Recipe) -> None:
        """Start a drink, subject to the safety checks."""
        self.check_brew_allowed()
        try:
            order = recipe.order_string()
        except ValueError as err:
            raise HomeAssistantError(str(err)) from err
        # One drink per arm: disarm before sending so a retry can't double-brew.
        if self.brew_arm_switch is not None:
            self.brew_arm_switch.disarm()
        else:
            self.brew_armed_until = None
        try:
            await self.api.async_send_drink_order(self.device_id, order)
        except TerraKaffeAPIError as err:
            raise HomeAssistantError(f"Could not start the drink: {err}") from err
        await self.async_request_refresh()

    # ------------------------------------------------------------ device info

    @property
    def device_info(self) -> DeviceInfo:
        """Return device information."""
        twin = self.data.twin if self.data else {}
        serial = twin.get("device.serial")
        name = twin.get("device.friendly_name") or (
            f"Terra Kaffe {serial}" if serial else "Terra Kaffe"
        )
        return DeviceInfo(
            identifiers={(DOMAIN, self.device_id)},
            name=str(name),
            manufacturer=MANUFACTURER,
            model=str(twin.get("device.model") or DEFAULT_MODEL),
            sw_version=_str_or_none(twin.get("device.fw.app") or twin.get("device.os")),
            serial_number=_str_or_none(serial),
        )


def _str_or_none(value: Any) -> str | None:
    return None if value in (None, "") else str(value)
