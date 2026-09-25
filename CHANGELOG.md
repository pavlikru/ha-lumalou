# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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

- One Bluetooth session stays open while the device is reachable. State comes
  from the device's own pushes (also for button presses on the device); a
  command is done when the device acknowledges it, with no reconnect or read
  afterwards. Reconnects wait 1.5 seconds after a disconnect.
- Power loss: a reconnect that finds the device clock more than 10 minutes off
  and the settings different is a reset. Home Assistant sets the clock and
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
- The aggregate state and soother commands (`0x01`, `0x03`) and the nap
  commands (`0x4D`, `0x4F`) are blocked. Routine start (`0x7B`) and routine
  control (`0x6B` codes 0–4) are allowed as exact payloads after their
  hardware check.
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

[Unreleased]: https://github.com/pavlikru/ha-lumalou/compare/v0.1.0b4...HEAD
[0.1.0b4]: https://github.com/pavlikru/ha-lumalou/releases/tag/v0.1.0b4
[0.1.0b3]: https://github.com/pavlikru/ha-lumalou/releases/tag/v0.1.0b3
[0.1.0b2]: https://github.com/pavlikru/ha-lumalou/releases/tag/v0.1.0b2
[0.1.0b1]: https://github.com/pavlikru/ha-lumalou/releases/tag/v0.1.0b1
