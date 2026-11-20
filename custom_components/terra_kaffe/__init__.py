"""The Terra Kaffe integration (TK Cloud 2.0)."""

from __future__ import annotations

import voluptuous as vol
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_PASSWORD, Platform
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.typing import ConfigType

from .api import TerraKaffeAPI
from .const import (
    ATTR_CONFIG_ENTRY_ID,
    ATTR_NAME,
    ATTR_RECIPE,
    CONF_REFRESH_TOKEN,
    DOMAIN,
    SERVICE_BREW_RECIPE,
)
from .coordinator import TerraKaffeConfigEntry, TerraKaffeDataUpdateCoordinator
from .twin import Recipe, clean_drink_name, is_recipe_string

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.SENSOR,
    Platform.SWITCH,
]

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

BREW_RECIPE_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_RECIPE): cv.string,
        vol.Optional(ATTR_NAME, default="Home Assistant"): cv.string,
        vol.Optional(ATTR_CONFIG_ENTRY_ID): cv.string,
    }
)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register services."""

    async def _brew_recipe(call: ServiceCall) -> None:
        recipe = call.data[ATTR_RECIPE].strip()
        if not is_recipe_string(recipe):
            raise ServiceValidationError(
                "Recipe must look like $53|e1|f115 (a $type id, then |key+value parts)."
            )
        name = clean_drink_name(call.data[ATTR_NAME])
        coordinator = _coordinator_for(hass, call.data.get(ATTR_CONFIG_ENTRY_ID))
        await coordinator.async_brew(Recipe(uid="ha", name=name, recipe=recipe))

    hass.services.async_register(
        DOMAIN, SERVICE_BREW_RECIPE, _brew_recipe, schema=BREW_RECIPE_SCHEMA
    )
    return True


def _coordinator_for(
    hass: HomeAssistant, entry_id: str | None
) -> TerraKaffeDataUpdateCoordinator:
    entries = [
        entry
        for entry in hass.config_entries.async_entries(DOMAIN)
        if entry.state is ConfigEntryState.LOADED
    ]
    if entry_id:
        entries = [entry for entry in entries if entry.entry_id == entry_id]
    if not entries:
        raise ServiceValidationError("No loaded Terra Kaffe machine matches.")
    if len(entries) > 1:
        raise ServiceValidationError(
            "More than one Terra Kaffe machine is set up; pass config_entry_id."
        )
    return entries[0].runtime_data


async def async_setup_entry(hass: HomeAssistant, entry: TerraKaffeConfigEntry) -> bool:
    """Set up Terra Kaffe from a config entry."""
    api = TerraKaffeAPI(async_get_clientsession(hass))
    # Only the offline refresh token is kept. If Terra Kaffe revokes it (for
    # example after a password change) Home Assistant asks for the password.
    api.set_refresh_token(entry.data.get(CONF_REFRESH_TOKEN))

    @callback
    def _persist_refresh_token(token: str) -> None:
        if entry.data.get(CONF_REFRESH_TOKEN) != token:
            hass.config_entries.async_update_entry(
                entry, data={**entry.data, CONF_REFRESH_TOKEN: token}
            )

    api.on_refresh_token_update = _persist_refresh_token

    coordinator = TerraKaffeDataUpdateCoordinator(hass, entry, api)
    entry.runtime_data = coordinator
    # Raises ConfigEntryAuthFailed / ConfigEntryNotReady as appropriate.
    await coordinator.async_config_entry_first_refresh()
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: TerraKaffeConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_migrate_entry(
    hass: HomeAssistant, entry: TerraKaffeConfigEntry
) -> bool:
    """Entries from the retired Afero platform cannot be converted.

    Afero device ids and tokens mean nothing to the new cloud, so the entry
    has to be removed and added again.

    2.1 -> 2.2: stop storing the account password; the offline refresh
    token is enough, and reauth covers a revoked token.
    """
    if entry.version < 2:
        return False
    if entry.version == 2 and entry.minor_version < 2:
        data = {k: v for k, v in entry.data.items() if k != CONF_PASSWORD}
        hass.config_entries.async_update_entry(entry, data=data, minor_version=2)
    return True
