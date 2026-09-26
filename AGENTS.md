# AGENTS.md

## Project

`ha-lumalou` is an open-source Home Assistant custom integration for the
Fisher-Price Lumalou (`GLD09`). Status: stable 0.1.x, installable through HACS,
validated on real hardware (firmware 0.3.7, Raspberry Pi 4 onboard adapter; see
[docs/hardware-validation.md](docs/hardware-validation.md)).

The Bluetooth protocol and cryptography live in the
[`lumalou-gld09`](https://github.com/pavlikru/lumalou) package (import name
`lumalou`), a fork of [`stramanu/lumalou`](https://github.com/stramanu/lumalou).
This repository contains only Home Assistant adaptation code. If upstream ships
equivalent APIs, switching back means changing the manifest requirement and the
lock file (see [docs/architecture.md](docs/architecture.md#library-fork)).

## Architecture rules

- Keep the Home Assistant domain `lumalou`. Integration code goes in
  `custom_components/lumalou/`, tests in `tests/`.
- Use Home Assistant's Bluetooth APIs for discovery and connections
  (`establish_connection`). Never start independent global Bleak scans.
- Keep protocol and cryptography changes in the library, not here.
- Use config entries and UI setup. Store runtime state in
  `ConfigEntry.runtime_data`.
- Keep all I/O async. Never block the Home Assistant event loop.
- Model functions with standard entity platforms before adding custom actions.
- Send only allowlisted opcodes and characteristics (`transport.py`, see
  [Command policy](docs/architecture.md#command-policy)). Never access the DFU
  service (`00001530-...`) or add OTA/firmware commands.
- Keep user-facing text translatable through `strings.json` and
  `translations/`.
- Follow the current Home Assistant developer documentation; target at least
  the Bronze Integration Quality Scale.

## Hardware facts to respect

Details and evidence are in
[docs/hardware-validation.md](docs/hardware-validation.md); do not duplicate
them here or contradict them in code.

- The device accepts a single BLE central; another client (vendor app, Web
  Bluetooth tab) blocks Home Assistant.
- Power loss resets the device to factory settings; the integration detects
  this and auto-restores the saved profile.
- The device pushes state (GLOBAL_STATE, CURRENT_DATE, ROUTINE_TASK_STATUS);
  do not add polling.
- Routine start is ignored unless routine mode is on; the integration refuses
  it instead.
- During a routine the device ignores light and audio commands; the
  integration refuses them instead.

## Hardware work

- Validate device behaviour with the library probe (`tools/hw_probe` in the
  fork) before changing Home Assistant code that depends on it.
- Automated tests mock BLE and never contact a device or a Home Assistant
  installation. Hardware tests are manual, explicit and opt-in.
- Add or update tests with each behaviour change.

## Repository rules

- Conventional Commits: `feat:`, `fix:`, `docs:`, `test:`, `chore:`.
- Pin `manifest.json` requirements to exact released versions. Change the
  manifest `version` only in a release.
- Never commit secrets, private keys, device addresses, Bluetooth captures,
  Home Assistant config or private working notes.
- Preserve the upstream project's attribution and MIT license when copying
  upstream code.

## Releases

1. PR to `main` with the `CHANGELOG.md` section `## [X.Y.Z] - YYYY-MM-DD` and
   the manifest `version` set to `X.Y.Z`.
2. Merge once CI is green.
3. Tag the merge commit on `main` as `vX.Y.Z` and push the tag.
   `release.yml` checks that the tag is on `main`, matches the manifest version
   and has a CHANGELOG section, reruns validation and publishes the GitHub
   release.

Library releases are separate: push a `v*` tag on the fork to publish
`lumalou-gld09` to PyPI, then bump the pinned requirement and `uv.lock` here.

## Checks

Run before committing (CI runs the same, plus hassfest and HACS validation):

```bash
uv sync --locked
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv run pytest
uv run pre-commit run --all-files
```
