"""End-to-end tests inside Home Assistant with the cloud mocked at HTTP level."""

from __future__ import annotations

import json

import pytest
from homeassistant.config_entries import SOURCE_USER, ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.terra_kaffe.const import DOMAIN, MOBILE_API_URL, TOKEN_URL
from custom_components.terra_kaffe.diagnostics import (
    async_get_config_entry_diagnostics,
)

from .conftest import FLAT_REPORTED, NESTED_REPORTED, make_twin

DEVICE = "dev123456"
TWIN_URL = f"{MOBILE_API_URL}/devices/{DEVICE}/twin"
COMMAND_URL = f"{MOBILE_API_URL}/devices/{DEVICE}/command"


def mock_cloud(aioclient_mock, reported=NESTED_REPORTED, connection_state="Connected"):
    aioclient_mock.post(
        TOKEN_URL,
        json={"access_token": "acc", "refresh_token": "ref", "expires_in": 120},
    )
    aioclient_mock.get(
        f"{MOBILE_API_URL}/devices/",
        json={"devices": [{"device_id": DEVICE, "access_level": "owner"}]},
    )
    aioclient_mock.get(TWIN_URL, json=make_twin(reported, connection_state))
    aioclient_mock.put(TWIN_URL, json=make_twin(reported, connection_state))
    aioclient_mock.post(
        COMMAND_URL,
        json={"success": True, "message": "ok", "device_id": DEVICE, "timestamp": "t"},
    )


def calls(aioclient_mock, method, url):
    return [c for c in aioclient_mock.mock_calls if c[0] == method and str(c[1]) == url]


async def setup_entry(hass, aioclient_mock, **kwargs) -> MockConfigEntry:
    mock_cloud(aioclient_mock, **kwargs)
    entry = MockConfigEntry(
        domain=DOMAIN,
        version=2,
        unique_id=DEVICE,
        data={
            "username": "me@example.com",
            "password": "pw",
            "refresh_token": "ref",
            "device_id": DEVICE,
        },
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def test_config_flow_creates_entry(hass: HomeAssistant, aioclient_mock):
    mock_cloud(aioclient_mock)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"username": " me@example.com ", "password": "pw"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Kitchen TK (123456)"
    assert result["data"] == {
        "username": "me@example.com",
        "refresh_token": "ref",
        "device_id": DEVICE,
    }
    await hass.async_block_till_done()
    assert hass.states.get("sensor.kitchen_tk_power_status").state == "awake"


async def test_config_flow_bad_password(hass: HomeAssistant, aioclient_mock):
    aioclient_mock.post(TOKEN_URL, status=401, json={"error": "invalid_grant"})
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"username": "me@example.com", "password": "bad"}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_auth"}


async def test_config_flow_no_devices(hass: HomeAssistant, aioclient_mock):
    aioclient_mock.post(
        TOKEN_URL,
        json={"access_token": "acc", "refresh_token": "ref", "expires_in": 120},
    )
    aioclient_mock.get(f"{MOBILE_API_URL}/devices/", json={"devices": []})
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"username": "me@example.com", "password": "pw"}
    )
    assert result["errors"] == {"base": "no_devices"}


@pytest.mark.parametrize("reported", [NESTED_REPORTED, FLAT_REPORTED])
async def test_entities_for_both_twin_shapes(
    hass: HomeAssistant, aioclient_mock, reported
):
    await setup_entry(hass, aioclient_mock, reported=reported)
    states = {s.entity_id: s for s in hass.states.async_all()}
    by_suffix = lambda suffix: next(
        s for eid, s in states.items() if eid.endswith(suffix)
    )
    assert by_suffix("_power_status").state == "awake"
    assert by_suffix("_brewing_status").state == "ready"
    assert by_suffix("_total_cups").state == "504"
    assert by_suffix("_brew_unit_cleaning_remaining").state == "78"
    assert by_suffix("_cloud_connection").state == "on"
    latte = by_suffix("_brew_morning_latte")
    assert latte.attributes["recipe"] == "$53|e1|f115"


async def test_brew_requires_arming_then_disarms(hass: HomeAssistant, aioclient_mock):
    await setup_entry(hass, aioclient_mock)
    button = "button.kitchen_tk_brew_morning_latte"
    switch = "switch.kitchen_tk_arm_remote_brewing"
    assert hass.states.get(switch).state == "off"

    with pytest.raises(HomeAssistantError, match="not armed"):
        await hass.services.async_call(
            "button", "press", {"entity_id": button}, blocking=True
        )
    assert calls(aioclient_mock, "POST", COMMAND_URL) == []

    await hass.services.async_call(
        "switch", "turn_on", {"entity_id": switch}, blocking=True
    )
    await hass.services.async_call(
        "button", "press", {"entity_id": button}, blocking=True
    )
    sent = calls(aioclient_mock, "POST", COMMAND_URL)
    assert len(sent) == 1
    order = sent[0][2]["drink_order"]
    uid, stamp, name, recipe = order.split("^")
    assert (uid, name, recipe) == ("a1", "Morning latte", "$53|e1|f115")
    assert stamp.isdigit() and int(stamp) > 1_700_000_000
    assert sent[0][3]["Authorization"] == "Bearer acc"
    assert hass.states.get(switch).state == "off"

    with pytest.raises(HomeAssistantError, match="not armed"):
        await hass.services.async_call(
            "button", "press", {"entity_id": button}, blocking=True
        )
    assert len(calls(aioclient_mock, "POST", COMMAND_URL)) == 1


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"device": {"status": 0}}, "not awake"),
        ({"brew": {"status": 1}}, "busy"),
    ],
)
async def test_brew_blocked_when_not_ready(
    hass: HomeAssistant, aioclient_mock, change, message
):
    reported = json.loads(json.dumps(NESTED_REPORTED))
    for section, values in change.items():
        reported[section].update(values)
    await setup_entry(hass, aioclient_mock, reported=reported)
    await hass.services.async_call(
        "switch",
        "turn_on",
        {"entity_id": "switch.kitchen_tk_arm_remote_brewing"},
        blocking=True,
    )
    with pytest.raises(HomeAssistantError, match=message):
        await hass.services.async_call(
            "button",
            "press",
            {"entity_id": "button.kitchen_tk_brew_morning_latte"},
            blocking=True,
        )
    assert calls(aioclient_mock, "POST", COMMAND_URL) == []


async def test_brew_blocked_when_offline(hass: HomeAssistant, aioclient_mock):
    await setup_entry(hass, aioclient_mock, connection_state="Disconnected")
    await hass.services.async_call(
        "switch",
        "turn_on",
        {"entity_id": "switch.kitchen_tk_arm_remote_brewing"},
        blocking=True,
    )
    with pytest.raises(HomeAssistantError, match="offline"):
        await hass.services.async_call(
            "button",
            "press",
            {"entity_id": "button.kitchen_tk_brew_morning_latte"},
            blocking=True,
        )


async def test_wake_sleep_cancel(hass: HomeAssistant, aioclient_mock):
    await setup_entry(hass, aioclient_mock)
    for name in ("wake", "sleep", "cancel_brew"):
        await hass.services.async_call(
            "button", "press", {"entity_id": f"button.kitchen_tk_{name}"}, blocking=True
        )
    puts = [c[2] for c in calls(aioclient_mock, "PUT", TWIN_URL)]
    assert puts == [
        {"desired": {"device": {"status": 2}}},
        {"desired": {"device": {"status": 3}}},
    ]
    assert calls(aioclient_mock, "POST", COMMAND_URL)[0][2] == {"cancel_brew": True}


async def test_wake_uses_flat_key_on_flat_twin(hass: HomeAssistant, aioclient_mock):
    await setup_entry(hass, aioclient_mock, reported=FLAT_REPORTED)
    wake = next(
        s.entity_id
        for s in hass.states.async_all("button")
        if s.entity_id.endswith("_wake")
    )
    await hass.services.async_call(
        "button", "press", {"entity_id": wake}, blocking=True
    )
    assert calls(aioclient_mock, "PUT", TWIN_URL)[0][2] == {"desired": {"dev_sts": 2}}


async def test_brew_recipe_service(hass: HomeAssistant, aioclient_mock):
    await setup_entry(hass, aioclient_mock)
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN, "brew_recipe", {"recipe": "latte please"}, blocking=True
        )
    with pytest.raises(HomeAssistantError, match="not armed"):
        await hass.services.async_call(
            DOMAIN, "brew_recipe", {"recipe": "$50|e2"}, blocking=True
        )
    await hass.services.async_call(
        "switch",
        "turn_on",
        {"entity_id": "switch.kitchen_tk_arm_remote_brewing"},
        blocking=True,
    )
    await hass.services.async_call(
        DOMAIN, "brew_recipe", {"recipe": "$50|e2", "name": "Dou^ble"}, blocking=True
    )
    order = calls(aioclient_mock, "POST", COMMAND_URL)[0][2]["drink_order"].split("^")
    assert order[0] == "ha" and order[2:] == ["Dou ble", "$50|e2"]


async def test_auth_failure_starts_reauth(hass: HomeAssistant, aioclient_mock):
    aioclient_mock.post(TOKEN_URL, status=400, json={"error": "invalid_grant"})
    entry = MockConfigEntry(
        domain=DOMAIN,
        version=2,
        unique_id=DEVICE,
        data={
            "username": "me@example.com",
            "password": "pw",
            "refresh_token": "ref",
            "device_id": DEVICE,
        },
    )
    entry.add_to_hass(hass)
    assert not await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.SETUP_ERROR
    assert any(
        f["context"]["source"] == "reauth"
        for f in hass.config_entries.flow.async_progress()
    )


async def test_cloud_down_retries_setup(hass: HomeAssistant, aioclient_mock):
    aioclient_mock.post(
        TOKEN_URL,
        json={"access_token": "acc", "refresh_token": "ref", "expires_in": 120},
    )
    aioclient_mock.get(TWIN_URL, status=503, text="down")
    entry = MockConfigEntry(
        domain=DOMAIN,
        version=2,
        unique_id=DEVICE,
        data={
            "username": "me@example.com",
            "password": "pw",
            "refresh_token": "ref",
            "device_id": DEVICE,
        },
    )
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.SETUP_RETRY


async def test_diagnostics_redacted(hass: HomeAssistant, aioclient_mock):
    entry = await setup_entry(hass, aioclient_mock)
    dump = json.dumps(await async_get_config_entry_diagnostics(hass, entry))
    for secret in (
        "pw",
        "TK-1234567890",
        "HomeNet",
        "192.168.1.100",
        "1234",
        "me@example.com",
        DEVICE,
        "Kitchen TK",
    ):
        assert f'"{secret}"' not in dump, secret
    assert '"stats.cups": 504' in dump
