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
├── __init__.py        Entry setup/unload/removal, registry migration, Repairs issues
├── config_flow.py     Discovery, confirmation, reconfigure, options/profile editors
├── coordinator.py     One serialized BLE session, reconnects, restore, clock sync
├── restore.py         Pure readback mapping and ordered restore steps (no I/O)
├── transport.py       HA connection path and restricted GATT/opcode surface
├── identity.py        Read-only Device Information and factory-key probe
├── models.py          Profile schema, validation, revisions
├── storage.py         Private, atomic, per-entry profile Store
├── entity.py          Shared entity base, device info, identifier migration
├── light.py, media_player.py, select.py, switch.py, button.py,
│   sensor.py, binary_sensor.py
├── services.py        Entry-targeted profile actions
├── repairs.py         Fix flows for unreadable storage and differing settings
└── diagnostics.py     Allowlisted, redacted diagnostics
```

## Identity and enrollment

1. Discovery matches connectable advertisements with Mattel manufacturer data
   (`manufacturer_id` 950, prefix `MB`).
2. The user confirms the candidate. The flow reads standard Device Information
   only as a conflict check (an explicit different model is rejected).
3. The flow reads the factory token; the library verifies its signature and
   returns a fingerprint of the signed device key. Only that fingerprint is
   stored, in the config entry, and used as the entry's unique ID; entity and
   device registry IDs derive from it. The token and serial never leave the
   library call. Manual setup treats choosing the device as the confirmation;
   Bluetooth discovery shows a confirmation form.
4. Every later session passes the fingerprint to the library, which refuses a
   device with a different key before any session or TX write.
5. Controls stay locked (`protocol_verified` false) until one complete, strict
   profile read has been previewed and confirmed by the user.

Config entry 1.2 moved registry IDs from the Bluetooth address to the entry
unique ID. `async_migrate_entry` migrates enrolled entries; entries created
before enrollment keep their address IDs, stay blocked and get a Repair until
Reconfigure verifies the device and migrates them.

This is per-device enrollment. It does not prove which retail model a device
is, and it makes no claim about other hardware revisions.

## State ownership

Each config entry owns one coordinator, one BLE session and one Store
(`lumalou.<entry_id>.profile`). Removing the entry deletes the Store.

- `desired_profile` is the last user-confirmed, revisioned profile. Every edit
  checks the expected revision (compare-and-swap) and is written atomically.
- A revision is **verified** when a fresh complete read on the enrolled device
  key matched it (`verified_revision`, `verified_fingerprint`). A confirmed
  device read, a successful restore, or a live scalar change on top of a
  verified revision that fresh GLOBAL_STATE confirms produce verified
  revisions. Editor saves and imports are pending until a restore.
- Editors require a complete profile; they never invent default values.
- The observed state is the latest fresh device notification. It never
  overwrites the desired profile implicitly.
- One-off commands (play, stop, light off) are never queued or replayed.

## Connection lifecycle

- Advertisement callbacks mark the device present and schedule one recovery
  pass when no session is live: at most one per 30 seconds, with exponential
  backoff up to 15 minutes on failure. A remote link loss of the live session
  (for example power loss) also schedules one.
- Before verification, recovery only reads GLOBAL_STATE. Afterwards it opens a
  fresh strict session, reads the complete profile and the device clock,
  writes the clock from Home Assistant local time if it is more than
  60 seconds off (never when the host clock looks unset), and compares the
  profile with the current verified revision.
- A mismatch sets `restore_needed` and raises the `profile_restore_needed`
  Repair. With the `auto_restore` option on, the coordinator runs the restore
  executor instead, at most twice per detected event; then the Repair takes
  over with error severity.
- The unavailability callback invalidates state and detaches the session
  synchronously, then closes it in the background.
- All device operations run under one lock; each connection has a generation
  number so late notifications from an old session are ignored.
- Maintenance mode disconnects and blocks all device I/O until turned off.
- Unload cancels running operations and closes Bluetooth; the Store is kept.

## Restore executor

`async_restore_profile(expected_revision, confirmed=True)` runs under the
coordinator lock: revision check, new strict session with a complete read,
clock correction, the minimal setter writes from `restore.build_restore_steps`
in a fixed order (clock display, timers, audio, routine sound and volume,
schedules and routines, color and brightness, then the Ready-to-Rise and
routine on/off flags), then a new session with a complete read. Only a full
match marks the revision verified; otherwise `ProfileRestoreError` reports
the applied steps and the error (`restore_write`, `restore_verify` or
`restore_mismatch`). The executor never retries by itself and refuses a
revision verified on a different device key.

## Command policy

Only allowlisted application opcodes are sent: live controls (light, audio,
volume, durations, clock, state request), the profile setters used by restore,
and read-only profile queries. Pairing-complete (`0x34`) and time-prescaler
(`0x52`) are explicitly denied; aggregate state, nap, routine start and
firmware commands are not in any allowlist. The transport wrapper exposes only
the factory read, RX subscription, SESSION write and TX write characteristics
and connects through Home Assistant's `establish_connection`.

## Apple Home

Only standard entity platforms are used, so HomeKit Bridge can export the light
(on/off, brightness) and the speaker (on/off switch) without Apple-specific
code. Configuration and diagnostic entities carry an entity category and are
excluded from HomeKit by default.

## Library fork

`stramanu/lumalou` 0.1.0 lacks strict fresh reads, schedule and routine codecs,
and signed identity binding. These are maintained in the
[`pavlikru/lumalou`](https://github.com/pavlikru/lumalou) fork and published as
`lumalou-gld09`, keeping the import name `lumalou`. The manifest pins the exact
release, so no runtime capability check is needed. If upstream releases an
equivalent API, switching back means changing the manifest requirement and the
lock file.
