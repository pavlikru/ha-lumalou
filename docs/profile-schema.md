# Persistent profile schema

Storage schema 2 is a Home Assistant-side logical model. It validates and
persists user intent but does not encode BLE payloads or claim hardware
acceptance; `restore.py` maps it to setter payloads. Top-level keys may be
absent only in partial edits; a saved profile from a device read or an import
is always complete. An absent key is never replaced by a default. The
options-flow editors are offered only for a complete profile.

## Persistent keys

The profile holds persistent configuration only:

- ordered `playlist` (zero to twelve song IDs 1–12);
- `clock_settings`: display boolean, brightness 0–9, format 0 (12-hour) or 1
  (24-hour);
- `routine_settings`: enabled boolean, music 0–15, volume 0–15, and task and
  routine reward sound nibbles 0–15. The setters carry a full byte and music
  has no established enum, but the device reports music and volume as 4-bit
  values in its global state, so only 0–15 can pass restore verification
  (hardware assumption A2). Editors, imports and restore accept only 0–15;
- `ready_to_rise`: enabled boolean plus seven Sunday-first times;
- `sleepy_times`: seven Sunday-first times;
- `alarm`: seven alarm enum values 0–10 plus sound nibble 0–15;
- `routines`: exactly seven named days, each containing a time and exactly
  twelve ordered slots. A slot is null or a step 1–12/task 0–11 pair. Slot
  positions, holes, duplicate/non-monotonic step numbers, and task order are
  preserved.

Schedule/routine time is either `{hour: 0..23, minute: 0..59}` or null. Null is
the established `FF FF` no-scheduled-time encoding. It does not by itself prove
that a routine with task slots is disabled. `{hour: 0, minute: 0}` is midnight
and remains distinct.

## Current state, not profile

These values are deliberately **not** part of the profile, so they are never
compared for power-loss detection and never written by a restore (automatic or
explicit):

- light brightness and color, and audio volume. They change in everyday use
  (Home Assistant, the device buttons, the app), and the device reports
  brightness 0 whenever the light is off. Comparing them would flag every
  reconnect with the light off as a power loss, and restoring them could turn
  the nursery light on at night;
- the light and playlist timers. The protocol has dedicated queries for them
  (`6D → 95`, `43 → 1A`), but the pinned library has no typed decoder for those
  responses, so they can only be read from GLOBAL_STATE nibbles next to the
  live values. Without a strict dedicated read they are treated as live state;
- light/audio on/off, current source or song, current clock time/date, timer
  remainder, nap state/alarm, executing alarm, current routine step and task
  status. These are transient or lack an established persistent
  setter/readback contract.

The `alarm` block is only the evidenced weekly alarm nibbles and sound nibble;
no separate ready-to-rise alarm activation or nap-alarm field is invented.

## Completeness and verification

`require_complete_profile()` is the structural prerequisite for a restore and an
import. It accepts only a valid profile containing every persistent key and all
seven days/slots. It does not prove complete readback, persistence, setter
safety, or hardware acceptance; only a fresh complete device read on the
enrolled device key marks a revision verified (see `docs/architecture.md`).

`restore.changed_blocks()` compares two complete profiles by top-level block;
it drives power-loss detection and restore verification. The restore executor
(`async_restore_profile`) derives its own ordered setter steps from a fresh
read with `restore.build_restore_steps()`.

The playlist is canonical user intent: zero to twelve nonzero song IDs in order.
It is not an exact twelve-slot device dump and cannot preserve interior padding
zeros from an unknown standalone playlist response layout.

## Record and export

The saved record adds revision metadata: `revision`, the `previous` revision
for undo, `verified_revision` with the `verified_fingerprint` of the device key
that verified it, `pending`, `sync_status` (`empty`, `saved`, `pending`,
`applying`, `error`), the symbolic `last_error` and the `maintenance` flag.
There is no migration from development builds; a record that does not match
this schema is ignored with a warning.

`lumalou.export_profile` returns `{current_revision, profile}` where `profile`
is `{schema_version: 2, scope: "persistent_profile", profile: {...}}`.
`lumalou.import_profile` accepts only that envelope with a complete profile.
