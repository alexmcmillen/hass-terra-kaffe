"""Security hardening and live-firmware behaviour (TK-02 firmware 39).

The firmware cases replay what a real machine reported on 2026-11-20, so a
change that breaks brewing on real hardware fails here first.
"""

from __future__ import annotations

import json

import pytest
from aiohttp import ClientSession, web
from homeassistant.config_entries import SOURCE_USER, ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.exceptions import ServiceValidationError
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.terra_kaffe.api import (
    TerraKaffeAPI,
    TerraKaffeAPIError,
    validate_device_id,
)
from custom_components.terra_kaffe.const import DOMAIN, MOBILE_API_URL, TOKEN_URL
from custom_components.terra_kaffe.diagnostics import (
    async_get_config_entry_diagnostics,
)
from custom_components.terra_kaffe.twin import (
    clean_drink_name,
    is_recipe_string,
    normalize_reported,
    parse_grind,
)

from .test_integration import COMMAND_URL, DEVICE, calls, mock_cloud, setup_entry

# Shape and values as reported by a TK-02 on firmware 39 (identifiers fake).
LIVE_REPORTED = {
    "dev_sts": 1,
    "brew_sts": 3,
    "cup_cnt": 944,
    "shot_cnt": 2455,
    "milk_cnt": 120,
    "water_cnt": 527664,
    "bean_cnt": 31337,
    "bean_lvl": 2,
    "grind_sz": "2|0",
    "care_sts": 29372972,
    "cln_brew": "382/300",
    "cln_desc": "211/100",
    "cln_fltr": "443/300",
    "cln_wb": "10/9",
    "fw_cb": "39.0.0",
    "fw_hmi": "39.0.0",
    "name": "Kitchen TK",
    "serial": "TK-0000000000",
    "wifi_ssid": "HomeNet",
    "wifi_ip": "10.0.0.5",
    "pin_cd": "4321",
    "drink_02": "TK510000000=^1764332835^Americano^$51|e3|w230^TKep0100000=",
    "drink_07": "TK000000000=^1643139737^Hot Water^$0|w100:t3",
    "drink_10": "1LtvprPuUR0=^1766325207^Custom^$10|s1|d12|x200|t2|g3|b10",
    "history": "TK510000000=^1764332835^Americano^$51|e3|w230^TKep0100000=",
    "analytics": "opaque",
    "$metadata": {"dev_sts": {"$lastUpdated": "2026-11-20T13:42:00Z"}},
}


# ------------------------------------------------------------- twin helpers


def test_live_flat_keys_normalise():
    twin = normalize_reported(LIVE_REPORTED)
    assert twin["stats.beans"] == 31337
    assert twin["levels.grind"] == "2|0"
    assert twin["brew.status"] == 3


@pytest.mark.parametrize(
    ("value", "expected"),
    [("2|0", 2), ("4", 4), (5, 5), ("|", None), (None, None), ("x|1", None)],
)
def test_parse_grind(value, expected):
    assert parse_grind(value) == expected


@pytest.mark.parametrize(
    "recipe",
    [
        "$53|e1|f115",
        "$0|w100:t3",
        "$10|s1|d12|x200|t2|g3|b10",
        "$51|e3|w230",
        "$100|d10|x50|t4|g2",
    ],
)
def test_real_recipes_accepted(recipe):
    assert is_recipe_string(recipe)


@pytest.mark.parametrize(
    "recipe",
    [
        "$53|e1^evil",
        "$53|e1|f115\nX",
        "$53|e 1",
        "$53||e1",
        "$|e1",
        "$12345|e1",
        "53|e1",
        "$53|e1|" + "f1|" * 20,
        "$53|1e",
        "$53|e1;rm",
        "$53|é1",
    ],
)
def test_hostile_recipes_rejected(recipe):
    assert not is_recipe_string(recipe)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Latte", "Latte"),
        ("Dou^ble", "Dou ble"),
        ("a|b$c", "a b c"),
        ("line\nbreak\x00", "line break"),
        ("", "Home Assistant"),
        ("   ", "Home Assistant"),
        ("x" * 80, "x" * 32),
    ],
)
def test_clean_drink_name(raw, expected):
    assert clean_drink_name(raw) == expected


@pytest.mark.parametrize("device_id", ["0123456789ABCDEF", "dev123456", "a-b_c"])
def test_valid_device_ids(device_id):
    assert validate_device_id(device_id) == device_id


@pytest.mark.parametrize(
    "device_id",
    ["", "../admin", "a/b", "a?x=1", "a#b", "a b", "a%2F", "x" * 65, None, 123],
)
def test_invalid_device_ids(device_id):
    with pytest.raises(TerraKaffeAPIError):
        validate_device_id(device_id)


# ------------------------------------------------------------- API client


@pytest.fixture
async def redirecting_cloud(aiohttp_server, socket_enabled):
    """A mercury stand-in that tries to bounce requests to another host."""
    seen = {"evil": []}

    async def token(request):
        return web.json_response(
            {"access_token": "acc", "refresh_token": "ref", "expires_in": 120}
        )

    async def evil(request):
        seen["evil"].append(dict(request.headers))
        return web.json_response({"devices": []})

    evil_app = web.Application()
    evil_app.router.add_route("*", "/{tail:.*}", evil)
    evil_server = await aiohttp_server(evil_app)

    async def api(request):
        raise web.HTTPFound(str(evil_server.make_url("/steal")))

    app = web.Application()
    app.router.add_post("/token", token)
    app.router.add_route("*", "/api/{tail:.*}", api)
    server = await aiohttp_server(app)
    async with ClientSession() as session:
        client = TerraKaffeAPI(
            session,
            token_url=str(server.make_url("/token")),
            api_url=str(server.make_url("/api")),
        )
        yield seen, client


async def test_bearer_token_never_follows_redirects(redirecting_cloud):
    seen, api = redirecting_cloud
    await api.async_login("me@example.com", "pw")
    with pytest.raises(TerraKaffeAPIError, match="302"):
        await api.async_get_devices()
    assert seen["evil"] == []


async def test_bad_device_id_never_reaches_network(redirecting_cloud):
    seen, api = redirecting_cloud
    await api.async_login("me@example.com", "pw")
    for call in (
        api.async_get_twin("../../admin"),
        api.async_update_desired("a/b", {"x": 1}),
        api.async_send_drink_order("a?b", "x"),
        api.async_cancel_brew(""),
    ):
        with pytest.raises(TerraKaffeAPIError, match="Invalid device id"):
            await call
    assert seen["evil"] == []


# ------------------------------------------------------------- inside HA


async def test_password_is_not_stored(hass: HomeAssistant, aioclient_mock):
    mock_cloud(aioclient_mock)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"username": "me@example.com", "password": "s3cret"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert "password" not in result["data"]
    assert "s3cret" not in json.dumps(result["data"])


async def test_old_entry_password_is_purged(hass: HomeAssistant, aioclient_mock):
    entry = await setup_entry(hass, aioclient_mock)
    assert entry.state is ConfigEntryState.LOADED
    assert "password" not in entry.data
    assert entry.minor_version == 2


async def test_revoked_token_asks_for_password(hass: HomeAssistant, aioclient_mock):
    aioclient_mock.post(TOKEN_URL, status=400, json={"error": "invalid_grant"})
    entry = MockConfigEntry(
        domain=DOMAIN,
        version=2,
        minor_version=2,
        unique_id=DEVICE,
        data={
            "username": "me@example.com",
            "refresh_token": "revoked",
            "device_id": DEVICE,
        },
    )
    entry.add_to_hass(hass)
    assert not await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    # Only one token request: there is no stored password to fall back on.
    assert len(calls(aioclient_mock, "POST", TOKEN_URL)) == 1
    assert any(
        f["context"]["source"] == "reauth"
        for f in hass.config_entries.flow.async_progress()
    )


async def test_reauth_does_not_store_password(hass: HomeAssistant, aioclient_mock):
    entry = await setup_entry(hass, aioclient_mock)
    result = await entry.start_reauth_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"username": "me@example.com", "password": "n3w"}
    )
    assert result["reason"] == "reauth_successful"
    assert "password" not in entry.data


async def test_unsafe_device_ids_hidden_from_picker(
    hass: HomeAssistant, aioclient_mock
):
    aioclient_mock.post(
        TOKEN_URL,
        json={"access_token": "acc", "refresh_token": "ref", "expires_in": 120},
    )
    aioclient_mock.get(
        f"{MOBILE_API_URL}/devices/",
        json={"devices": [{"device_id": "../admin"}, {"device_id": 7}, {}]},
    )
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"username": "me@example.com", "password": "pw"}
    )
    assert result["errors"] == {"base": "no_devices"}


async def test_live_firmware_entities(hass: HomeAssistant, aioclient_mock):
    await setup_entry(hass, aioclient_mock, reported=LIVE_REPORTED)
    states = {s.entity_id: s for s in hass.states.async_all()}
    by = lambda suffix: next((s for e, s in states.items() if e.endswith(suffix)), None)
    assert by("_brewing_status").state == "done"
    assert by("_power_status").state == "awake"
    assert by("_grind_setting").state == "2"
    assert by("_brew_unit_cleaning_remaining").state == "0"
    # Values this firmware does not report get no entity at all.
    assert by("_brewing_progress") is None
    assert by("_waste_bin_level") is None
    assert by("_screen_brightness") is None
    assert by("_brew_americano") is not None
    assert by("_brew_hot_water") is not None


async def test_can_brew_again_after_a_finished_drink(
    hass: HomeAssistant, aioclient_mock
):
    """brew_sts 3 is what the machine reports after every drink."""
    await setup_entry(hass, aioclient_mock, reported=LIVE_REPORTED)
    button = next(
        s.entity_id
        for s in hass.states.async_all("button")
        if s.entity_id.endswith("_brew_americano")
    )
    switch = next(s.entity_id for s in hass.states.async_all("switch"))
    await hass.services.async_call(
        "switch", "turn_on", {"entity_id": switch}, blocking=True
    )
    await hass.services.async_call(
        "button", "press", {"entity_id": button}, blocking=True
    )
    order = calls(aioclient_mock, "POST", COMMAND_URL)[0][2]["drink_order"]
    uid, _stamp, name, recipe, profile = order.split("^")
    assert (uid, name, recipe, profile) == (
        "TK510000000=",
        "Americano",
        "$51|e3|w230",
        "TKep0100000=",
    )
    assert len(order) <= 90


@pytest.mark.parametrize("dev_sts", [0, 2, 3])
async def test_brew_waits_for_fully_awake(hass: HomeAssistant, aioclient_mock, dev_sts):
    """2 (warming up) and 3 (going to sleep) must not brew."""
    from homeassistant.exceptions import HomeAssistantError

    await setup_entry(
        hass, aioclient_mock, reported={**LIVE_REPORTED, "dev_sts": dev_sts}
    )
    button = next(
        s.entity_id
        for s in hass.states.async_all("button")
        if s.entity_id.endswith("_brew_americano")
    )
    switch = next(s.entity_id for s in hass.states.async_all("switch"))
    await hass.services.async_call(
        "switch", "turn_on", {"entity_id": switch}, blocking=True
    )
    with pytest.raises(HomeAssistantError, match="not awake"):
        await hass.services.async_call(
            "button", "press", {"entity_id": button}, blocking=True
        )
    assert calls(aioclient_mock, "POST", COMMAND_URL) == []


async def test_brew_recipe_service_rejects_injection(
    hass: HomeAssistant, aioclient_mock
):
    await setup_entry(hass, aioclient_mock)
    for recipe in ("$53|e1^x^y", "$53|e1|f115|" + "a1|" * 30, "$53|e1\n|x"):
        with pytest.raises(ServiceValidationError):
            await hass.services.async_call(
                DOMAIN, "brew_recipe", {"recipe": recipe}, blocking=True
            )
    assert calls(aioclient_mock, "POST", COMMAND_URL) == []


async def test_diagnostics_redact_live_twin(hass: HomeAssistant, aioclient_mock):
    entry = await setup_entry(hass, aioclient_mock, reported=LIVE_REPORTED)
    dump = json.dumps(await async_get_config_entry_diagnostics(hass, entry))
    for secret in (
        "TK-0000000000",
        "HomeNet",
        "10.0.0.5",
        "4321",
        "Kitchen TK",
        "opaque",
        "me@example.com",
        DEVICE,
        "ref",
    ):
        assert f'"{secret}"' not in dump, secret
    assert "pw" not in json.dumps(dict(entry.data))
