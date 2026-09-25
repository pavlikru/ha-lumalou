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
- Controls unlock only after one complete, strict profile read from the device.
- Light (on/off, brightness, palette effects), media player (play/stop, volume,
  built-in sources), light and playlist duration selects, maintenance switch,
  clock-sync and refresh buttons, diagnostic sensors.
- Private, revisioned profile per entry with offline editors for light and
  audio values, playlist, clock settings, routine settings, weekly schedules,
  alarms and all seven daily routines; JSON export/import with preview and
  revision check.
- Actions: `refresh_state`, `sync_clock`, `set_maintenance`, `export_profile`,
  `import_profile`.
- Push updates while connected; read-only reconnect on advertisement with
  bounded backoff.
- Repairs fix flow for unreadable profile storage; redacted diagnostics;
  English and Russian translations; local brand icons.
- Standard entities for Apple Home through HomeKit Bridge; configuration and
  diagnostic entities are excluded from HomeKit by default.
- CI with lint, type checks, tests, hassfest and HACS validation; tag-based
  release workflow.

### Security

- No DFU, OTA, firmware or factory-reset access. Only allowlisted opcodes and
  GATT characteristics are used; pairing-complete and time-prescaler commands
  are denied.
- Bluetooth addresses, fingerprints, tokens and schedules are kept out of the
  UI, logs and diagnostics.

### Known limitations

- Automatic restore after power loss cannot be enabled yet; `restore_profile`
  returns an error.
- Not yet validated on hardware; see `docs/hardware-validation.md`.
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
