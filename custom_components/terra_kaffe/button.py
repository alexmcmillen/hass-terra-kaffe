"""Buttons for Terra Kaffe: wake, sleep, cancel, and saved drinks."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import SAVED_DRINK_SLOTS
from .coordinator import TerraKaffeConfigEntry, TerraKaffeDataUpdateCoordinator
from .entity import TerraKaffeEntity
from .twin import Recipe, parse_recipe_slot


async def async_setup_entry(
    hass: HomeAssistant,
    entry: TerraKaffeConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Terra Kaffe buttons."""
    coordinator = entry.runtime_data
    entities: list[ButtonEntity] = [
        TerraKaffeWakeButton(coordinator),
        TerraKaffeSleepButton(coordinator),
        TerraKaffeCancelBrewButton(coordinator),
    ]
    entities.extend(
        TerraKaffeSavedDrinkButton(coordinator, slot)
        for slot in range(1, SAVED_DRINK_SLOTS + 1)
    )
    async_add_entities(entities)


class TerraKaffeWakeButton(TerraKaffeEntity, ButtonEntity):
    """Wakes the machine so it starts heating."""

    _attr_translation_key = "wake"

    def __init__(self, coordinator: TerraKaffeDataUpdateCoordinator) -> None:
        """Initialize the button."""
        super().__init__(coordinator, "wake")

    async def async_press(self) -> None:
        """Wake the machine."""
        await self.coordinator.async_wake()


class TerraKaffeSleepButton(TerraKaffeEntity, ButtonEntity):
    """Puts the machine to sleep."""

    _attr_translation_key = "sleep"

    def __init__(self, coordinator: TerraKaffeDataUpdateCoordinator) -> None:
        """Initialize the button."""
        super().__init__(coordinator, "sleep")

    async def async_press(self) -> None:
        """Put the machine to sleep."""
        await self.coordinator.async_sleep()


class TerraKaffeCancelBrewButton(TerraKaffeEntity, ButtonEntity):
    """Cancels the drink currently being made. Always allowed."""

    _attr_translation_key = "cancel_brew"

    def __init__(self, coordinator: TerraKaffeDataUpdateCoordinator) -> None:
        """Initialize the button."""
        super().__init__(coordinator, "cancel_brew")

    async def async_press(self) -> None:
        """Cancel the brew."""
        await self.coordinator.async_cancel_brew()


class TerraKaffeSavedDrinkButton(TerraKaffeEntity, ButtonEntity):
    """Starts one of the drinks saved on the machine (slots 1-10)."""

    def __init__(
        self, coordinator: TerraKaffeDataUpdateCoordinator, slot: int
    ) -> None:
        """Initialize the button for a saved-drink slot."""
        super().__init__(coordinator, f"saved_drink_{slot}")
        self._slot = slot
        # Empty slots stay out of the way until a drink is saved there.
        self._attr_entity_registry_enabled_default = self._drink is not None

    @property
    def _drink(self) -> Recipe | None:
        data = self.coordinator.data
        if data is None:
            return None
        return parse_recipe_slot(data.twin.get(f"drinks.{self._slot:02d}"))

    @property
    def name(self) -> str:
        """Use the drink's own name, e.g. "Brew Morning latte"."""
        drink = self._drink
        if drink:
            return f"Brew {drink.name}"
        return f"Saved drink {self._slot} (empty)"

    @property
    def available(self) -> bool:
        """Only available when the slot holds a drink."""
        return super().available and self._drink is not None

    @property
    def extra_state_attributes(self) -> dict[str, str] | None:
        """Expose the recipe so automations can see what will be made."""
        drink = self._drink
        if not drink:
            return None
        return {"recipe": drink.recipe, "slot": str(self._slot)}

    async def async_press(self) -> None:
        """Start this drink, subject to the safety checks."""
        drink = self._drink
        if drink is None:
            raise HomeAssistantError("This saved-drink slot is empty.")
        await self.coordinator.async_brew(drink)
