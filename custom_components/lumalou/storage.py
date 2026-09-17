"""Private atomic storage with independent on-disk save verification."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.storage import Store

from .const import DOMAIN, PROFILE_SCHEMA_VERSION
from .models import ProfileRecord, migrate_v1_record

STORAGE_MINOR_VERSION = 1


class ProfileStorageError(HomeAssistantError):
    """A profile could not be loaded or durably checked after saving."""


def _read_document(path: str) -> dict:
    """Read from disk, deliberately bypassing Store's memory cache."""
    with Path(path).open(encoding="utf-8") as handle:
        return json.load(handle)


def _create_migration_backup(path: str, version: int, expected: dict) -> None:
    """Preserve the original bytes once before replacing a validated old schema."""
    source = Path(path)
    contents = source.read_bytes()
    if json.loads(contents) != expected:
        raise ValueError("Profile changed while preparing migration")
    backup = Path(f"{path}.v{version}.backup")
    try:
        descriptor = os.open(backup, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        if backup.read_bytes() != contents:
            raise ValueError("Conflicting profile migration backup") from None
        return
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(contents)
            handle.flush()
            os.fsync(handle.fileno())
    except BaseException:
        backup.unlink(missing_ok=True)
        raise


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
            minor_version=STORAGE_MINOR_VERSION,
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
                if not isinstance(document, dict) or set(document) not in (
                    {"version", "key", "data"},
                    {"version", "minor_version", "key", "data"},
                ):
                    raise ValueError("Invalid storage document")
                minor_version = document.get("minor_version", STORAGE_MINOR_VERSION)
                if (
                    type(minor_version) is not int
                    or minor_version != STORAGE_MINOR_VERSION
                ):
                    raise ValueError("Unsupported storage minor version")
                version = document["version"]
                if (
                    type(version) is not int
                    or not 1 <= version <= PROFILE_SCHEMA_VERSION
                ):
                    raise ValueError("Unsupported storage version")
                if document["key"] != self._store.key:
                    raise ValueError("Storage key mismatch")
                if version == PROFILE_SCHEMA_VERSION:
                    return ProfileRecord.from_dict(document["data"])
                record = migrate_v1_record(document["data"])
            except (KeyError, TypeError, ValueError) as err:
                raise ProfileStorageError("Saved profile requires recovery") from err
            migration = asyncio.create_task(
                self._async_migrate(document, version, record.to_dict())
            )
            cancelled = False
            while True:
                try:
                    await asyncio.shield(migration)
                    break
                except asyncio.CancelledError as err:
                    if migration.cancelled():
                        raise ProfileStorageError(
                            "Profile migration was cancelled before verification"
                        ) from err
                    cancelled = True
                except (OSError, ValueError, TypeError, AttributeError) as err:
                    raise ProfileStorageError(
                        "Profile migration could not be verified"
                    ) from err
            if cancelled:
                raise asyncio.CancelledError
            return record

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
                not isinstance(actual, dict)
                or set(actual) != {"version", "minor_version", "key", "data"}
                or actual.get("version") != PROFILE_SCHEMA_VERSION
                or actual.get("minor_version") != STORAGE_MINOR_VERSION
                or actual.get("key") != self._store.key
                or actual.get("data") != data
            ):
                raise ValueError("Saved profile readback mismatch")
        except (OSError, ValueError, TypeError, AttributeError) as err:
            raise ProfileStorageError("Profile save could not be verified") from err

    async def _async_migrate(self, document: dict, version: int, data: dict) -> None:
        """Back up a validated legacy document, then durably publish its upgrade."""
        await self.hass.async_add_executor_job(
            _create_migration_backup, self._store.path, version, document
        )
        await self._async_commit(data)
