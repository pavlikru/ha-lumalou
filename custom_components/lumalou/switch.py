"""Lumalou switches."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.const import EntityCategory

from .const import ROUTINE_SETTING_FIELDS
from .entity import LumalouControlEntity, LumalouEntity

PARALLEL_UPDATES = 0


async def async_setup_entry(hass: Any, entry: Any, async_add_entities: Any) -> None:
    """Set up Lumalou switches."""
    async_add_entities(
        [
            LumalouMaintenanceSwitch(entry),
            LumalouClockDisplaySwitch(entry),
            LumalouRoutinesSwitch(entry),
            LumalouRoutineMusicSwitch(entry),
            LumalouTaskRewardSoundSwitch(entry),
            LumalouRoutineRewardSoundSwitch(entry),
        ]
    )


class LumalouMaintenanceSwitch(LumalouEntity, SwitchEntity):
    """Release Bluetooth and block integration device operations."""

    _attr_translation_key = "maintenance"
    _attr_entity_category = EntityCategory.CONFIG

    @property
    def available(self) -> bool:
        """Maintenance is saved locally, so it stays usable while offline."""
        return True

    @property
    def is_on(self) -> bool:
        return self.coordinator.maintenance

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.coordinator.async_set_maintenance(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.coordinator.async_set_maintenance(False)


class LumalouClockDisplaySwitch(LumalouControlEntity, SwitchEntity):
    """Show or hide the device clock."""

    _attr_translation_key = "clock_display"
    _attr_entity_category = EntityCategory.CONFIG

    @property
    def is_on(self) -> bool | None:
        value = self.snapshot_value("clockDisplay")
        return None if value is None else bool(value)

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.coordinator.async_set_clock_settings(display=True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.coordinator.async_set_clock_settings(display=False)


class _LumalouRoutineSettingSwitch(LumalouControlEntity, SwitchEntity):
    """A routine setting, kept in the saved profile (restored after power loss)."""

    _attr_entity_category = EntityCategory.CONFIG
    _setting: str

    @property
    def is_on(self) -> bool | None:
        value = self.snapshot_value(ROUTINE_SETTING_FIELDS[self._setting])
        return None if value is None else bool(value)

    async def _async_set(self, on: bool) -> None:
        value = on if self._setting == "enabled" else int(on)
        await self.coordinator.async_set_routine_settings(**{self._setting: value})

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._async_set(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._async_set(False)


class LumalouRoutinesSwitch(_LumalouRoutineSettingSwitch):
    """Routine mode: start the day's routine automatically at its time."""

    _attr_translation_key = "routines"
    _setting = "enabled"


class LumalouRoutineMusicSwitch(_LumalouRoutineSettingSwitch):
    """Play music during routine tasks."""

    _attr_translation_key = "routine_music"
    _setting = "music"


class LumalouTaskRewardSoundSwitch(_LumalouRoutineSettingSwitch):
    """Play a reward sound when a task is completed."""

    _attr_translation_key = "task_reward_sound"
    _setting = "task_reward_sfx"


class LumalouRoutineRewardSoundSwitch(_LumalouRoutineSettingSwitch):
    """Play a reward sound when the whole routine is completed."""

    _attr_translation_key = "routine_reward_sound"
    _setting = "routine_reward_sfx"
