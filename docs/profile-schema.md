# Persistent profile schema

Profile schema 3 is a Home Assistant-side logical model. It validates and
persists user intent but does not encode BLE payloads; `restore.py` maps it to
setter payloads. Top-level keys may be absent only in partial edits; a saved
profile from a device read or an import is always complete. An absent key is
never replaced by a default. The options-flow editors are offered only for a
complete profile.

The profile holds everything a power loss resets. On firmware 0.3.7 a power
loss restores factory settings: clock 05:00 on Sunday in 12-hour format,
playlist 1–12, all weekly times and routines 00:00 with empty slots, alarm
sound 0, routine music 1, reward sounds 1/1, routine volume 5, light timer 4,
playlist timer 5, volume 5 and LED brightness 5; light and sound off.

## Keys

- ordered `playlist` (zero to twelve song IDs 1–12);
- `clock_settings`: display boolean, brightness 0–9, format 0 (12-hour) or 1
  (24-hour);
- `routine_settings`: enabled boolean, music 0–15, volume 0–15, and task and
  routine reward sound nibbles 0–15. The setters carry a full byte, but the
  device reports music and volume as 4-bit values in its global state, so only
  0–15 can pass restore verification. Editors, imports and restore accept
  only 0–15;
- `ready_to_rise`: enabled boolean plus seven Sunday-first times;
- `sleepy_times`: seven Sunday-first times;
- `alarm`: seven alarm enum values 0–10 plus sound nibble 0–15;
- `routines`: exactly seven named days, each containing a time and exactly
  twelve ordered slots. A slot is null or a step 1–12/task 0–11 pair. Slot
  positions, holes, duplicate/non-monotonic step numbers, and task order are
  preserved;
- `light_and_sound`: `volume` 0–9, `light_brightness` 0–9, `light_duration`
  0–5 and `playlist_duration` 0–6 (the device enums). They are read from
  GLOBAL_STATE (brightness is kept while the light is off) and written with
  the live setters, which were verified not to switch light or sound on.
  Because the device buttons change them in everyday use, they are compared
  with the device only when the clock shows a reset. Changes made in Home
  Assistant are kept in the saved profile once the device confirms them.

Schedule/routine time is either `{hour: 0..23, minute: 0..59}` or null. Null is
the established `FF FF` no-scheduled-time encoding. It does not by itself prove
that a routine with task slots is disabled. `{hour: 0, minute: 0}` is midnight
and remains distinct.

## Not in the profile

- Light color and on/off, audio on/off, current source or song. A restore
  never turns light or sound on;
- current clock time (set from Home Assistant instead), timer remainder, nap
  state/alarm, executing alarm, current routine step and task status. These
  are transient or lack a persistent setter/readback contract. The nap alarm
  queries time out on the device and are never sent.

The `alarm` block is only the evidenced weekly alarm nibbles and sound nibble;
no separate ready-to-rise alarm activation or nap-alarm field is invented.

## Completeness and verification

`require_complete_profile()` is the structural prerequisite for a restore and an
import. It accepts only a valid profile containing every persistent key and all
seven days/slots. It does not prove complete readback, persistence, setter
safety, or hardware acceptance; only a fresh complete device read on the
enrolled device key marks a revision verified (see `docs/architecture.md`).

`restore.changed_blocks()` compares two complete profiles by top-level block
(optionally without `light_and_sound`); it drives reset detection and restore
verification. The restore executor
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
There is no migration from earlier builds; a record that does not match this
schema (including a schema 2 record without `light_and_sound`) is ignored with
a warning, and the device profile has to be read again.

`lumalou.export_profile` returns `{current_revision, profile}` where `profile`
is `{schema_version: 3, scope: "persistent_profile", profile: {...}}`.
`lumalou.import_profile` accepts only that envelope with a complete profile.
