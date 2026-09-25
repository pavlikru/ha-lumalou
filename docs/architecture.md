# Architecture

## Boundaries

The protocol library (`lumalou-gld09`, import name `lumalou`) owns MPID framing,
cryptography, command builders, response parsing, signed factory-key
verification and session binding. This integration owns only the Home Assistant
side: Bluetooth discovery and connection lifecycle, config entries, entities,
the saved profile, diagnostics and translations. Protocol or cryptography code
is not copied into this repository.

The integration uses Home Assistant's Bluetooth APIs (discovery matchers,
advertisement and unavailability callbacks, `BLEDevice` lookup). It never
starts its own scanner and never accesses the DFU service.

## Modules

```text
custom_components/lumalou/
├── __init__.py        Entry setup/unload, Repairs issue for damaged storage
├── config_flow.py     Discovery, confirmation, reconfigure, options/profile editors
├── coordinator.py     One serialized BLE session, reconnects, command policy
├── transport.py       Restricted GATT surface (allowlisted characteristics only)
├── identity.py        Read-only Device Information and factory-key probe
├── upstream_api.py    Checks that the installed library has the required API
├── models.py          Profile schema, validation, revisions, restore diff
├── storage.py         Private, atomic, per-entry profile Store
├── entity.py          Shared entity base and device info
├── light.py, media_player.py, select.py, switch.py, button.py,
│   sensor.py, binary_sensor.py
├── services.py        Entry-targeted actions
├── repairs.py         Fix flow for unreadable profile storage
└── diagnostics.py     Allowlisted, redacted diagnostics
```

## Identity and enrollment

1. Discovery matches connectable advertisements with Mattel manufacturer data
   (`manufacturer_id` 950, prefix `MB`).
2. The user confirms the candidate. The flow reads standard Device Information
   only as a conflict check (an explicit different model is rejected).
3. The flow reads the factory token; the library verifies its signature and
   returns a fingerprint of the signed device key. Only that fingerprint is
   stored, in the config entry, and used as the entry's unique ID. The token and
   serial never leave the library call.
4. Every later session passes the fingerprint to the library, which refuses a
   device with a different key before any session or TX write.
5. Controls stay locked (`protocol_verified` false) until one complete, strict
   profile read succeeds.

This is per-device enrollment. It does not prove which retail model a device
is, and it makes no claim about other hardware revisions.

## State ownership

Each config entry owns one coordinator, one BLE session and one Store
(`lumalou.<entry_id>.profile`).

- `desired_profile` is the last user-confirmed, revisioned profile. Every edit
  checks the expected revision (compare-and-swap) and is written atomically.
- The observed state is the latest fresh device notification. It never
  overwrites the desired profile implicitly.
- Offline edits stay pending. One-off commands (play, stop, light off, clock
  sync) are never queued or replayed.

## Connection lifecycle

- Advertisement callbacks mark the device present and schedule one read-only
  refresh, with exponential backoff from 30 seconds to 15 minutes on failure.
- The unavailability callback invalidates state and detaches the session
  synchronously, then closes it in the background.
- All device operations run under one lock; each connection has a generation
  number so late notifications from an old session are ignored.
- Maintenance mode disconnects and blocks all device I/O until turned off.
- Unload cancels running operations and closes Bluetooth; the Store is kept.

## Command policy

Only allowlisted application opcodes are sent (light, audio, volume, durations,
clock, state request). Pairing-complete (`0x34`) and time-prescaler (`0x52`) are
explicitly denied. The transport wrapper exposes only the factory read, RX
subscription, SESSION write and TX write characteristics.

## Apple Home

Only standard entity platforms are used, so HomeKit Bridge can export the light
(on/off, brightness) and the speaker (on/off switch) without Apple-specific
code. Configuration and diagnostic entities carry an entity category and are
excluded from HomeKit by default.

## Library fork

`stramanu/lumalou` 0.1.0 lacks strict fresh reads, schedule and routine codecs,
and signed identity binding. These are maintained in the
[`pavlikru/lumalou`](https://github.com/pavlikru/lumalou) fork and published as
`lumalou-gld09`, keeping the import name `lumalou`. If upstream releases an
equivalent API, switching back means changing only the manifest requirement
and the lock file; `upstream_api.py` checks the required API at runtime and
fails closed if anything is missing.
