"""Binary sensors for Terra Kaffe."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import (
    TerraKaffeConfigEntry,
    TerraKaffeData,
    TerraKaffeDataUpdateCoordinator,
)
from .entity import TerraKaffeEntity
from .twin import BREW_BREWING, BREW_ERROR, to_bool


@dataclass(frozen=True, kw_only=True)
class TerraKaffeBinarySensorDescription(BinarySensorEntityDescription):
    """Describes a Terra Kaffe binary sensor."""

    is_on_fn: Callable[[TerraKaffeData], bool | None]


def _connected(data: TerraKaffeData) -> bool | None:
    if data.connection_state is None:
        return None
    return data.connection_state == "Connected"


BINARY_SENSORS: tuple[TerraKaffeBinarySensorDescription, ...] = (
    TerraKaffeBinarySensorDescription(
        key="connected",
        translation_key="connected",
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        entity_category=EntityCategory.DIAGNOSTIC,
        is_on_fn=_connected,
    ),
    TerraKaffeBinarySensorDescription(
        key="brewing",
        translation_key="brewing",
        device_class=BinarySensorDeviceClass.RUNNING,
        is_on_fn=lambda d: None if d.brew_status is None else d.brew_status == BREW_BREWING,
    ),
    TerraKaffeBinarySensorDescription(
        key="brew_error",
        translation_key="brew_error",
        device_class=BinarySensorDeviceClass.PROBLEM,
        is_on_fn=lambda d: None if d.brew_status is None else d.brew_status == BREW_ERROR,
    ),
    TerraKaffeBinarySensorDescription(
        key="locked",
        translation_key="locked",
        entity_category=EntityCategory.DIAGNOSTIC,
        is_on_fn=lambda d: to_bool(d.twin.get("device.locked")),
    ),
    TerraKaffeBinarySensorDescription(
        key="preground_selected",
        translation_key="preground_selected",
        entity_category=EntityCategory.DIAGNOSTIC,
        is_on_fn=lambda d: to_bool(d.twin.get("brew.preground")),
    ),
    TerraKaffeBinarySensorDescription(
        key="schedule_enabled",
        translation_key="schedule_enabled",
        entity_category=EntityCategory.DIAGNOSTIC,
        is_on_fn=lambda d: to_bool(d.twin.get("schedules.enabled")),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: TerraKaffeConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Terra Kaffe binary sensors."""
    coordinator = entry.runtime_data
    async_add_entities(
        TerraKaffeBinarySensor(coordinator, description)
        for description in BINARY_SENSORS
    )


class TerraKaffeBinarySensor(TerraKaffeEntity, BinarySensorEntity):
    """An on/off value derived from the device twin."""

    entity_description: TerraKaffeBinarySensorDescription

    def __init__(
        self,
        coordinator: TerraKaffeDataUpdateCoordinator,
        description: TerraKaffeBinarySensorDescription,
    ) -> None:
        """Initialize the binary sensor."""
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def is_on(self) -> bool | None:
        """Return the state."""
        return self.entity_description.is_on_fn(self.coordinator.data)
