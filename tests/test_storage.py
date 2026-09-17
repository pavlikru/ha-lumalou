"""Test disk verification separately from Store's in-memory view."""

import asyncio
import json
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from custom_components.lumalou.models import ProfileRecord, ProfileValidationError
from custom_components.lumalou.storage import ProfileStorageError, ProfileStore


@pytest.fixture
def storage(tmp_path):
    """Use a minimal HA executor and simulated Store, but actual disk reads."""

    async def executor(function, *args):
        return function(*args)

    hass = SimpleNamespace(async_add_executor_job=executor)
    key = "lumalou.synthetic-entry.profile"
    path = tmp_path / key

    async def write(data):
        path.write_text(json.dumps({"version": 1, "key": key, "data": data}))

    backend = SimpleNamespace(
        path=str(path), key=key, async_save=AsyncMock(side_effect=write)
    )
    with patch(
        "custom_components.lumalou.storage.Store", return_value=backend
    ) as factory:
        adapter = ProfileStore(hass, "synthetic-entry")
    factory.assert_called_once_with(hass, 1, key, private=True, atomic_writes=True)
    return adapter, backend, path


async def test_missing_then_saved_profile_survives_new_adapter(storage):
    adapter, backend, path = storage
    assert await adapter.async_load() == ProfileRecord()
    record = ProfileRecord(
        revision=1, desired_profile={"playlist": [2, 1]}, pending=True
    )
    await adapter.async_save(record)
    assert path.exists()
    # A second adapter has no copy of the saved record or Store load cache.
    with patch("custom_components.lumalou.storage.Store", return_value=backend):
        reopened = ProfileStore(adapter.hass, "synthetic-entry")
    assert await reopened.async_load() == record


@pytest.mark.parametrize(
    "document",
    [
        "not JSON",
        "[]",
        "{}",
        '{"version":2,"key":"lumalou.synthetic-entry.profile","data":{}}',
        '{"version":1,"key":"other-entry","data":{}}',
        '{"version":1,"key":"lumalou.synthetic-entry.profile","data":{}}',
    ],
)
async def test_corrupt_file_is_preserved(storage, document):
    adapter, backend, path = storage
    path.write_text(document)
    with pytest.raises(ProfileStorageError):
        await adapter.async_load()
    assert path.read_text() == document
    backend.async_save.assert_not_awaited()


async def test_disk_read_error_does_not_become_empty(storage):
    adapter, _, _ = storage
    with (
        patch(
            "custom_components.lumalou.storage._read_document",
            side_effect=PermissionError,
        ),
        pytest.raises(ProfileStorageError, match="Cannot read"),
    ):
        await adapter.async_load()


async def test_swallowed_write_failure_cannot_use_old_disk_state(storage):
    adapter, backend, _ = storage
    old = ProfileRecord(revision=1, desired_profile={"volume": 1})
    await adapter.async_save(old)
    backend.async_save.side_effect = None
    with pytest.raises(ProfileStorageError, match="could not be verified"):
        await adapter.async_save(
            replace(old, revision=2, desired_profile={"volume": 2})
        )
    assert await adapter.async_load() == old


@pytest.mark.parametrize(
    "failure", [OSError("disk full"), PermissionError("read only")]
)
async def test_write_failure_propagates_without_publishing_success(storage, failure):
    adapter, backend, path = storage
    backend.async_save.side_effect = failure
    with pytest.raises(ProfileStorageError):
        await adapter.async_save(ProfileRecord())
    assert not path.exists()


@pytest.mark.parametrize(
    "readback",
    [
        {"version": 2},
        {"version": 1, "key": "wrong"},
        {"version": 1, "key": "lumalou.synthetic-entry.profile", "data": {}},
        [],
    ],
)
async def test_independent_disk_readback_mismatch(storage, readback):
    adapter, _, _ = storage
    with (
        patch(
            "custom_components.lumalou.storage._read_document", return_value=readback
        ),
        pytest.raises(ProfileStorageError),
    ):
        await adapter.async_save(ProfileRecord())


async def test_invalid_record_never_reaches_disk(storage):
    adapter, backend, _ = storage
    with pytest.raises(ProfileValidationError):
        await adapter.async_save(ProfileRecord(desired_profile={"volume": True}))
    backend.async_save.assert_not_awaited()


async def test_cancelled_save_holds_lock_until_commit_finishes(tmp_path):
    """An executor-backed old write cannot land after a newer revision."""

    async def executor(function, *args):
        return function(*args)

    hass = SimpleNamespace(async_add_executor_job=executor)
    key = "lumalou.cancellation.profile"
    path = tmp_path / key
    started = asyncio.Event()
    release = asyncio.Event()

    async def write(data):
        if data["revision"] == 1:
            started.set()
            await release.wait()
        path.write_text(json.dumps({"version": 1, "key": key, "data": data}))

    backend = SimpleNamespace(
        path=str(path), key=key, async_save=AsyncMock(side_effect=write)
    )
    with patch("custom_components.lumalou.storage.Store", return_value=backend):
        adapter = ProfileStore(hass, "cancellation")

    first_record = ProfileRecord(
        revision=1, desired_profile={"volume": 3}, pending=True
    )
    second_record = ProfileRecord(
        revision=2, desired_profile={"volume": 4}, pending=True
    )
    first = asyncio.create_task(adapter.async_save(first_record))
    await started.wait()
    first.cancel()
    second = asyncio.create_task(adapter.async_save(second_record))
    await asyncio.sleep(0)
    assert backend.async_save.await_count == 1
    first.cancel()
    await asyncio.sleep(0)
    assert backend.async_save.await_count == 1

    release.set()
    with pytest.raises(asyncio.CancelledError):
        await first
    await second
    assert await adapter.async_load() == second_record


async def test_internally_cancelled_commit_is_not_reported_as_durable(storage):
    """Only caller cancellation after a completed commit can publish the record."""
    adapter, _, _ = storage
    with (
        patch.object(
            adapter, "_async_commit", new=AsyncMock(side_effect=asyncio.CancelledError)
        ),
        pytest.raises(ProfileStorageError, match="cancelled before verification"),
    ):
        await adapter.async_save(ProfileRecord())
