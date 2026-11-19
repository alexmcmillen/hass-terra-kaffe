"""Tests for the device-twin helpers."""

import json

import pytest

from custom_components.terra_kaffe.twin import (
    Recipe,
    build_desired_patch,
    counter_remaining_percent,
    decode_recipe,
    is_recipe_string,
    normalize_reported,
    parse_recipe_slot,
    saved_drinks,
    to_bool,
    to_int,
    uses_flat_keys,
)

from .conftest import FLAT_REPORTED, NESTED_REPORTED


def test_normalize_nested():
    twin = normalize_reported(NESTED_REPORTED)
    assert twin["device.status"] == 1
    assert twin["care.clean.waste_bin"] == "8/15"
    assert twin["device.fw.app"] == "38.0.0"
    assert twin["drinks.01"].endswith("$53|e1|f115")
    assert not any(key.startswith("$") for key in twin)
    assert uses_flat_keys(NESTED_REPORTED) is False


def test_normalize_flat_matches_nested_paths():
    twin = normalize_reported(FLAT_REPORTED)
    assert to_int(twin["device.status"]) == 1
    assert to_int(twin["brew.status"]) == 0
    assert twin["care.clean.brew_unit"] == "45/200"
    assert twin["schedules.monday.on"] == "420"
    assert twin["drinks.01"].startswith("a1^")
    assert uses_flat_keys(FLAT_REPORTED) is True


def test_normalize_json_string_and_garbage():
    assert normalize_reported(json.dumps(NESTED_REPORTED))["stats.cups"] == 504
    assert normalize_reported(None) == {}
    assert normalize_reported("not json") == {}
    assert normalize_reported({"drinks": {"8": "x"}}) == {"drinks.08": "x"}


def test_desired_patch_mirrors_reported_shape():
    assert build_desired_patch("device.status", 2, flat=False) == {
        "device": {"status": 2}
    }
    assert build_desired_patch("device.status", 2, flat=True) == {"dev_sts": 2}


@pytest.mark.parametrize(
    ("value", "expected"),
    [("45/200", 78), ("0/200", 100), ("200/200", 0), ("250/200", 0), ("x", None), (5, None), ("1/0", None)],
)
def test_counter_remaining(value, expected):
    assert counter_remaining_percent(value) == expected


def test_conversions():
    assert to_int("3") == 3 and to_int(True) == 1 and to_int("x") is None
    assert to_bool("true") is True and to_bool(0) is False and to_bool("?") is None


def test_parse_slot_and_order_string():
    drink = parse_recipe_slot("b2^1710000001^Flat white^$54|e2|f90^ep7")
    assert drink == Recipe("b2", "Flat white", "$54|e2|f90", "ep7")
    assert drink.order_string(now=1800000000) == "b2^1800000000^Flat white^$54|e2|f90^ep7"
    assert parse_recipe_slot("a1^1^Latte^$53|e1|f115").order_string(now=5) == (
        "a1^5^Latte^$53|e1|f115"
    )


@pytest.mark.parametrize(
    "value",
    [None, "", "drip^6^240^0", "a^b^c", "a1^1^Name^not-a-recipe", 7, "^1^Name^$53|e1"],
)
def test_parse_slot_rejects(value):
    assert parse_recipe_slot(value) is None


def test_order_string_trimmed_to_limit():
    drink = Recipe("uid", "N" * 200, "$53|e1|f115")
    order = drink.order_string(now=1800000000)
    assert len(order) == 90 and order.endswith("^$53|e1|f115")
    with pytest.raises(ValueError):
        Recipe("u" * 100, "n", "$53|e1").order_string(now=1)


def test_recipe_strings():
    assert is_recipe_string("$53|e1|f115") and is_recipe_string("$0")
    assert not is_recipe_string("53|e1") and not is_recipe_string("$x|e1")
    assert not is_recipe_string("$53|e1^evil")
    assert decode_recipe("$52|f70|e1") == {"type": "52", "f": "70", "e": "1"}


def test_saved_drinks():
    drinks = saved_drinks(normalize_reported(NESTED_REPORTED))
    assert sorted(drinks) == [1, 2]
    assert drinks[2].espresso_profile_id == "ep7"
