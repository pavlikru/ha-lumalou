"""Lumalou routine events."""

from __future__ import annotations

from typing import Any

from homeassistant.components.event import EventEntity
from homeassistant.core import callback

from .entity import LumalouEntity

PARALLEL_UPDATES = 0
ROUTINE_EVENTS = (
    "task_completed",
    "routine_completed",
    "routine_cancelled",
    "routine_expired",
)


async def async_setup_entry(hass: Any, entry: Any, async_add_entities: Any) -> None:
    """Set up the routine event entity."""
    async_add_entities([LumalouRoutineEvent(entry)])


class LumalouRoutineEvent(LumalouEntity, EventEntity):
    """Fires when a task or the whole routine is done, cancelled or expired.

    ``task_completed`` carries the ``task`` key (for example ``brush_teeth``).
    Derived from the device's pushed task status while HA is connected.
    """

    _attr_translation_key = "routine"

    def __init__(self, entry: Any) -> None:
        super().__init__(entry)
        self._attr_event_types = list(ROUTINE_EVENTS)

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(
            self.coordinator.async_add_routine_listener(self._async_handle_event)
        )

    @callback
    def _async_handle_event(self, event_type: str, attributes: dict[str, str]) -> None:
        self._trigger_event(event_type, attributes)
        self.async_write_ha_state()
