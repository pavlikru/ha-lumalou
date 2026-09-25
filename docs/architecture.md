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
│   button.py, sensor.py, binary_sensor.py, event.py
├── services.py        Entry-targeted profile and routine actions
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
  a pending revision. Editor confirmations and `lumalou.set_routine` apply
  the edit at once through the restore executor
  (`async_apply_profile_edit`: fresh read, new revision with the light and
  sound levels taken from that read, minimal writes, verifying read). If the
  device cannot be read (or maintenance is on) the edit is saved as a pending
  revision and a translated `profile_saved_not_applied` error is shown; a
  running routine refuses the edit without saving. Imports stay pending until
  restored.
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
- One-off commands (play, stop, light on/off, routine start and control) are
  never queued or replayed.

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
  the profile with the current revision when it is verified, or pending but
  edited from a revision verified on this device key.
- The device pushes CURRENT_DATE at least every minute. A pushed clock more
  than 60 seconds off (drift) is corrected in the live session, at most once
  an hour, or at once when more than 10 minutes off (DST). A failed automatic
  write pauses automatic writes for an hour; in recovery it fails the pass
  (retried after the backoff, then without the paused write).
- Any frame re-arms a three-minute silence timer; when it expires the session
  is invalidated and closed and recovery is scheduled, as for a link loss.
- The **reset marker** is the power-loss clock: a power loss restarts the
  device clock at 05:00:00 on Sunday. On reconnect the clock must be more
  than 10 minutes off, not off by whole hours (±2 minutes: DST or a time
  zone change, which only sets the clock, unless the whole device read equals
  the power-loss defaults, `restore.is_factory_default`), on Sunday, and
  between 05:00 and
  05:00 plus the time since the last frame Home Assistant received from the
  device plus 10 minutes (12 hours when unknown, for example after a
  restart). With it, every block is
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

## Routines

- Day routines, routine mode (automatic start) and the routine sounds and
  volume are profile blocks. The routine setting entities write through the
  live session and update the saved profile in place once the pushed state
  confirms them, like the clock entities. `lumalou.set_routine` edits the
  routines of the saved profile (new revision; the light and sound levels are
  taken from the device read so the action never reverts button changes) and
  applies it with the restore executor, so the result is verified by a fresh
  read. The Daily routines editor uses the same path.
- A routine is one task per step (`step` 1..N), task ids 1..11, each task at
  most once, because ROUTINE_TASK_STATUS reports progress per task id. No
  tasks means no routine that day (no time).
- Progress comes from pushes: GLOBAL_STATE `operationMode` 7 is routine mode,
  ROUTINE_TASK_STATUS (`0x94`) carries the current step and one nibble per
  task id (0 pending, 1 current, 2 done). Step 0 is the silent preview; step
  N+1 (no current task, some done) is the completed routine, after which the
  device resets the status and leaves routine mode. The status is runtime
  only and reset with each session.
- Events are derived from two consecutive statuses of one session while in
  routine mode: a task nibble 1 -> 2 is `task_completed`; the first completed
  status is `routine_completed`; leaving routine mode (7 -> other) without it
  is `routine_cancelled`. Nothing is inferred across a reconnect.
- Start (button or action) sends `0x7B`, waits for the pushed state to show
  routine mode (else writes a one-off day back and raises
  `routine_not_started`), waits one second and sends `0x6B 0`, so task 1
  becomes current with its music like a scheduled start. The start button is
  unavailable, and restores and profile edits are refused, while a routine
  runs.
  Control buttons send `0x6B` 0 (complete, the remote's check-mark), 1
  (previous) or 4 (cancel) and are refused unless routine mode is on.
- One-off routine (`start_routine` with tasks): the tasks are written as
  today's routine (today's saved time) in the live session, then started. The
  saved profile is not changed. "Today" is the weekday of the device clock
  when a pushed clock is known (else Home Assistant's). The weekday is kept in
  the private profile record (`temporary_routine_day`, saved before the
  write) so it survives a restart.
  When the live session sees routine mode end, the saved day routine is
  written back and the marker cleared. If the session is gone, the next
  recovery writes it back once the device is out of routine mode; while the
  one-off routine still runs, that day is compared as saved, so it never
  raises a Repair or blocks a pending revision. A verified restore (including
  `set_routine`) or keeping the device profile also clears the marker. Only
  one day is tracked; a new one-off start first writes back an earlier one.

## Restore executor

`async_restore_profile(expected_revision, confirmed=True)` runs under the
coordinator lock: revision check, new strict session with a complete read,
clock correction, the minimal setter writes from `restore.build_restore_steps`
in a fixed order (clock settings, playlist, light and playlist timers, volume
and LED brightness, routine sound and volume, weekly times, alarms and
routines, then the Ready-to-Rise and routine on/off flags), then a new session
with a complete read. Color, play/stop, soother, nap, routine start and
routine control are never part of a restore; the timer, volume and brightness setters were
verified on hardware not to switch light or sound on. Only a full
match marks the revision verified; otherwise `ProfileRestoreError` reports
the applied steps and the error (`restore_write`, `restore_verify` or
`restore_mismatch`). The executor never retries by itself and refuses a
revision verified on a different device key.

## Command policy

Only allowlisted application opcodes are sent: live controls (light, audio,
volume, timers, clock, clock settings, routine mode and sounds, routine start
and control, state request), the profile setters used by restore, and
read-only profile queries (never the nap alarm queries, which time out on the
device). Routine start (`0x7B`, no argument) and routine control (`0x6B` with
code 0..4) are accepted only as those exact payloads. They became live
controls after the hardware validation: start only enters routine mode with a
silent preview, the control codes behave as in the app (the remote's
check-mark is code 0) and cancel returns silently to normal mode. User-state refusals (controls locked,
maintenance, untrusted host clock) are `ServiceValidationError`; device
failures are translated `HomeAssistantError`. Aggregate SET_GLOBAL_STATE (`0x01`), the soother
SET_GLOBAL_ON (`0x03`), pairing-complete (`0x34`), nap start and nap alarm
(`0x4D`, `0x4F`) and time-prescaler (`0x52`) are explicitly denied; firmware
commands are not in any allowlist. The transport wrapper exposes only
the factory read, RX subscription, SESSION write and TX write characteristics
and connects through Home Assistant's `establish_connection`.

## Apple Home

Only standard entity platforms are used, so HomeKit Bridge can export the light
(on/off, brightness) and the speaker (on/off switch; on is the soother)
without Apple-specific code. Configuration and diagnostic entities carry an
entity category and are excluded from HomeKit by default; the routine
buttons, sensors and event are in domains that a HomeKit Bridge does not
include by default.

## Library fork

`stramanu/lumalou` 0.1.0 lacks strict fresh reads, schedule and routine codecs,
and signed identity binding. These are maintained in the
[`pavlikru/lumalou`](https://github.com/pavlikru/lumalou) fork and published as
`lumalou-gld09`, keeping the import name `lumalou`. The manifest pins the exact
release, so no runtime capability check is needed. If upstream releases an
equivalent API, switching back means changing the manifest requirement and the
lock file.
