# AGENTS.md

## Project

`ha-lumalou` is an open-source Home Assistant custom integration for the
Fisher-Price Lumalou (`gld09`). It uses the async Python library published by
[`stramanu/lumalou`](https://github.com/stramanu/lumalou).

The repository is currently a scaffold. Do not claim that the integration is
installable or functional until setup, discovery, entities, and hardware tests
exist.

## Architecture rules

- Keep the Home Assistant domain `lumalou`.
- Put integration code in `custom_components/lumalou/` and tests in `tests/`.
- Use Home Assistant's Bluetooth APIs for discovery and connection handling.
  Do not start independent global Bleak scans.
- Keep protocol and cryptography code in the upstream `lumalou` package. This
  repository should contain only Home Assistant adaptation code.
- Use config entries and UI setup. Store runtime state in
  `ConfigEntry.runtime_data`.
- Keep all I/O async. Never block the Home Assistant event loop.
- Model standard functions with standard entity platforms before adding custom
  actions.
- Never access the DFU service (`00001530-...`) or add OTA/firmware commands.
- Add tests with each behavior change. Mock BLE in automated tests; hardware
  tests must remain explicit and opt-in.
- Keep user-facing text translatable through `strings.json` and
  `translations/`.
- Follow the current Home Assistant developer documentation and target at least
  the Bronze Integration Quality Scale.

## Repository rules

- Use Conventional Commits where practical: `feat:`, `fix:`, `docs:`,
  `test:`, `chore:`.
- Keep `manifest.json` dependencies pinned to exact released versions.
- Update the manifest version only as part of a release.
- Do not commit secrets, Bluetooth captures, Home Assistant config, device
  addresses, or private keys.
- Preserve the upstream project's attribution and MIT license when copying any
  upstream code.

## Checks

Run before committing:

```bash
uv sync --locked
uv run ruff check .
uv run ruff format --check .
uv run pytest
uv run pre-commit run --all-files
```
