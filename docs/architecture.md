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
├── __init__.py        Entry setup/unload/removal, restore Repair
├── config_flow.py     Discovery, confirmation, reconfigure, options/profile editors
├── coordinator.py     One live BLE session fed by device pushes, reconnects,
│                      reset detection, restore, clock sync
├── restore.py         Pure readback mapping and ordered restore steps (no I/O)
├── transport.py       HA connection path, restricted GATT/opcode surface and the
│                      read-only signed-identity probe
├── models.py          Profile schema, validation, revisions
├── storage.py         Per-entry profile on Home Assistant's Store helper
├── entity.py          Shared entity base and device info
├── light.py, media_player.py, select.py, switch.py, number.py,
│   button.py, sensor.py, binary_sensor.py
├── services.py        Entry-targeted profile actions
├── repairs.py         Fix flow for device settings that differ from the profile
└── diagnostics.py     Allowlisted, redacted diagnostics
```

## Identity and enrollment

1. Discovery matches connectable advertisements with Mattel manufacturer data
   (`manufacturer_id` 950, prefix `MB`).
2. The user confirms the candidate.
3. The flow connects once and reads only the factory token; the library
   verifies its signature and returns a fingerprint of the signed device key.
   (The target has no readable Device Information Model Number, and the signed
   key is the stronger binding, so no other characteristic is read.) Only that fingerprint is
   stored, in the config entry, and used as the entry's unique ID; entity and
   device registry IDs derive from it. The token and serial never leave the
   library call. Manual setup treats choosing the device as the confirmation;
   Bluetooth discovery shows a confirmation form.
4. Every later session passes the fingerprint to the library, which refuses a
   device with a different key before any session or TX write.
5. Controls stay locked (`protocol_verified` false) until one complete, strict
   profile read has been previewed and confirmed by the user. The same signed
   key at a new address (discovery or Reconfigure) only updates the address.

There are no config entry or storage migrations: no stable version was
released. A saved profile of an older schema is ignored on load, which locks
the controls until the device profile is read again. An entry without a
fingerprint (from an early development build) fails setup with a translated
error asking to remove and re-add it.

This is per-device enrollment. It does not prove which retail model a device
is, and it makes no claim about other hardware revisions.

## State ownership

Each config entry owns one coordinator, one BLE session and one private
`homeassistant.helpers.storage.Store` (`lumalou.<entry_id>.profile`, atomic
writes). Removing the entry deletes it. Store moves undecodable JSON aside as
`.corrupt.<timestamp>` and raises its own Repair; an invalid record is ignored
with a warning. Without a usable saved profile the entry starts empty and
controls stay locked until a device read is confirmed again.

- `desired_profile` is the last user-confirmed, revisioned profile of
  everything a power loss resets (see `docs/profile-schema.md`). Every edit
  checks the expected revision (compare-and-swap) under the coordinator lock.
- A revision is **verified** when a fresh complete read on the enrolled device
  key matched it (`verified_revision`, `verified_fingerprint`): a confirmed
  device read, a successful restore, or a reconnect whose read already equals
  a pending revision. Editor saves and imports are pending until then.
- Editors and imports require a complete profile; they never invent default
  values and never drop saved blocks.
- The observed state is the latest GLOBAL_STATE the live session received,
  pushed by the device after every command and button press. Light color and
  on/off and the playing state live only there.
- Volume, light brightness, the timers and the clock settings are also
  profile settings. When Home Assistant changes one and the next pushed state
  confirms it, the saved profile is updated in place (same revision, verified
  status kept), so a power-loss restore brings back the last choice. Changes
  made with the device buttons are shown but not saved.
- One-off commands (play, stop, light on/off) are never queued or replayed.

## Connection lifecycle

- One session stays open while the device is reachable. Only recovery, a
  profile read and a restore open sessions, always after a 1.5 second pause
  following the previous disconnect (an immediate reconnect sometimes fails
  once on the device). Live commands use the open session: success is the
  acknowledged write, and the device pushes the resulting state itself. They
  never open a session, read back or reconnect.
- Advertisement callbacks mark the device present and schedule one recovery
  pass when no session is live: at most one per 30 seconds, with exponential
  backoff up to 15 minutes on failure. A remote link loss of the live session
  (for example power loss) also schedules one.
- Before verification, recovery only reads GLOBAL_STATE. Afterwards it opens a
  fresh strict session, reads the complete profile and the device clock,
  writes the clock from Home Assistant local time if it is more than
  60 seconds off (never when the host clock looks unset), marks a pending
  revision verified if the device already matches it exactly, and compares
  the profile with the current verified revision.
- The device pushes CURRENT_DATE at least every minute. A pushed clock more
  than 60 seconds off (DST, drift) is corrected in the live session, at most
  once an hour; a failed automatic write pauses automatic writes for an hour.
- A device clock more than 10 minutes off on reconnect is the **reset marker**
  (a power loss resets it to 05:00 on Sunday). With it, every block is
  compared; without it, the light and sound block is skipped because the
  device buttons change it in everyday use. A difference sets
  `restore_needed` (`reset` stays set until the event is resolved). A reset
  with the `auto_restore` option on (the default) runs the restore executor,
  at most twice per event; a failed attempt ends the session so the next one
  starts fresh. The `profile_restore_needed` Repair is raised for a
  difference without a reset, with automatic restore off, or once the
  attempts are used up (error severity).
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
in a fixed order (clock settings, playlist, light and playlist timers, volume
and LED brightness, routine sound and volume, weekly times, alarms and
routines, then the Ready-to-Rise and routine on/off flags), then a new session
with a complete read. Color, play/stop, soother, nap and routine start are
never part of a restore; the timer, volume and brightness setters were
verified on hardware not to switch light or sound on. Only a full
match marks the revision verified; otherwise `ProfileRestoreError` reports
the applied steps and the error (`restore_write`, `restore_verify` or
`restore_mismatch`). The executor never retries by itself and refuses a
revision verified on a different device key.

## Command policy

Only allowlisted application opcodes are sent: live controls (light, audio,
volume, timers, clock, clock settings, state request), the profile setters
used by restore, and read-only profile queries (never the nap alarm queries,
which time out on the device). User-state refusals (controls locked,
maintenance, untrusted host clock) are `ServiceValidationError`; device
failures are translated `HomeAssistantError`. Aggregate SET_GLOBAL_STATE (`0x01`), the soother
SET_GLOBAL_ON (`0x03`), pairing-complete (`0x34`) and time-prescaler (`0x52`)
are explicitly denied; nap, routine start and firmware commands are not in
any allowlist. The transport wrapper exposes only
the factory read, RX subscription, SESSION write and TX write characteristics
and connects through Home Assistant's `establish_connection`.

## Apple Home

Only standard entity platforms are used, so HomeKit Bridge can export the light
(on/off, brightness) and the speaker (on/off switch; on is the soother)
without Apple-specific code. Configuration and diagnostic entities carry an entity category and are
excluded from HomeKit by default.

## Library fork

`stramanu/lumalou` 0.1.0 lacks strict fresh reads, schedule and routine codecs,
and signed identity binding. These are maintained in the
[`pavlikru/lumalou`](https://github.com/pavlikru/lumalou) fork and published as
`lumalou-gld09`, keeping the import name `lumalou`. The manifest pins the exact
release, so no runtime capability check is needed. If upstream releases an
equivalent API, switching back means changing the manifest requirement and the
lock file.
