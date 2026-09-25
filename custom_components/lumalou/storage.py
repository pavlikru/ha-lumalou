"""Private per-entry profile storage on Home Assistant's Store helper."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .const import DOMAIN, STORE_VERSION
from .models import ProfileRecord, ProfileValidationError

_LOGGER = logging.getLogger(__name__)


class ProfileStore:
    """Load and atomically save one entry's revisioned profile record."""

    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        self._store: Store[dict[str, Any]] = Store(
            hass,
            STORE_VERSION,
            f"{DOMAIN}.{entry_id}.profile",
            private=True,
            atomic_writes=True,
        )

    async def async_load(self) -> ProfileRecord:
        """Return the saved record, or an empty one when nothing usable exists.

        Store itself renames undecodable JSON to ``.corrupt.<timestamp>`` and
        raises a Repair. A decodable but invalid record is ignored with a
        warning; controls stay locked until the device profile is read again.
        """
        data = await self._store.async_load()
        if data is None:
            return ProfileRecord()
        try:
            return ProfileRecord.from_dict(data)
        except ProfileValidationError:
            _LOGGER.warning(
                "Ignoring an invalid saved Lumalou profile; read the device "
                "profile again in the integration options"
            )
            return ProfileRecord()

    async def async_save(self, record: ProfileRecord) -> None:
        """Write the record atomically."""
        await self._store.async_save(record.to_dict())

    async def async_remove(self) -> None:
        """Delete the entry's private profile (entry removal)."""
        await self._store.async_remove()
