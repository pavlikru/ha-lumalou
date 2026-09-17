# Lumalou for Home Assistant

[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Validate](https://github.com/pavlikru/ha-lumalou/actions/workflows/validate.yml/badge.svg)](https://github.com/pavlikru/ha-lumalou/actions/workflows/validate.yml)

Home Assistant custom integration for local Bluetooth LE control of the
Fisher-Price Lumalou Better Bedtime Routine System (`gld09`).

> [!IMPORTANT]
> This repository is an initial development scaffold. The integration is not
> functional or ready to install yet.

The integration will adapt the async Python package from
[`stramanu/lumalou`](https://github.com/stramanu/lumalou) to Home Assistant. All
device communication stays local; no Fisher-Price account or cloud service is
required.

## Planned scope

- UI setup and Bluetooth discovery
- Light color, brightness, and timers
- Audio, playlists, volume, and timers
- Routines and sleep schedules
- Clock and live device state
- Diagnostics, translations, and HACS installation

See [the architecture notes](docs/architecture.md) for boundaries and planned
Home Assistant entities.

## Repository layout

```text
custom_components/lumalou/  Home Assistant integration
docs/                       Architecture and development notes
tests/                      Automated tests
```

## Development

Requirements:

- Python 3.14.2 or newer
- [uv](https://docs.astral.sh/uv/)

```bash
uv sync --locked
uv run pre-commit install
uv run ruff check .
uv run ruff format --check .
uv run pytest
```

See [CONTRIBUTING.md](CONTRIBUTING.md) before opening a pull request.

## Safety

The integration must not access the Lumalou firmware-update service or send OTA
commands. Incorrect writes to that service can make the device unusable.

## Independence and trademarks

This is an independent interoperability project. It is not affiliated with or
endorsed by Mattel or Fisher-Price. “Fisher-Price” and “Lumalou” are trademarks
of their respective owners.

## License

MIT. See [LICENSE](LICENSE).
