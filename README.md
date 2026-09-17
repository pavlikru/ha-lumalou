# Lumalou for Home Assistant

[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Validate](https://github.com/pavlikru/ha-lumalou/actions/workflows/validate.yml/badge.svg)](https://github.com/pavlikru/ha-lumalou/actions/workflows/validate.yml)

Development implementation of local Bluetooth LE control for the Fisher-Price
Lumalou Better Bedtime Routine System (`gld09`) from Home Assistant.
Communication uses the independent
[`stramanu/lumalou`](https://github.com/stramanu/lumalou) reverse-engineering
library and needs no cloud account.

> [!WARNING]
> This is a development preview, not a stable release. Automated tests use BLE
> mocks; target-device acceptance has not run. Full schedule and seven-day
> routine backup/restore are blocked because `lumalou==0.1.0` exposes opcodes
> but no payload codecs or complete fresh readback API.

## Implemented development scope

- config-entry setup from Home Assistant Bluetooth discovery;
- one shared, serialized BLE coordinator with no independent scanner;
- HA-managed presence callbacks and rate-limited read-only reconnect;
- read-only onboarding and offline config-entry startup;
- light, fixed palette, audio source, volume, and light-duration entities;
- persistent revisioned desired-profile storage for supported fields;
- maintenance mode, clock sync, manual refresh, profile import/export actions;
- diagnostics allowlist, English/Russian translations, and HACS metadata;
- standard `light` and `media_player` entities usable by HomeKit Bridge.

The component deliberately does not claim complete restoration or automatically
write a profile on discovery. A write completion is not treated as verification,
and a cached upstream state cannot confirm a command.

## Apple Home

Use Home Assistant's built-in [HomeKit Bridge][homekit] and include only the
Lumalou light and audio entities in its filter. The light appears as a
brightness-capable light. HomeKit maps a generic `media_player` to switches, so
Apple Home gets audio on/off rather than the HA source/volume UI. The integration
does not mislabel the toy as a TV or receiver to work around that limitation.

## Installation

The repository is packaged in the HACS custom-integration shape, but clean HACS
installation and target hardware operation are not yet accepted. For an agreed
development test, add it as a custom Integration repository, restart Home
Assistant if requested, then add Lumalou from **Settings → Devices & services**.

Do not install this preview on a working nursery device without a backup and an
agreed hardware-test window. See the [Russian installation and Apple Home
guide](docs/installation.ru.md).

## Evidence and limitations

- [Protocol audit](docs/protocol-audit.md) pins the upstream commits and records
  missing APIs, freshness defects, and prohibited operations.
- [Hardware validation ledger](docs/hardware-validation.md) lists every test that
  remains unverified.
- [Implementation status](docs/implementation-status.md) maps the complete
  specification to current evidence and remaining release gates.
- [Persistent profile schema](docs/profile-schema.md) defines the strict logical
  model, partial-profile semantics and migration boundary.
- [Architecture](docs/architecture.md) defines the Home Assistant/upstream
  boundary.
- [Testing](docs/testing.md) explains mocked and hardware checks.

The observed setting loss after power removal applies to the user's unit only;
it is not yet established for every hardware revision. Only `gld09` is in scope.

## Development

Requires Python 3.14.2+ and [uv](https://docs.astral.sh/uv/).

```bash
uv sync --locked
uv run ruff check .
uv run ruff format --check .
uv run pytest
uv run pre-commit run --all-files
```

See [CONTRIBUTING.md](CONTRIBUTING.md) before opening a pull request.

## Safety

The integration must never access the Nordic DFU service, send OTA, factory
reset, time-prescaler, pairing-complete, arbitrary opcode, or arbitrary GATT
writes. Raw decrypted payloads, BLE/session keys, addresses, family schedules,
and exported profiles do not belong in public diagnostics or issues.

## Independence and trademarks

Independent interoperability project. Not affiliated with or endorsed by
Mattel or Fisher-Price. “Fisher-Price” and “Lumalou” are trademarks of their
respective owners.

## License

MIT. See [LICENSE](LICENSE).

[homekit]: https://www.home-assistant.io/integrations/homekit/
