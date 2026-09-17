# Testing

Automated tests use mocked Bluetooth and must not contact a real Lumalou or a
Home Assistant installation.

```bash
uv sync --locked
uv run ruff check .
uv run ruff format --check .
uv run pytest
uv run pre-commit run --all-files
python scripts/build_hacs_zip.py /tmp/lumalou.zip
```

The archive builder is shared by validation and release workflows. It places
`manifest.json` at the ZIP root, excludes Python caches, sorts entries, and uses
fixed metadata so identical source produces identical archive bytes. Until
hardware acceptance, the tag workflow intentionally creates prereleases only.

The pinned test target is Home Assistant 2026.9.2. A passing mocked suite proves
the Home Assistant adapter behavior only; it does not prove BLE advertising,
payload layouts, setter side effects, or recovery on hardware.

## Hardware tests

Hardware tests remain explicit and opt-in. Before any write, record the label
product code, firmware, Home Assistant version, installation type, Bluetooth
backend, and whether the device advertises connectably after power-on without a
pairing-button press. Use safe daytime light and audio levels.

Do not run destructive power tests against the Raspberry Pi. Restart Home
Assistant and the Pi cleanly; power-cycle only the Lumalou during an agreed test
window. Restore the user's profile after every block.
