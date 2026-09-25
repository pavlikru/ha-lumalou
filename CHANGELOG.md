# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

Planned as the first release, `0.1.0`, preceded by `0.1.0b1` pre-releases for
hardware validation. When tagging, move these entries under
`## [0.1.0] - YYYY-MM-DD`; the release workflow uses that section as release
notes.

### Added

- Config-entry setup from Home Assistant Bluetooth discovery, with user
  confirmation and no independent scanner.
- Per-device enrollment: the signed factory key is verified and only a private
  fingerprint is stored; every session is bound to it. Reconfigure accepts a
  new Bluetooth address only for the same signed device.
- Controls unlock only after one complete, strict profile read from the device
  has been confirmed.
- Light (on/off, brightness, palette effects), media player (play/stop, volume,
  built-in sources), light and playlist duration selects, maintenance switch,
  clock-sync and refresh buttons, connectivity binary sensor and diagnostic
  sensors.
- Private, revisioned profile per entry, read from the device, with editors
  for light and audio values, playlist, clock settings, routine settings,
  weekly schedules, alarms and all seven daily routines; JSON export/import
  actions with a revision check.
- Verified profile restore (`lumalou.restore_profile`, optional
  `expected_revision`): fresh read, clock correction, minimal ordered setter
  writes with activation flags last, and a fresh verification read.
- Power-loss handling: every reconnect of a verified entry reads the full
  profile, corrects a device clock that is more than 60 seconds off and
  raises a Repair (restore saved profile or keep device settings) when the
  device no longer matches the verified profile. Optional automatic restore,
  off by default, limited to two attempts per event.
- Push updates while connected; reconnect on advertisement at most every
  30 seconds with bounded backoff; device loss and return logged once.
- Repairs for unreadable profile storage and for differing device settings;
  redacted diagnostics; translated errors; English and Russian translations;
  local brand icons.
- Standard entities for Apple Home through HomeKit Bridge; configuration and
  diagnostic entities are excluded from HomeKit by default.
- CI with lint, type checks, tests, hassfest and HACS validation; tag-based
  release workflow.

### Changed

For users of earlier development builds:

- Entity and device registry IDs now derive from the verified device
  fingerprint instead of the Bluetooth address (config entry 1.2). Existing
  entities keep their history; entries created before signed enrollment must
  be reconfigured once, as a Repair explains.
- **Breaking:** light effects, media player sources and duration select
  options are lower-case keys (for example `warm` instead of `WARM`); update
  automations and scripts.
- The text Connection sensor is replaced by a connectivity binary sensor; the
  old sensor is removed from the entity registry.
- Removed the Create menu and the JSON import from the options flow: editors
  appear only after a device read, and JSON import remains available as the
  `lumalou.import_profile` action.
- Removed the `refresh_state`, `sync_clock` and `set_maintenance` actions; use
  the Refresh and Synchronize clock buttons and the Maintenance switch.
- Manual setup no longer asks for a second confirmation after the device is
  chosen.
- Removing an entry now deletes its private saved profile; export it first.
- The requirement is the published `lumalou-gld09==0.2.0` release.

### Security

- No DFU, OTA, firmware or factory-reset access. Only allowlisted opcodes and
  GATT characteristics are used; pairing-complete and time-prescaler commands
  are denied. Restore uses only allowlisted profile setters.
- Bluetooth addresses, fingerprints, tokens and schedules are kept out of the
  UI, logs and diagnostics.

### Known limitations

- Not yet validated on hardware; see `docs/hardware-validation.md`.
- Power-loss detection is a heuristic; automatic restore overwrites every
  other change made on the device.
- Palette colors are not available in Apple Home.

<!-- Release section template:
## [X.Y.Z] - YYYY-MM-DD

### Added
### Changed
### Fixed
### Security
### Known limitations

Hardware validation: summary of passed phases (anonymized).
Requires Home Assistant 2026.9.2 or newer and lumalou-gld09==0.2.0.
-->

[Unreleased]: https://github.com/pavlikru/ha-lumalou/commits/main
