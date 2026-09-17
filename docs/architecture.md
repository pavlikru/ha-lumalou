# Architecture

## Goal

Expose a Fisher-Price Lumalou (`gld09`) as a local Home Assistant device while
keeping Bluetooth protocol details in the upstream `lumalou` Python package.

## Boundaries

The upstream library owns:

- MPID framing and cryptography
- BLE protocol commands and response parsing
- Device client behavior that is not Home Assistant-specific

This integration owns:

- Home Assistant Bluetooth discovery and connection lifecycle
- Config entries and runtime state
- Entity mapping, availability, and device registry metadata
- Home Assistant diagnostics and translations

The integration must use Home Assistant's Bluetooth stack instead of creating a
separate global scanner. It must not access DFU or firmware-update functions.

## Modules

```text
custom_components/lumalou/
├── __init__.py       Config entry setup and unload
├── config_flow.py    Bluetooth discovery and UI setup
├── const.py          Domain and integration constants
├── models.py         Validated desired-profile and revision models
├── storage.py        Private, atomic per-entry profile Store
├── coordinator.py    Shared BLE state and command serialization
├── entity.py         Common entity base
├── light.py          Night-light controls
├── media_player.py   Audio and volume controls
├── select.py         Confirmed duration choices
├── sensor.py         Read-only diagnostics
├── services.py       Validated entry-targeted actions
├── diagnostics.py    Redacted support data
└── manifest.json     Integration metadata and pinned dependency
```

Standard Home Assistant entities are preferred over custom actions. This also
lets HomeKit Bridge expose the light and the supported subset of audio control
without adding Apple-specific transport code. The HomeKit path is implemented,
but pairing and control on the target Home Assistant/Apple Home are not yet
verified.

## State ownership

Each config entry owns one runtime object, coordinator, BLE session, and Store.
The Store key contains the config-entry ID, so multiple devices cannot share a
profile. `desired_profile` is the last user-confirmed durable revision.
`observed_state` is only a current-session device snapshot and never overwrites
the desired profile implicitly.

The coordinator registers address-scoped passive advertisement and unavailable
callbacks through Home Assistant's Bluetooth manager. Advertisement recovery is
coalesced and rate-limited and performs only handshake plus fresh state read.
Unavailable callbacks synchronously invalidate the observation and detach the
old session before asynchronous cleanup. They do not replay a saved write.

A persistent edit follows: validate → build revision → atomically save → verify
the on-disk envelope → publish desired revision → attempt BLE apply → obtain a
fresh callback. Offline edits remain pending. Play, stop, light off, clock sync,
and other transient commands are not queued for replay.

The options-flow profile editors are offline editors: saving one updates the
private Store and does not write the device. Only currently supported live
entity commands (light, audio playback/volume, durations, maintenance, refresh,
and clock sync) are sent to BLE, subject to the same fresh-state and hardware
validation limits below.

## Current recovery boundary

`lumalou==0.1.0` only delivers `GLOBAL_STATE` and can return cached data after a
timeout. The coordinator additionally requires a new callback generation, but
upstream still zero-pads short payloads and cannot read complete playlists,
schedules, or seven daily routines. Therefore a snapshot is partial and cannot
mark a full profile verified. Automatic full restore stays disabled until a
released upstream API provides strict block parsers and session-bound fresh
readback.

An unpublished upstream candidate implements that strict session contract and
the schedule/routine codecs. It is not consumed here: repository policy requires
a reviewed, released, exactly pinned dependency, and hardware validation must
still establish setter side effects and a safe restore order.

## Initial milestones

1. Config flow, Bluetooth discovery, safe session lifecycle, mocked tests.
2. Device registry, read-only state, light/audio controls, and profile Store.
3. Upstream typed schedule/playlist/routine codecs and strict fresh readback.
4. Import preview, complete manual restore, and hardware side-effect tests.
5. Background reconnect/auto-restore, ten power cycles, 72-hour soak, prerelease.
