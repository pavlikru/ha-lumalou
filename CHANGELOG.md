# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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

[Unreleased]: https://github.com/pavlikru/ha-lumalou/compare/v0.1.0b1...HEAD
[0.1.0b1]: https://github.com/pavlikru/ha-lumalou/releases/tag/v0.1.0b1
