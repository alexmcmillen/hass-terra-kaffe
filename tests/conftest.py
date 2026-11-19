"""Shared fixtures."""

from __future__ import annotations

import copy

import pytest

NESTED_REPORTED = {
    "device": {
        "status": 1,
        "locked": False,
        "friendly_name": "Kitchen TK",
        "serial": "TK-1234567890",
        "model": "TK-02",
        "os": "1.0.0",
        "fw": {"cb": "1.1.1", "hmi": "1.0.0", "app": "38.0.0", "profile": "1.0.0"},
    },
    "brew": {"preground": False, "status": 0, "progress": 0},
    "levels": {"bean": 50, "waste_bin": 1, "water_hardness": 1, "grind": 4},
    "stats": {"cups": 504, "shots": 42, "beans": 2500, "milk": 1500, "water": 8000},
    "care": {
        "status": 1,
        "clean": {
            "brew_unit": "45/200",
            "descale": "120/500",
            "water_filter": "90/300",
            "waste_bin": "8/15",
        },
    },
    "wifi": {"ssid": "HomeNet", "enabled": True, "ip_address": "192.168.1.100"},
    "settings": {"pin": {"enabled": False, "code": "1234"}, "brightness": 5},
    "drinks": {
        "menu_order": "a1^b2",
        "01": "a1^1710000000^Morning latte^$53|e1|f115",
        "02": "b2^1710000001^Flat white^$54|e2|f90^ep7",
        "03": None,
    },
    "eps": {"01": "ep7^1710000000^Bold^$100|d10|x50|t4|g2"},
    "schedules": {"enabled": True, "monday": {"on": "420", "off": "1320"}},
    "current_ep": "ep7",
    "wake_drink": "a1^1710000000^Morning latte^$53|e1|f115",
    "$metadata": {"ignored": True},
}

FLAT_REPORTED = {
    "dev_sts": "1",
    "brew_sts": "0",
    "brew_prog": "0",
    "bean_lvl": "50",
    "cup_cnt": "504",
    "shot_cnt": "42",
    "cln_brew": "45/200",
    "serial": "TK-1234567890",
    "fw_app": "38.0.0",
    "drink_01": "a1^1710000000^Morning latte^$53|e1|f115",
    "sched_mon_on": "420",
}


def make_twin(reported, connection_state="Connected"):
    return {
        "device_id": "dev123456",
        "etag": "AAAAAAAAAAE=",
        "connection_state": connection_state,
        "last_activity_time": "2026-11-19T14:00:00Z",
        "desired": {},
        "reported": copy.deepcopy(reported),
        "tags": {},
    }


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Let Home Assistant load the integration under test."""
    return
