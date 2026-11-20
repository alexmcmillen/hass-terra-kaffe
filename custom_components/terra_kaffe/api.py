"""Client for the Terra Kaffe cloud (TK Cloud 2.0).

Auth is Keycloak (realm ``tkf``, public client ``tk_android``): a password
grant returns a short-lived access token and an offline refresh token.
Device state is an Azure IoT Hub device twin served by the "mercury" API.

This module only depends on aiohttp, so it can be tested without Home
Assistant.
"""

from __future__ import annotations

import asyncio
import re
import time
from collections.abc import Callable
from typing import Any

from aiohttp import ClientError, ClientSession, ClientTimeout

from .const import (
    AUTH_SCOPE,
    CLIENT_ID,
    LOGGER,
    MOBILE_API_URL,
    REQUEST_TIMEOUT,
    TOKEN_URL,
)

# Refresh this many seconds before the access token actually expires.
TOKEN_EXPIRY_MARGIN = 30

# Device ids are opaque hex strings from the device list. Anything else is
# refused before it can be spliced into a URL path.
_DEVICE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def validate_device_id(device_id: str) -> str:
    """Return ``device_id`` if it is safe to put in a URL path."""
    if not isinstance(device_id, str) or not _DEVICE_ID_RE.fullmatch(device_id):
        raise TerraKaffeAPIError("Invalid device id")
    return device_id


class TerraKaffeAPIError(Exception):
    """Base exception for Terra Kaffe API errors."""


class TerraKaffeAuthError(TerraKaffeAPIError):
    """Credentials or tokens were rejected."""


class TerraKaffeNotFoundError(TerraKaffeAPIError):
    """The device does not exist or is not on this account."""


class TerraKaffeAPI:
    """Async client for Terra Kaffe's auth and device APIs."""

    def __init__(
        self,
        session: ClientSession,
        *,
        token_url: str = TOKEN_URL,
        api_url: str = MOBILE_API_URL,
    ) -> None:
        """Initialize the client."""
        self._session = session
        self._token_url = token_url
        self._api_url = api_url.rstrip("/")
        self._timeout = ClientTimeout(total=REQUEST_TIMEOUT, connect=10)
        self._access_token: str | None = None
        self._refresh_token: str | None = None
        self._expires_at: float = 0.0
        self._username: str | None = None
        self._password: str | None = None
        self._auth_lock = asyncio.Lock()
        # Called with the new refresh token whenever Keycloak rotates it.
        self.on_refresh_token_update: Callable[[str], None] | None = None

    # ------------------------------------------------------------------ auth

    @property
    def refresh_token(self) -> str | None:
        """Return the current refresh token."""
        return self._refresh_token

    def set_credentials(self, username: str, password: str) -> None:
        """Store credentials used when the refresh token stops working."""
        self._username = username
        self._password = password

    def set_refresh_token(self, refresh_token: str | None) -> None:
        """Seed a stored refresh token (does not fire the update callback)."""
        self._refresh_token = refresh_token

    async def async_login(self, username: str, password: str) -> None:
        """Sign in with email and password."""
        self.set_credentials(username, password)
        async with self._auth_lock:
            await self._password_grant()

    async def _token_request(self, form: dict[str, str]) -> dict[str, Any]:
        try:
            async with self._session.post(
                self._token_url,
                data=form,
                timeout=self._timeout,
                allow_redirects=False,
            ) as response:
                status = response.status
                try:
                    body = await response.json(content_type=None)
                except ValueError:
                    body = None
        except (ClientError, TimeoutError) as err:
            raise TerraKaffeAPIError(
                f"Could not reach Terra Kaffe sign-in: {err}"
            ) from err

        if status == 200 and isinstance(body, dict) and body.get("access_token"):
            return body
        detail = ""
        if isinstance(body, dict):
            detail = str(body.get("error_description") or body.get("error") or "")
        if status in (400, 401, 403):
            # Keycloak answers 400/401 invalid_grant for bad credentials and
            # for expired or revoked refresh tokens.
            raise TerraKaffeAuthError(detail or f"Sign-in rejected ({status})")
        raise TerraKaffeAPIError(f"Sign-in failed ({status}) {detail}".strip())

    def _store_tokens(self, body: dict[str, Any]) -> None:
        self._access_token = body["access_token"]
        expires_in = body.get("expires_in")
        try:
            lifetime = float(expires_in)
        except (TypeError, ValueError):
            lifetime = 60.0
        self._expires_at = time.monotonic() + max(5.0, lifetime - TOKEN_EXPIRY_MARGIN)
        new_refresh = body.get("refresh_token")
        if new_refresh and new_refresh != self._refresh_token:
            self._refresh_token = new_refresh
            if self.on_refresh_token_update is not None:
                self.on_refresh_token_update(new_refresh)

    async def _password_grant(self) -> None:
        if not self._username or not self._password:
            raise TerraKaffeAuthError("No credentials available")
        body = await self._token_request(
            {
                "grant_type": "password",
                "client_id": CLIENT_ID,
                "username": self._username,
                "password": self._password,
                "scope": AUTH_SCOPE,
            }
        )
        self._store_tokens(body)

    async def _refresh_grant(self) -> None:
        if not self._refresh_token:
            raise TerraKaffeAuthError("No refresh token available")
        body = await self._token_request(
            {
                "grant_type": "refresh_token",
                "client_id": CLIENT_ID,
                "refresh_token": self._refresh_token,
            }
        )
        self._store_tokens(body)

    async def _renew(self) -> None:
        """Get a fresh access token: refresh token first, then password."""
        if self._refresh_token:
            try:
                await self._refresh_grant()
            except TerraKaffeAuthError as err:
                LOGGER.debug("Refresh token rejected, using password: %s", err)
            else:
                return
        await self._password_grant()

    async def _ensure_token(self, *, force: bool = False) -> str:
        async with self._auth_lock:
            expired = time.monotonic() >= self._expires_at
            if force or not self._access_token or expired:
                await self._renew()
            assert self._access_token is not None
            return self._access_token

    # -------------------------------------------------------------- requests

    async def _request(self, method: str, path: str, *, json_body: Any = None) -> Any:
        url = f"{self._api_url}/{path.lstrip('/')}"
        for attempt in (1, 2):
            token = await self._ensure_token(force=attempt == 2)
            try:
                async with self._session.request(
                    method,
                    url,
                    json=json_body,
                    headers={
                        "Authorization": f"Bearer {token}",
                        "Accept": "application/json",
                    },
                    timeout=self._timeout,
                    # Never forward the bearer token to another host.
                    allow_redirects=False,
                ) as response:
                    status = response.status
                    try:
                        body = await response.json(content_type=None)
                    except ValueError:
                        body = None
            except (ClientError, TimeoutError) as err:
                raise TerraKaffeAPIError(f"Could not reach Terra Kaffe: {err}") from err

            if status == 401 and attempt == 1:
                continue  # token expired early or was revoked: renew once
            if 200 <= status < 300:
                return body
            detail = ""
            if isinstance(body, dict):
                detail = str(
                    body.get("error") or body.get("message") or body.get("detail") or ""
                )
            if status in (401, 403):
                raise TerraKaffeAuthError(detail or f"Not authorised ({status})")
            if status == 404:
                raise TerraKaffeNotFoundError(detail or "Device not found")
            raise TerraKaffeAPIError(
                f"{method} {path} failed ({status}) {detail}".strip()
            )
        raise TerraKaffeAPIError("Request failed")  # pragma: no cover

    # ---------------------------------------------------------------- device

    async def async_get_devices(self) -> list[dict[str, Any]]:
        """Return the machines on this account."""
        body = await self._request("GET", "devices/")
        if isinstance(body, dict):
            body = body.get("devices")
        if not isinstance(body, list):
            return []
        return [
            d
            for d in body
            if isinstance(d, dict)
            and isinstance(d.get("device_id"), str)
            and _DEVICE_ID_RE.fullmatch(d["device_id"])
        ]

    async def async_get_twin(self, device_id: str) -> dict[str, Any]:
        """Return the device twin (state) for a machine."""
        validate_device_id(device_id)
        body = await self._request("GET", f"devices/{device_id}/twin")
        if not isinstance(body, dict):
            raise TerraKaffeAPIError("Unexpected twin response")
        return body

    async def async_update_desired(
        self, device_id: str, desired: dict[str, Any]
    ) -> dict[str, Any] | None:
        """Patch desired properties on the twin (merge semantics)."""
        validate_device_id(device_id)
        body = await self._request(
            "PUT", f"devices/{device_id}/twin", json_body={"desired": desired}
        )
        return body if isinstance(body, dict) else None

    async def _command(self, device_id: str, payload: dict[str, Any]) -> None:
        validate_device_id(device_id)
        body = await self._request(
            "POST", f"devices/{device_id}/command", json_body=payload
        )
        if isinstance(body, dict) and body.get("success") is False:
            raise TerraKaffeAPIError(
                str(body.get("message") or "The machine rejected the command")
            )

    async def async_send_drink_order(self, device_id: str, order: str) -> None:
        """Start a drink. ``order`` is ``id^epoch^name^recipe[^profileId]``."""
        await self._command(device_id, {"drink_order": order})

    async def async_cancel_brew(self, device_id: str) -> None:
        """Cancel the drink being made."""
        await self._command(device_id, {"cancel_brew": True})
