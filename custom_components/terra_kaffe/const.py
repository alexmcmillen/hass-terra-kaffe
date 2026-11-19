"""Constants for the Terra Kaffe integration (TK Cloud 2.0)."""

from __future__ import annotations

import logging

DOMAIN = "terra_kaffe"
MANUFACTURER = "Terra Kaffe"
DEFAULT_MODEL = "TK-02"
LOGGER = logging.getLogger(__package__)

# Terra Kaffe's own cloud (replaces the Afero platform retired in 2026).
AUTH_BASE_URL = "https://auth.terrakaffeservice.com"
AUTH_REALM = "tkf"
TOKEN_URL = f"{AUTH_BASE_URL}/realms/{AUTH_REALM}/protocol/openid-connect/token"
# Public Keycloak client used by the official app. No client secret.
CLIENT_ID = "tk_android"
AUTH_SCOPE = "openid profile offline_access"
# Device API ("mercury"): device list, twin, commands.
MOBILE_API_URL = "https://mercury.terrakaffeservice.com/api"

CONF_REFRESH_TOKEN = "refresh_token"
CONF_DEVICE_ID = "device_id"

UPDATE_INTERVAL = 30  # seconds
REQUEST_TIMEOUT = 30  # seconds

# Remote brewing must be armed first; it disarms after one drink or this long.
BREW_ARM_TIMEOUT = 600  # seconds

SERVICE_BREW_RECIPE = "brew_recipe"
ATTR_RECIPE = "recipe"
ATTR_NAME = "name"
ATTR_CONFIG_ENTRY_ID = "config_entry_id"

SAVED_DRINK_SLOTS = 10
