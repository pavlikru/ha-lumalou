"""Private atomic storage with independent on-disk save verification."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.storage import Store

from .const import DOMAIN, PROFILE_SCHEMA_VERSION
from .models import ProfileRecord


class ProfileStorageError(HomeAssistantError):
    """A profile could not be loaded or durably checked after saving."""


def _read_document(path: str) -> dict:
    """Read from disk, deliberately bypassing Store's memory cache."""
    with Path(path).open(encoding="utf-8") as handle:
        return json.load(handle)


class ProfileStore:
    """Serialize writes to an entry-private Store and independently check them."""

    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        self.hass = hass
        self._store: Store[dict] = Store(
            hass,
            PROFILE_SCHEMA_VERSION,
            f"{DOMAIN}.{entry_id}.profile",
            private=True,
            atomic_writes=True,
        )
        self._lock = asyncio.Lock()

    async def async_load(self) -> ProfileRecord:
        """Preserve malformed/unknown files; never replace them with defaults."""
        async with self._lock:
            try:
                document = await self.hass.async_add_executor_job(
                    _read_document, self._store.path
                )
            except FileNotFoundError:
                return ProfileRecord()
            except (OSError, ValueError) as err:
                raise ProfileStorageError("Cannot read saved profile") from err
            try:
                if document["version"] != PROFILE_SCHEMA_VERSION:
                    raise ValueError("Unsupported storage version")
                if document["key"] != self._store.key:
                    raise ValueError("Storage key mismatch")
                return ProfileRecord.from_dict(document["data"])
            except (KeyError, TypeError, ValueError) as err:
                raise ProfileStorageError("Saved profile requires recovery") from err

    async def async_save(self, record: ProfileRecord) -> None:
        """Save durably without letting cancellation release the write lock early."""
        data = record.to_dict()
        ProfileRecord.from_dict(data)
        async with self._lock:
            commit = asyncio.create_task(self._async_commit(data))
            cancelled = False
            while True:
                try:
                    await asyncio.shield(commit)
                    break
                except asyncio.CancelledError as err:
                    if commit.cancelled():
                        raise ProfileStorageError(
                            "Profile save was cancelled before verification"
                        ) from err
                    # Store writes run in an executor and cannot be stopped once
                    # started. Repeated caller cancellations must not release this
                    # lock until commit and independent readback both finish.
                    cancelled = True
            if cancelled:
                raise asyncio.CancelledError

    async def _async_commit(self, data: dict) -> None:
        """Write and independently verify one document while caller owns the lock."""
        try:
            await self._store.async_save(data)
            actual = await self.hass.async_add_executor_job(
                _read_document, self._store.path
            )
            if (
                actual.get("version") != PROFILE_SCHEMA_VERSION
                or actual.get("key") != self._store.key
                or actual.get("data") != data
            ):
                raise ValueError("Saved profile readback mismatch")
        except (OSError, ValueError, TypeError, AttributeError) as err:
            raise ProfileStorageError("Profile save could not be verified") from err
