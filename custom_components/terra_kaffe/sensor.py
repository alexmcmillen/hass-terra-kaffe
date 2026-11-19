"""Sensors for Terra Kaffe."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import PERCENTAGE, EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import StateType

from .coordinator import TerraKaffeConfigEntry, TerraKaffeDataUpdateCoordinator
from .entity import TerraKaffeEntity
from .twin import counter_remaining_percent, parse_counter, parse_recipe_slot, to_int

POWER_STATES = {0: "sleep", 1: "awake", 2: "waking_up", 3: "going_to_sleep"}
BREW_STATES = {
    0: "ready",
    1: "brewing",
    2: "prewarming",
    3: "paused",
    4: "cleaning",
    5: "error",
}


def _enum(mapping: dict[int, str]) -> Callable[[Any], StateType]:
    def convert(value: Any) -> StateType:
        number = to_int(value)
        return mapping.get(number) if number is not None else None

    return convert


def _text(value: Any) -> StateType:
    if value in (None, ""):
        return None
    return str(value)[:255]


def _recipe_name(value: Any) -> StateType:
    recipe = parse_recipe_slot(value)
    return recipe.name if recipe else _text(value)


@dataclass(frozen=True, kw_only=True)
class TerraKaffeSensorDescription(SensorEntityDescription):
    """Describes a Terra Kaffe sensor."""

    path: str
    value_fn: Callable[[Any], StateType] = to_int
    attrs_fn: Callable[[Any], dict[str, Any] | None] | None = None


def _counter_attrs(value: Any) -> dict[str, Any] | None:
    parsed = parse_counter(value)
    if parsed is None:
        return None
    return {"count": parsed[0], "interval": parsed[1]}


def _maintenance(key: str, path: str) -> TerraKaffeSensorDescription:
    return TerraKaffeSensorDescription(
        key=key,
        translation_key=key,
        path=path,
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=counter_remaining_percent,
        attrs_fn=_counter_attrs,
    )


def _diagnostic(key: str, path: str, *, enabled: bool = False) -> TerraKaffeSensorDescription:
    return TerraKaffeSensorDescription(
        key=key,
        translation_key=key,
        path=path,
        value_fn=_text,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=enabled,
    )


SENSORS: tuple[TerraKaffeSensorDescription, ...] = (
    TerraKaffeSensorDescription(
        key="device_status",
        translation_key="device_status",
        path="device.status",
        device_class=SensorDeviceClass.ENUM,
        options=list(POWER_STATES.values()),
        value_fn=_enum(POWER_STATES),
    ),
    TerraKaffeSensorDescription(
        key="brewing_status",
        translation_key="brewing_status",
        path="brew.status",
        device_class=SensorDeviceClass.ENUM,
        options=list(BREW_STATES.values()),
        value_fn=_enum(BREW_STATES),
    ),
    TerraKaffeSensorDescription(
        key="brewing_progress",
        translation_key="brewing_progress",
        path="brew.progress",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    TerraKaffeSensorDescription(
        key="bean_level",
        translation_key="bean_level",
        path="levels.bean",
        state_class=SensorStateClass.MEASUREMENT,
    ),
    TerraKaffeSensorDescription(
        key="waste_bin_level",
        translation_key="waste_bin_level",
        path="levels.waste_bin",
        state_class=SensorStateClass.MEASUREMENT,
    ),
    TerraKaffeSensorDescription(
        key="total_cups",
        translation_key="total_cups",
        path="stats.cups",
        state_class=SensorStateClass.TOTAL_INCREASING,
    ),
    TerraKaffeSensorDescription(
        key="espresso_shots",
        translation_key="espresso_shots",
        path="stats.shots",
        state_class=SensorStateClass.TOTAL_INCREASING,
    ),
    TerraKaffeSensorDescription(
        key="beans_used",
        translation_key="beans_used",
        path="stats.beans",
        state_class=SensorStateClass.TOTAL_INCREASING,
        entity_registry_enabled_default=False,
    ),
    TerraKaffeSensorDescription(
        key="milk_used",
        translation_key="milk_used",
        path="stats.milk",
        state_class=SensorStateClass.TOTAL_INCREASING,
        entity_registry_enabled_default=False,
    ),
    TerraKaffeSensorDescription(
        key="water_used",
        translation_key="water_used",
        path="stats.water",
        state_class=SensorStateClass.TOTAL_INCREASING,
        entity_registry_enabled_default=False,
    ),
    _maintenance("clean_brew_unit", "care.clean.brew_unit"),
    _maintenance("descale", "care.clean.descale"),
    _maintenance("water_filter", "care.clean.water_filter"),
    _maintenance("waste_bin_count", "care.clean.waste_bin"),
    TerraKaffeSensorDescription(
        key="care_status",
        translation_key="care_status",
        path="care.status",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    TerraKaffeSensorDescription(
        key="grind_setting",
        translation_key="grind_setting",
        path="levels.grind",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    TerraKaffeSensorDescription(
        key="water_hardness",
        translation_key="water_hardness",
        path="levels.water_hardness",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    TerraKaffeSensorDescription(
        key="screen_brightness",
        translation_key="screen_brightness",
        path="settings.brightness",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    TerraKaffeSensorDescription(
        key="wake_drink",
        translation_key="wake_drink",
        path="wake_drink",
        value_fn=_recipe_name,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    _diagnostic("current_espresso_profile", "current_ep", enabled=True),
    _diagnostic("firmware_version", "device.fw.app", enabled=True),
    _diagnostic("control_board_version", "device.fw.cb"),
    _diagnostic("hmi_version", "device.fw.hmi"),
    _diagnostic("os_version", "device.os"),
    _diagnostic("serial_number", "device.serial"),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: TerraKaffeConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Terra Kaffe sensors."""
    coordinator = entry.runtime_data
    async_add_entities(
        TerraKaffeSensor(coordinator, description) for description in SENSORS
    )


class TerraKaffeSensor(TerraKaffeEntity, SensorEntity):
    """A value read from the machine's device twin."""

    entity_description: TerraKaffeSensorDescription

    def __init__(
        self,
        coordinator: TerraKaffeDataUpdateCoordinator,
        description: TerraKaffeSensorDescription,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def _raw(self) -> Any:
        return self.coordinator.data.twin.get(self.entity_description.path)

    @property
    def native_value(self) -> StateType:
        """Return the sensor value."""
        return self.entity_description.value_fn(self._raw)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return extra attributes."""
        if self.entity_description.attrs_fn is None:
            return None
        return self.entity_description.attrs_fn(self._raw)
