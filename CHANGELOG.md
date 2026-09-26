# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.2] - 2026-09-25

Maintenance release. Requires Home Assistant 2026.9.0 or newer and
`lumalou-gld09` 0.3.1.

### Changed

- Requires `lumalou-gld09` 0.3.1. The library API is unchanged from 0.3.0;
  the release adds a `--yes` confirmation to the CLI `send` command, a
  security policy and CI on Python 3.10 to 3.14.

### Documentation

- `AGENTS.md` describes the released, hardware-validated integration, the
  release process and the current checks; `CONTRIBUTING.md` asks for a pull
  request with green CI before tagging a release.
- `docs/protocol-audit.md` is marked as a historical pre-implementation audit.

## [0.1.1] - 2026-09-25

Fixes from setting up routine automations on hardware (firmware 0.3.7).
Requires Home Assistant 2026.9.0 or newer and `lumalou-gld09` 0.3.0.

### Fixed

- **Start routine** and `lumalou.start_routine` failed with "Lumalou did not
  start the routine" while **Routines** was off: the device ignores a start
  in that case. The start is now refused before anything is written, with an
  error asking to switch **Routines** on (it is never switched on
  silently).
- Light and sound commands (light on or off, the media player's source, on
  and off) reported success during a routine although the device ignores
  them. They are now refused with an error asking to finish or cancel the
  routine first.

### Documentation

- New README section "Scheduling routines with automations": several
  routines a day with time triggers, `lumalou.start_routine` with the
  trigger id as the task, a weekday condition, cancelling a running routine
  first, **Routines** kept on and empty day routines.
- The light and playlist timers are global device settings (they also apply
  to light and sound started with the remote); wake-up and sleep examples.
- Hardware findings recorded in `docs/hardware-validation.md`.

## [0.1.0] - 2026-09-25

First stable release, validated on real hardware: a Lumalou with firmware
0.3.7 and Home Assistant on a Raspberry Pi 4 with its onboard Bluetooth
adapter (see `docs/hardware-validation.md`). Requires Home Assistant 2026.9.0
or newer and `lumalou-gld09` 0.3.0. The pre-releases below list the changes
in detail.

### Added

- Bluetooth discovery and setup bound to the signed identity of the device;
  one Bluetooth session stays open and the device pushes every change, also
  those made with its buttons and the remote.
- Night light (on/off, brightness, the device's color palette as effects),
  speaker (the soother, built-in sounds, volume), light and playlist timers,
  clock format, display and brightness.
- Routines: per-day routines (time and ordered tasks) in the options flow and
  with `lumalou.set_routine`; the **Routines** switch and routine sound
  settings; **Start routine**, **Complete task**, **Previous task** and
  **Cancel routine** buttons; `lumalou.start_routine` (also with one-off
  tasks); **Routine** and **Current task** sensors and a **Routine** event
  entity (`task_completed`, `routine_completed`, `routine_cancelled`,
  `routine_expired`).
- A private, revisioned profile of everything a power loss resets. Profile
  edits and `lumalou.set_routine` are written to the device and verified with
  a fresh read; export, import and restore actions.
- Power-loss detection and automatic, verified restore (on by default), a
  Repair for other differences, maintenance mode, redacted diagnostics,
  English and Russian translations, Apple Home through HomeKit Bridge.

### Fixed

- A pushed GLOBAL_STATE was logged as an unused push at debug level; the
  state itself was applied.

### Known limitations

- One Bluetooth connection at a time: close the Fisher-Price app.
- The soother light keeps cycling after its music stops.
- A scheduled routine plays no music on firmware 0.3.7.
- Naps are not supported.
- Event entities show their last event again after being unavailable; use
  `not_from: unavailable` in automations.

## [0.1.0b8] - 2026-09-25

Fixes from the fourth Home Assistant run on hardware (Raspberry Pi 4).

### Fixed

- After a scheduled routine started, Home Assistant received no updates
  (the sensor stayed at `ready`) and the silence watchdog never reconnected.
  Only state, clock and routine frames now count as signs of life (other
  pushes are logged at debug level), the limit is 90 seconds while a routine
  runs, and a reconnect during a routine reads its progress (routine task
  status), so the current task shows again. Reproduced with the device
  emulator.
- `routine_completed` fired at every reconnect of `lumalou.set_routine`
  although no routine ran. Routine events now come only from two statuses
  seen in one session while a routine runs.
- The power-loss clock is accepted from 04:55 on Sunday (hardware read
  04:59:07 right after a power loss).

## [0.1.0b7] - 2026-09-25

Fixes from the third Home Assistant run on hardware (Raspberry Pi 4).

### Fixed

- A power loss was still not restored automatically (0.1.0b6 on hardware).
  An end-to-end test with the real client, encrypted frames and a device
  emulator gives exactly the reported diagnostics when a replug resets the
  settings but leaves the device clock away from 05:00 Sunday: only the clock
  is set and a Repair is raised (with the 05:00 Sunday clock, with or without
  a clock push at connect and a dropped first handshake, it restores). A device
  whose settings are all at their factory defaults is now a reset whatever
  its clock shows. The raw device clock, the offset and each reset criterion
  are logged at info level.
- A restore could fail with `restore_mismatch` on `light_and_sound`: light and
  sound values the device cannot show at that moment (all while the soother
  runs, the brightness while the light is off) are no longer compared, and
  changes made while the soother runs are not saved. Each mismatching field is
  logged with its saved and device value.
- Downloading diagnostics failed: the library version is read outside the
  event loop.

## [0.1.0b6] - 2026-09-25

Fixes from the second Home Assistant run on hardware (Raspberry Pi 4).

### Fixed

- A power loss was not restored automatically: a Repair appeared instead. A
  reset seen by a fresh read now stays pending until it is resolved, so a
  reconnect pass that sets the clock and then fails no longer hides it; every
  fresh read (also a profile read or edit) checks for it before any clock
  write. A whole-hour clock offset no longer blocks the reset when Home
  Assistant heard the device within the last hour. The factory-default check
  compares the values recorded after a power loss, including light
  brightness 5, and no longer the routine and Ready-to-Rise on/off flags.
- A setting written right after a profile write (for example **Routines** on)
  could be acknowledged but not applied, silently. A setting the pushed state
  does not confirm is written once more and otherwise fails with an error; a
  command waits up to 15 seconds for a session that is being reopened.
- A routine the device ends by itself (for example an untouched scheduled
  routine after about two hours) fires `routine_expired` instead of
  `routine_cancelled`; `routine_cancelled` means Home Assistant cancelled it.
  Routine status pushes are logged at debug level.

## [0.1.0b5] - 2026-09-25

Fixes from the first Home Assistant run on hardware (Raspberry Pi 4).

### Fixed

- Profile edits, `lumalou.set_routine`, restores and profile reads failed with
  "saved but could not be written" because the fresh Bluetooth session was
  opened about 0.4 seconds after the previous one closed, which the device
  rejects ("BLE connection was lost"). A new connect now waits until the
  previous session is fully closed and at least 2 seconds after that, and a
  link that drops while connecting is retried twice.
- The audio source is shown while the sleep playlist (soother) plays: from
  the pushed state (built-in sounds, the soother's stage) or the source Home
  Assistant started.
- The light shows no palette effect while the soother cycles the colors.

## [0.1.0b4] - 2026-09-25

Based on a hardware check of every Bluetooth command on firmware 0.3.7.

### Upgrade notes

- The saved profile format changed (schema 3). After updating, open
  **Configure → Read the device profile** once and confirm it; controls stay
  locked until then.
- **Automatic restore** is now on by default for new entries. Existing entries
  keep their setting; switch it on under **Configure → Behavior options**.
- The **Refresh** button was removed; the device pushes its state.

### Added

- Clock entities: **Clock format** (12/24-hour), **Clock display** and
  **Clock brightness**.
- The profile now also holds the light and playlist timers, volume and light
  brightness, which a power loss resets. Changes made in Home Assistant are
  kept in it.
- Routines: **Start routine**, **Complete task** (the remote's check-mark
  button), **Previous task** and **Cancel routine** buttons; **Routine** and
  **Current task** sensors; a **Routine** event entity firing
  `task_completed` (with the task), `routine_completed` and
  `routine_cancelled`; **Routines** (automatic start), **Routine music**,
  **Task reward sound**, **Routine reward sound** and **Routine volume**
  configuration entities, kept in the saved profile.
- Actions `lumalou.set_routine` (a day routine for chosen weekdays, written
  to the device and verified) and `lumalou.start_routine` (today's routine
  now, or other tasks just this once; today's saved routine is written back
  when it ends, also after a reconnect or restart).

### Changed

- Confirming a profile editor in **Configure** now writes the change to the
  Lumalou right away and verifies it with a fresh read, instead of only
  saving a pending revision. If the device cannot be reached, the change is
  saved, a message says so, and the Repair offers to write it after the next
  reconnect (a reset restores it automatically).
- One Bluetooth session stays open while the device is reachable. State comes
  from the device's own pushes (also for button presses on the device); a
  command is done when the device acknowledges it, with no reconnect or read
  afterwards. Reconnects wait 1.5 seconds after a disconnect.
- Power loss: a reconnect that finds the device clock restarted at 05:00 on
  Sunday (as after a power loss: more than 10 minutes off, not by whole hours,
  and running no longer than since Home Assistant last heard from the device)
  and the settings different is a reset. A DST change or a long Home
  Assistant downtime only sets the clock; a whole-hour offset counts as a
  reset only when every device setting is at its factory default. Home Assistant sets the clock and
  restores the saved profile automatically (at most two attempts), or raises
  the Repair when automatic restore is off. Settings changed without a reset
  still raise the Repair and are never overwritten automatically.
- Light: "on" switches the light on in the current color at the stored
  brightness; a brightness is written before the color. Effect names describe
  what the device shows.
- The sleep playlist is labelled as the soother (music and light); stopping
  sound leaves its light on. `pink_noise` is labelled "White noise".
- The clock is corrected from the clock the device pushes every minute (DST,
  drift) instead of a daily reconnect.
- A clock offset of more than 10 minutes seen while connected (for example
  a DST change) is corrected at once, not after the hourly limit. A failed
  automatic clock write now fails that reconnect instead of reading the
  device again.
- A session that sends nothing for three minutes is closed and reconnected.
- **Start routine** waits for the device to enter routine mode before making
  the first task current; if it does not, a one-off routine is written back
  and an error is shown. The button is unavailable while a routine runs, and
  a restore is refused while one runs.
- A one-off routine uses the device clock's weekday, and its marker is kept in
  the private profile store instead of the config entry.
- State pushes with an unknown value in one field are no longer dropped.
- The aggregate state and soother commands (`0x01`, `0x03`) and the nap
  commands (`0x4D`, `0x4F`) are blocked. Routine start (`0x7B`) and routine
  control (`0x6B` codes 0–4) are allowed as exact payloads after their
  hardware check.
- Requires `lumalou-gld09` 0.3.0: it ignores the bare frame the device sends
  after "previous task", "restart" and "complete all", which ended the
  session with 0.2.1.
- The **Daily routines** editor picks one task per step by name (each task
  once, up to 11); a day without tasks has no routine. The **Bathroom** task
  is now called **Toilet**.

## [0.1.0b3] - 2026-09-25

### Fixed

- Correcting the device clock after a power cut no longer drops the Bluetooth
  session. The library (`lumalou-gld09` 0.2.1) now accepts the device's write
  acknowledgements for every command length; before, the clock write and
  most profile-restore writes ended the session with "unsupported SSI route or
  invalid FE length/checksum". Ignored and rejected device frames are logged at
  debug level for the `lumalou` logger.
- A failed automatic clock correction is no longer repeated on every
  reconnect attempt: a warning is logged, the reconnect finishes with a second
  read, and automatic clock writes pause for an hour. The **Synchronize clock**
  button still writes at once. Diagnostics show `clock_sync_paused`.
- Turning **Maintenance** on takes effect at once: it cancels a running
  reconnect or clock check instead of waiting behind it.
- Reading the device profile in the options no longer closes the connection
  and immediately opens a new one.

## [0.1.0b2] - 2026-09-25

### Fixed

- Bluetooth setup no longer fails with "cannot connect" when the first
  connection attempt is slow: the connect is no longer cut off after 20
  seconds, so Home Assistant's Bluetooth connector can time out and retry as
  designed. Failed setup probes are logged at debug level, and the first
  failed connection while the device is unavailable is logged once at info
  level with the error.

## [0.1.0b1] - 2026-09-25

First beta for hardware validation. Not yet validated on a real device; see
`docs/hardware-validation.md`.

### Added

- Config-entry setup from Home Assistant Bluetooth discovery, with user
  confirmation and no independent scanner.
- Per-device enrollment: the signed factory key is verified and only a private
  fingerprint is stored; every session is bound to it. Reconfigure accepts a
  new Bluetooth address only for the same signed device.
- Controls unlock only after one complete, strict profile read from the device
  has been confirmed.
- Light (on/off, brightness, palette effects; a plain "on" uses the last
  brightness seen), media player (play/stop, volume, built-in sources), light
  and playlist duration selects, maintenance switch, clock-sync and refresh
  buttons, connectivity binary sensor, firmware sensor and profile sync
  status sensor.
- Private, revisioned profile per entry with the persistent configuration
  (playlist, clock settings, routine settings, weekly schedules, alarms and
  all seven daily routines), read from the device, with editors for every
  block; JSON export/import actions with a revision check. Light brightness
  and color, volume and timers are live state, not profile settings.
- Verified profile restore (`lumalou.restore_profile`, optional
  `expected_revision`): fresh read, clock correction, minimal ordered setter
  writes with activation flags last, and a fresh verification read. A restore
  never writes light or volume.
- Power-loss handling: every reconnect of a verified entry reads the full
  profile, corrects a device clock that is more than 60 seconds off and
  raises a Repair (restore saved profile or keep device settings) when the
  device no longer matches the verified profile. A pending revision the device
  already matches becomes verified. Optional automatic restore, off by
  default, limited to two attempts per event.
- Daily clock check at 03:05 and on Home Assistant time zone changes.
- Push updates while connected; reconnect on advertisement at most every
  30 seconds with bounded backoff; device loss and return logged once.
- Repair for differing device settings; redacted diagnostics; translated
  errors; English and Russian translations; local brand icons.
- Standard entities for Apple Home through HomeKit Bridge; configuration and
  diagnostic entities are excluded from HomeKit by default.
- CI with lint, type checks, tests, hassfest and HACS validation; tag-based
  release workflow.

### Changed

For users of earlier development builds (there are no migrations; remove the
old Lumalou entry and add the device again):

- An entry without a verified device identity fails setup and asks to be
  removed and added again.
- Light brightness and color, volume and the light and playlist timers are no
  longer saved in the profile, compared for power loss or restored; the
  "Light and audio values" editor is gone. A light that is off (brightness 0)
  no longer raises a *settings differ* Repair.
- `lumalou.import_profile` accepts only a complete profile.
- The profile revision, verified revision and last error sensors and the
  profile pending and present binary sensors are removed; their data is in the
  diagnostics download.
- The profile is stored with Home Assistant's `Store` helper; the storage
  recovery Repair and custom backups are gone.
- Setup no longer reads Bluetooth Device Information; only the signed
  identity is read.
- The same signed device at a new address keeps its unlocked controls.
- **Breaking:** light effects, media player sources and duration select
  options are lower-case keys (for example `warm` instead of `WARM`); update
  automations and scripts.
- Removed the Create menu and the JSON import from the options flow: editors
  appear only after a device read, and JSON import remains available as the
  `lumalou.import_profile` action.
- Removed the `refresh_state`, `sync_clock` and `set_maintenance` actions; use
  the Refresh and Synchronize clock buttons and the Maintenance switch.
- Manual setup no longer asks for a second confirmation after the device is
  chosen.
- Removing an entry now deletes its private saved profile; export it first.
- The requirement is the published `lumalou-gld09==0.2.0` release; Home
  Assistant 2026.9.0 or newer.

### Security

- No DFU, OTA, firmware or factory-reset access. Only allowlisted opcodes and
  GATT characteristics are used; pairing-complete and time-prescaler commands
  are denied. Restore uses only allowlisted profile setters.
- Bluetooth addresses, fingerprints, tokens and schedules are kept out of the
  UI, logs and diagnostics.

### Known limitations

- Not yet validated on hardware; see `docs/hardware-validation.md`.
- Power-loss detection is a heuristic over the persistent profile settings;
  automatic restore overwrites every other change of them made on the device.
  Light, volume and timers are not restored after a power loss.
- Palette colors are not available in Apple Home.

<!-- Release section template:
## [X.Y.Z] - YYYY-MM-DD

### Added
### Changed
### Fixed
### Security
### Known limitations

Hardware validation: summary of passed phases (anonymized).
Requires Home Assistant 2026.9.0 or newer and lumalou-gld09==0.2.0.
-->

[Unreleased]: https://github.com/pavlikru/ha-lumalou/compare/v0.1.2...HEAD
[0.1.2]: https://github.com/pavlikru/ha-lumalou/compare/v0.1.1...v0.1.2
[0.1.1]: https://github.com/pavlikru/ha-lumalou/releases/tag/v0.1.1
[0.1.0]: https://github.com/pavlikru/ha-lumalou/releases/tag/v0.1.0
[0.1.0b8]: https://github.com/pavlikru/ha-lumalou/releases/tag/v0.1.0b8
[0.1.0b7]: https://github.com/pavlikru/ha-lumalou/releases/tag/v0.1.0b7
[0.1.0b6]: https://github.com/pavlikru/ha-lumalou/releases/tag/v0.1.0b6
[0.1.0b5]: https://github.com/pavlikru/ha-lumalou/releases/tag/v0.1.0b5
[0.1.0b4]: https://github.com/pavlikru/ha-lumalou/releases/tag/v0.1.0b4
[0.1.0b3]: https://github.com/pavlikru/ha-lumalou/releases/tag/v0.1.0b3
[0.1.0b2]: https://github.com/pavlikru/ha-lumalou/releases/tag/v0.1.0b2
[0.1.0b1]: https://github.com/pavlikru/ha-lumalou/releases/tag/v0.1.0b1
