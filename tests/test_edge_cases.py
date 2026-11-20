"""Error paths and less common flows, so every branch has a test."""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch

import pytest
from aiohttp import ClientSession, web
from homeassistant.config_entries import SOURCE_USER, ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.terra_kaffe.api import (
    TerraKaffeAPI,
    TerraKaffeAPIError,
    TerraKaffeAuthError,
)
from custom_components.terra_kaffe.const import (
    BREW_ARM_TIMEOUT,
    DOMAIN,
    MOBILE_API_URL,
    TOKEN_URL,
)

from .conftest import NESTED_REPORTED, make_twin
from .test_integration import (
    COMMAND_URL,
    DEVICE,
    TWIN_URL,
    mock_cloud,
    setup_entry,
)

ARM = "switch.kitchen_tk_arm_remote_brewing"
LATTE = "button.kitchen_tk_brew_morning_latte"


async def _flow(hass, password="pw"):
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    return await hass.config_entries.flow.async_configure(
        result["flow_id"], {"username": "me@example.com", "password": password}
    )


# ------------------------------------------------------------- config flow


async def test_picker_for_several_machines(hass: HomeAssistant, aioclient_mock):
    aioclient_mock.post(
        TOKEN_URL,
        json={"access_token": "acc", "refresh_token": "ref", "expires_in": 120},
    )
    aioclient_mock.get(
        f"{MOBILE_API_URL}/devices/",
        json={"devices": [{"device_id": DEVICE}, {"device_id": "other999999"}]},
    )
    aioclient_mock.get(TWIN_URL, json=make_twin(NESTED_REPORTED))
    aioclient_mock.get(
        f"{MOBILE_API_URL}/devices/other999999/twin", status=404, json={}
    )
    result = await _flow(hass)
    assert result["type"] is FlowResultType.FORM and result["step_id"] == "device"
    with patch("custom_components.terra_kaffe.async_setup_entry", return_value=True):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"device_id": "other999999"}
        )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Terra Kaffe other999999"
    assert result["data"]["device_id"] == "other999999"


async def test_same_machine_twice_aborts(hass: HomeAssistant, aioclient_mock):
    await setup_entry(hass, aioclient_mock)
    result = await _flow(hass)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_cloud_error_during_sign_in(hass: HomeAssistant, aioclient_mock):
    aioclient_mock.post(
        TOKEN_URL,
        json={"access_token": "acc", "refresh_token": "ref", "expires_in": 120},
    )
    aioclient_mock.get(f"{MOBILE_API_URL}/devices/", status=503, text="down")
    assert (await _flow(hass))["errors"] == {"base": "cannot_connect"}


async def test_no_offline_token_is_refused(hass: HomeAssistant, aioclient_mock):
    aioclient_mock.post(TOKEN_URL, json={"access_token": "acc", "expires_in": 120})
    aioclient_mock.get(
        f"{MOBILE_API_URL}/devices/", json={"devices": [{"device_id": DEVICE}]}
    )
    aioclient_mock.get(TWIN_URL, json=make_twin(NESTED_REPORTED))
    assert (await _flow(hass))["errors"] == {"base": "cannot_connect"}


async def test_unexpected_error_during_sign_in(hass: HomeAssistant):
    with patch(
        "custom_components.terra_kaffe.config_flow.TerraKaffeAPI.async_login",
        side_effect=RuntimeError("boom"),
    ):
        assert (await _flow(hass))["errors"] == {"base": "unknown"}


async def test_reauth_wrong_account_then_bad_password(
    hass: HomeAssistant, aioclient_mock
):
    entry = await setup_entry(hass, aioclient_mock)
    aioclient_mock.clear_requests()
    aioclient_mock.post(
        TOKEN_URL,
        json={"access_token": "acc", "refresh_token": "ref", "expires_in": 120},
    )
    aioclient_mock.get(
        f"{MOBILE_API_URL}/devices/", json={"devices": [{"device_id": "other999999"}]}
    )
    aioclient_mock.get(
        f"{MOBILE_API_URL}/devices/other999999/twin", json=make_twin(NESTED_REPORTED)
    )
    result = await entry.start_reauth_flow(hass)
    assert result["step_id"] == "reauth_confirm"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"username": "someone@else.com", "password": "pw"}
    )
    assert result["errors"] == {"base": "wrong_account"}
    aioclient_mock.clear_requests()
    aioclient_mock.post(TOKEN_URL, status=401, json={"error": "invalid_grant"})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"username": "me@example.com", "password": "bad"}
    )
    assert result["errors"] == {"base": "invalid_auth"}


# ------------------------------------------------------------- setup


async def test_afero_era_entry_is_not_migrated(hass: HomeAssistant):
    entry = MockConfigEntry(domain=DOMAIN, version=1, data={"device_id": DEVICE})
    entry.add_to_hass(hass)
    assert not await hass.config_entries.async_setup(entry.entry_id)
    assert entry.state is ConfigEntryState.MIGRATION_ERROR


async def test_rotated_refresh_token_is_saved(hass: HomeAssistant, aioclient_mock):
    entry = await setup_entry(hass, aioclient_mock)
    assert entry.data["refresh_token"] == "ref"
    runtime = entry.runtime_data
    runtime.api._expires_at = 0
    aioclient_mock.clear_requests()
    mock_cloud(aioclient_mock)
    aioclient_mock.clear_requests()
    aioclient_mock.post(
        TOKEN_URL,
        json={"access_token": "acc2", "refresh_token": "ref2", "expires_in": 120},
    )
    aioclient_mock.get(TWIN_URL, json=make_twin(NESTED_REPORTED))
    await runtime.async_refresh()
    assert entry.data["refresh_token"] == "ref2"


# ------------------------------------------------------------- actions


async def test_write_errors(hass: HomeAssistant, aioclient_mock):
    entry = await setup_entry(hass, aioclient_mock)
    aioclient_mock.clear_requests()
    aioclient_mock.put(TWIN_URL, status=500, json={"detail": "nope"})
    with pytest.raises(HomeAssistantError, match="rejected the change"):
        await hass.services.async_call(
            "button", "press", {"entity_id": "button.kitchen_tk_wake"}, blocking=True
        )
    aioclient_mock.clear_requests()
    aioclient_mock.post(TOKEN_URL, status=401, json={"error": "invalid_grant"})
    aioclient_mock.put(TWIN_URL, status=403, json={"detail": "forbidden"})
    with pytest.raises(HomeAssistantError, match="sign-in expired"):
        await hass.services.async_call(
            "button", "press", {"entity_id": "button.kitchen_tk_sleep"}, blocking=True
        )
    await hass.async_block_till_done()
    assert any(
        f["context"]["source"] == "reauth"
        for f in hass.config_entries.flow.async_progress()
    )
    assert entry.state is ConfigEntryState.LOADED


async def test_cancel_and_brew_command_errors(hass: HomeAssistant, aioclient_mock):
    await setup_entry(hass, aioclient_mock)
    aioclient_mock.clear_requests()
    aioclient_mock.post(COMMAND_URL, json={"success": False, "message": "Machine busy"})
    with pytest.raises(HomeAssistantError, match="Machine busy"):
        await hass.services.async_call(
            "button",
            "press",
            {"entity_id": "button.kitchen_tk_cancel_brew"},
            blocking=True,
        )
    await hass.services.async_call(
        "switch", "turn_on", {"entity_id": ARM}, blocking=True
    )
    with pytest.raises(HomeAssistantError, match="Could not start the drink"):
        await hass.services.async_call(
            "button", "press", {"entity_id": LATTE}, blocking=True
        )
    # The arm is spent even though the order failed: no silent retry.
    assert hass.states.get(ARM).state == "off"


async def test_arm_times_out_and_turns_off(hass: HomeAssistant, aioclient_mock):
    await setup_entry(hass, aioclient_mock)
    await hass.services.async_call(
        "switch", "turn_on", {"entity_id": ARM}, blocking=True
    )
    assert hass.states.get(ARM).state == "on"
    async_fire_time_changed(
        hass, dt_util.utcnow() + timedelta(seconds=BREW_ARM_TIMEOUT + 1)
    )
    await hass.async_block_till_done()
    assert hass.states.get(ARM).state == "off"
    await hass.services.async_call(
        "switch", "turn_on", {"entity_id": ARM}, blocking=True
    )
    await hass.services.async_call(
        "switch", "turn_off", {"entity_id": ARM}, blocking=True
    )
    assert hass.states.get(ARM).state == "off"


async def test_empty_slot_button(hass: HomeAssistant, aioclient_mock):
    entry = await setup_entry(hass, aioclient_mock)
    from custom_components.terra_kaffe.button import TerraKaffeSavedDrinkButton

    # Empty slots are disabled by default, so build one directly.
    button = TerraKaffeSavedDrinkButton(entry.runtime_data, 3)
    assert button.entity_registry_enabled_default is False
    assert button.name == "Saved drink 3 (empty)"
    assert button.available is False
    assert button.extra_state_attributes is None
    with pytest.raises(HomeAssistantError, match="empty"):
        await button.async_press()
    assert entry.state is ConfigEntryState.LOADED


async def test_service_target_selection(hass: HomeAssistant, aioclient_mock):
    entry = await setup_entry(hass, aioclient_mock)
    with pytest.raises(ServiceValidationError, match="No loaded"):
        await hass.services.async_call(
            DOMAIN,
            "brew_recipe",
            {"recipe": "$50|e1", "config_entry_id": "nope"},
            blocking=True,
        )
    second = MockConfigEntry(
        domain=DOMAIN,
        version=2,
        minor_version=2,
        unique_id="other999999",
        data={
            "username": "x@example.com",
            "refresh_token": "ref",
            "device_id": "other999999",
        },
    )
    aioclient_mock.get(
        f"{MOBILE_API_URL}/devices/other999999/twin", json=make_twin(NESTED_REPORTED)
    )
    second.add_to_hass(hass)
    assert await hass.config_entries.async_setup(second.entry_id)
    await hass.async_block_till_done()
    with pytest.raises(ServiceValidationError, match="More than one"):
        await hass.services.async_call(
            DOMAIN, "brew_recipe", {"recipe": "$50|e1"}, blocking=True
        )
    with pytest.raises(HomeAssistantError, match="not armed"):
        await hass.services.async_call(
            DOMAIN,
            "brew_recipe",
            {"recipe": "$50|e1", "config_entry_id": entry.entry_id},
            blocking=True,
        )
    assert await hass.config_entries.async_unload(entry.entry_id)


# ------------------------------------------------------------- API client edges


@pytest.fixture
async def odd_cloud(aiohttp_server, socket_enabled):
    state = {
        "token": {"access_token": "acc", "refresh_token": "ref"},
        "token_status": 200,
    }

    async def token(request):
        if state["token_status"] != 200:
            return web.Response(status=state["token_status"], text="<html>oops</html>")
        return web.json_response(state["token"])

    async def api(request):
        path = request.path
        if path == "/api/devices/":
            return web.json_response(
                [{"device_id": "abc123"}, {"device_id": "bad/id"}, "junk"]
            )
        if path == "/api/devices/abc123/twin":
            return web.json_response(["not", "a", "dict"])
        if path == "/api/devices/abc123/command":
            return web.Response(status=403, text="")
        return web.Response(status=500, text="plain text")

    app = web.Application()
    app.router.add_post("/token", token)
    app.router.add_route("*", "/api/{tail:.*}", api)
    server = await aiohttp_server(app)
    async with ClientSession() as session:
        yield (
            state,
            TerraKaffeAPI(
                session,
                token_url=str(server.make_url("/token")),
                api_url=str(server.make_url("/api")),
            ),
        )


async def test_api_odd_responses(odd_cloud):
    state, api = odd_cloud
    await api.async_login("me@example.com", "pw")  # no expires_in: default lifetime
    assert await api.async_get_devices() == [{"device_id": "abc123"}]
    with pytest.raises(TerraKaffeAPIError, match="Unexpected twin"):
        await api.async_get_twin("abc123")
    with pytest.raises(TerraKaffeAuthError, match="403"):
        await api.async_cancel_brew("abc123")
    with pytest.raises(TerraKaffeAPIError, match="500"):
        await api.async_update_desired("zzz", {"a": 1})
    state["token_status"] = 502
    api._expires_at = 0
    with pytest.raises(TerraKaffeAPIError, match="Sign-in failed \\(502\\)"):
        await api.async_get_devices()
