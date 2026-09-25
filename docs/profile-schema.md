# Persistent profile schema

Storage schema 2 is a Home Assistant-side logical model. It validates and
persists user intent but does not encode BLE payloads, enable restore, or claim
hardware acceptance. Top-level keys may be absent, meaning that block is
unknown or was never saved. An absent key is not replaced by a default.

The persistent keys are:

- `brightness` (0–9), `color` (0–9), and `light_duration` (0–5);
- `volume` (0–9), ordered `playlist` (zero to twelve song IDs 1–12), and
  `playlist_duration` (0–6);
- `clock_settings`: display boolean, brightness 0–9, format 0 (12-hour) or 1
  (24-hour);
- `routine_settings`: enabled boolean, music byte 0–255, volume byte 0–255, and task
  and routine reward sound nibbles 0–15. The music byte has no established
  enum, and neither byte has a proven narrower hardware range, so the model
  preserves their validated numeric representations;
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
and remains distinct. Top-level absence is unknown.

`require_complete_profile()` is a structural prerequisite for a future restore.
It accepts only a valid profile containing every persistent key and all seven
days/slots. It does not prove complete readback, persistence, setter safety, or
hardware acceptance. Partial profiles remain valid saved intent but are never
structurally complete. V1 migration
preserves its subset, revision, previous revision, verified revision, pending
state, sync status, error, and maintenance flag without synthesizing fields.

`plan_profile_reconciliation()` compares a complete desired profile with a
complete observed snapshot bound to the current revision. Its sorted changed
block list is for preview and deterministic tests only; it is not a BLE write
order and does not execute setters or advance verification metadata. The
coordinator's `async_plan_profile_restore()` obtains a fresh strict readback,
checks the entry identity, maintenance state, and revision both before and after
readback, then returns only this diff. It still does not authorize setters.

Not persistent: current clock time/date, light/audio on/off, current source or
song, timer remainder, nap state/alarm, executing alarm, current routine step,
and task status. These are transient or lack an established persistent
setter/readback contract. The alarm profile block is only the evidenced weekly
alarm nibbles and sound nibble; no separate ready-to-rise alarm activation or
nap-alarm field is invented.

The playlist is canonical user intent: zero to twelve nonzero song IDs in order.
It is not an exact twelve-slot device dump and cannot preserve interior padding
zeros from an unknown standalone playlist response layout.
