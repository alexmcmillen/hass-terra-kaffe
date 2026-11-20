"""Device-twin helpers for the Terra Kaffe cloud (TK Cloud 2.0).

The machine's state is an Azure IoT Hub device twin. The documented shape is
nested (``reported.device.status``), but the firmware and the app's native
library also know a flat, abbreviated key set (``dev_sts``). Everything here
normalises either shape to one flat dict keyed by the canonical dotted path,
so the rest of the integration never cares which one the cloud returned.

This module has no Home Assistant imports so it can be tested on its own.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from typing import Any

# Flat firmware key -> canonical dotted path.
FLAT_ALIASES: dict[str, str] = {
    "dev_sts": "device.status",
    "locked": "device.locked",
    "serial": "device.serial",
    "os": "device.os",
    "fw_cb": "device.fw.cb",
    "fw_hmi": "device.fw.hmi",
    "fw_app": "device.fw.app",
    "fw_profile": "device.fw.profile",
    "brew_sts": "brew.status",
    "brew_prog": "brew.progress",
    "preground": "brew.preground",
    "bean_lvl": "levels.bean",
    "waste_bin_lvl": "levels.waste_bin",
    "water_hard": "levels.water_hardness",
    "grind_sz": "levels.grind",
    "cup_cnt": "stats.cups",
    "shot_cnt": "stats.shots",
    "bean_cnt": "stats.beans",
    "milk_cnt": "stats.milk",
    "water_cnt": "stats.water",
    "care_sts": "care.status",
    "cln_brew": "care.clean.brew_unit",
    "cln_desc": "care.clean.descale",
    "cln_fltr": "care.clean.water_filter",
    "cln_wb": "care.clean.waste_bin",
    "wifi_ssid": "wifi.ssid",
    "wifi_en": "wifi.enabled",
    "wifi_ip": "wifi.ip_address",
    "pin_en": "settings.pin.enabled",
    "pin_cd": "settings.pin.code",
    "lang": "settings.lang",
    "time_fmt": "settings.time_format",
    "bright": "settings.brightness",
    "units": "settings.units",
    "notif": "settings.notification",
    "menu_order": "drinks.menu_order",
    "curr_ep": "current_ep",
    "one_touch": "one_touch_buy",
    "sched_en": "schedules.enabled",
}
for _n in range(1, 11):
    FLAT_ALIASES[f"drink_{_n:02d}"] = f"drinks.{_n:02d}"
    FLAT_ALIASES[f"ep_{_n:02d}"] = f"eps.{_n:02d}"
for _short, _day in (
    ("mon", "monday"),
    ("tue", "tuesday"),
    ("wed", "wednesday"),
    ("thu", "thursday"),
    ("fri", "friday"),
    ("sat", "saturday"),
    ("sun", "sunday"),
):
    FLAT_ALIASES[f"sched_{_short}_on"] = f"schedules.{_day}.on"
    FLAT_ALIASES[f"sched_{_short}_off"] = f"schedules.{_day}.off"

PATH_TO_FLAT: dict[str, str] = {path: flat for flat, path in FLAT_ALIASES.items()}

# Keys never worth exposing or logging.
SENSITIVE_PATHS = frozenset({"settings.pin.code", "wifi.ssid", "wifi.ip_address"})

POWER_SLEEP = 0
POWER_AWAKE = 1
POWER_GOING_TO_WAKE = 2
POWER_GOING_TO_SLEEP = 3

BREW_READY = 0
BREW_BREWING = 1
BREW_PREWARMUP = 2
# The app labels 3 "paused", but on TK-02 firmware 39 the machine reports 3
# after a drink completes and keeps it until the next drink (observed live
# 2026-11-20: 0 -> 1 while brewing -> 3 when done). It is an idle state.
BREW_DONE = 3
BREW_CLEANING = 4
BREW_ERROR = 5
# Statuses in which a new drink may be started.
BREW_IDLE_STATES = frozenset({BREW_READY, BREW_DONE})

# The command endpoint rejects drink orders longer than this.
DRINK_ORDER_MAX_LEN = 90


def _as_dict(value: Any) -> dict[str, Any]:
    """Twin sections arrive as objects, or occasionally as JSON strings."""
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.strip().startswith("{"):
        try:
            parsed = json.loads(value)
        except ValueError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _flatten(prefix: str, value: Any, out: dict[str, Any]) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            key = str(key)
            if key.startswith("$"):  # IoT Hub metadata ($metadata, $version)
                continue
            _flatten(f"{prefix}.{key}" if prefix else key, child, out)
    else:
        out[prefix] = value


def normalize_reported(reported: Any) -> dict[str, Any]:
    """Return ``{dotted.path: value}`` for a nested or flat reported section."""
    flat: dict[str, Any] = {}
    _flatten("", _as_dict(reported), flat)
    out: dict[str, Any] = {}
    for key, value in flat.items():
        out[FLAT_ALIASES.get(key, key)] = value
    # Slot keys can lose their leading zero in transit ("8" for "08").
    for group in ("drinks", "eps"):
        for n in range(1, 10):
            short, canonical = f"{group}.{n}", f"{group}.{n:02d}"
            if short in out and canonical not in out:
                out[canonical] = out.pop(short)
    return out


def uses_flat_keys(reported: Any) -> bool:
    """True when the machine reports the abbreviated flat key set."""
    section = _as_dict(reported)
    return any(key in FLAT_ALIASES for key in section) and "device" not in section


def build_desired_patch(path: str, value: Any, *, flat: bool) -> dict[str, Any]:
    """Build a desired-properties patch for one canonical path.

    The patch mirrors whichever shape the machine reports, since that is the
    shape its firmware understands.
    """
    if flat:
        return {PATH_TO_FLAT.get(path, path): value}
    patch: dict[str, Any] = {}
    node = patch
    parts = path.split(".")
    for part in parts[:-1]:
        node = node.setdefault(part, {})
    node[parts[-1]] = value
    return patch


def to_int(value: Any) -> int | None:
    """Best-effort int conversion (the twin mixes ints, bools and strings)."""
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, str):
        try:
            return int(float(value.strip()))
        except ValueError:
            return None
    return None


def to_bool(value: Any) -> bool | None:
    """Best-effort bool conversion."""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in ("true", "1", "on", "yes"):
            return True
        if lowered in ("false", "0", "off", "no", ""):
            return False
    return None


def parse_counter(value: Any) -> tuple[int, int] | None:
    """Parse a maintenance counter of the form ``"current/max"``."""
    if not isinstance(value, str) or "/" not in value:
        return None
    current, _, maximum = value.partition("/")
    try:
        cur, mx = int(current.strip()), int(maximum.strip())
    except ValueError:
        return None
    if mx <= 0:
        return None
    return cur, mx


def counter_remaining_percent(value: Any) -> int | None:
    """Percentage of the maintenance interval still left (100 = just done)."""
    parsed = parse_counter(value)
    if parsed is None:
        return None
    cur, mx = parsed
    return max(0, min(100, round(100 * (mx - cur) / mx)))


@dataclass(frozen=True)
class Recipe:
    """A drink or espresso profile stored in a machine slot.

    Slot format: ``id^epochSeconds^name^recipeString[^espressoProfileId]``,
    where the recipe string looks like ``$53|e1|f115``.
    """

    uid: str
    name: str
    recipe: str
    espresso_profile_id: str | None = None

    def order_string(self, now: int | None = None) -> str:
        """Build the drink-order attribute string the machine expects."""
        stamp = int(time.time()) if now is None else now
        parts = [self.uid, str(stamp), self.name, self.recipe]
        if self.espresso_profile_id:
            parts.append(self.espresso_profile_id)
        order = "^".join(parts)
        if len(order) > DRINK_ORDER_MAX_LEN:
            # The name is the only free-form part; trim it to fit.
            overflow = len(order) - DRINK_ORDER_MAX_LEN
            parts[2] = self.name[: max(1, len(self.name) - overflow)]
            order = "^".join(parts)
        if len(order) > DRINK_ORDER_MAX_LEN:
            raise ValueError("Drink order is too long for the machine")
        return order


def parse_recipe_slot(value: Any) -> Recipe | None:
    """Parse a slot value; return None for empty or unrecognised slots."""
    if not isinstance(value, str):
        return None
    parts = value.strip().split("^")
    if len(parts) < 4:
        return None
    uid, _stamp, name, recipe = parts[0], parts[1], parts[2], parts[3]
    if not uid or not is_recipe_string(recipe):
        return None
    profile = parts[4].strip() if len(parts) > 4 and parts[4].strip() else None
    return Recipe(
        uid=uid.strip(),
        name=name.strip() or "Saved drink",
        recipe=recipe.strip(),
        espresso_profile_id=profile,
    )


# ``$<type>`` then ``|<letter><value>`` parts, e.g. ``$53|e1|f115`` or
# ``$0|w100:t3``. Nothing that could break out of the ``^``-separated order.
_RECIPE_RE = re.compile(r"^\$\d{1,4}(\|[A-Za-z][A-Za-z0-9:]{0,15}){0,16}$")
RECIPE_MAX_LEN = 64


def is_recipe_string(value: Any) -> bool:
    """True for strings shaped like ``$53|e1|f115``."""
    if not isinstance(value, str):
        return False
    value = value.strip()
    return len(value) <= RECIPE_MAX_LEN and bool(_RECIPE_RE.fullmatch(value))


def clean_drink_name(value: Any, default: str = "Home Assistant") -> str:
    """Make a free-text name safe for the ``^``-separated drink order."""
    text = "".join(
        ch if ch.isprintable() and ch not in "^|$" else " " for ch in str(value or "")
    )
    text = " ".join(text.split())[:32]
    return text or default


def parse_grind(value: Any) -> int | None:
    """Grind setting; firmware 39 reports ``"2|0"`` (setting|sub-step)."""
    if isinstance(value, str) and "|" in value:
        value = value.split("|", 1)[0]
    return to_int(value)


def decode_recipe(recipe: str) -> dict[str, str]:
    """Decode ``$52|f70|e1`` into ``{"type": "52", "f": "70", "e": "1"}``."""
    out: dict[str, str] = {}
    for index, segment in enumerate(recipe.strip().split("|")):
        if index == 0:
            out["type"] = segment.lstrip("$")
        elif segment:
            out[segment[0]] = segment[1:]
    return out


def saved_drinks(twin: dict[str, Any]) -> dict[int, Recipe]:
    """Return ``{slot (1-10): Recipe}`` for the occupied drink slots."""
    out: dict[int, Recipe] = {}
    for slot in range(1, 11):
        recipe = parse_recipe_slot(twin.get(f"drinks.{slot:02d}"))
        if recipe is not None:
            out[slot] = recipe
    return out
