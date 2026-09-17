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
- Home Assistant diagnostics, translations, and repairs

The integration must use Home Assistant's Bluetooth stack instead of creating a
separate global scanner. It must not access DFU or firmware-update functions.

## Planned modules

```text
custom_components/lumalou/
├── __init__.py       Config entry setup and unload
├── config_flow.py    Bluetooth discovery and UI setup
├── const.py          Domain and integration constants
├── coordinator.py    Shared device state and command serialization
├── entity.py         Common entity base
├── light.py          Night-light controls
├── media_player.py   Audio and volume controls
├── select.py         Routine, playlist, and mode choices
├── sensor.py         Read-only device state
├── time.py           Clock and schedule times
└── manifest.json     Integration metadata and pinned dependency
```

This list is a design direction, not a commitment to create every platform.
Standard Home Assistant entities should be preferred over custom actions.

## Initial milestones

1. Config flow, Bluetooth discovery, connect/disconnect, and mocked tests.
2. Device registry plus read-only state and availability.
3. Light controls.
4. Audio and routine controls.
5. Schedules, diagnostics, translations, and HACS release process.
