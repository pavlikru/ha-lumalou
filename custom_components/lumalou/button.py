"""Lumalou buttons."""

from __future__ import annotations

from typing import Any

from homeassistant.components.button import ButtonEntity
from homeassistant.const import EntityCategory

from .const import ROUTINE_OPERATION_MODE
from .entity import LumalouControlEntity

PARALLEL_UPDATES = 0


async def async_setup_entry(hass: Any, entry: Any, async_add_entities: Any) -> None:
    """Set up Lumalou buttons."""
    async_add_entities(
        [
            LumalouSyncClockButton(entry),
            LumalouStartRoutineButton(entry),
            LumalouCompleteTaskButton(entry),
            LumalouPreviousTaskButton(entry),
            LumalouCancelRoutineButton(entry),
        ]
    )


class LumalouSyncClockButton(LumalouControlEntity, ButtonEntity):
    """Synchronize the device clock."""

    _attr_translation_key = "sync_clock"
    _attr_entity_category = EntityCategory.CONFIG

    async def async_press(self) -> None:
        await self.coordinator.async_sync_clock()


class LumalouStartRoutineButton(LumalouControlEntity, ButtonEntity):
    """Start today's routine now, like the scheduled start."""

    _attr_translation_key = "start_routine"

    async def async_press(self) -> None:
        await self.coordinator.async_start_routine()


class _LumalouRoutineControlButton(LumalouControlEntity, ButtonEntity):
    """Send one routine control code to the running routine."""

    _control: int

    @property
    def available(self) -> bool:
        """Only while a routine runs."""
        return (
            super().available
            and self.snapshot_value("operationMode") == ROUTINE_OPERATION_MODE
        )

    async def async_press(self) -> None:
        await self.coordinator.async_routine_control(self._control)


class LumalouCompleteTaskButton(_LumalouRoutineControlButton):
    """Complete the current task, like the remote's check-mark button."""

    _attr_translation_key = "complete_task"
    _control = 0


class LumalouPreviousTaskButton(_LumalouRoutineControlButton):
    """Go back to the previous task."""

    _attr_translation_key = "previous_task"
    _control = 1


class LumalouCancelRoutineButton(_LumalouRoutineControlButton):
    """Cancel the running routine (silent)."""

    _attr_translation_key = "cancel_routine"
    _control = 4
