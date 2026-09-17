# Contributing

Thanks for helping build Lumalou support for Home Assistant.

## Before starting

- Search existing issues and pull requests.
- Open an issue before a large architectural change.
- Never post device addresses, private keys, personal Home Assistant config, or
  unredacted Bluetooth captures.
- Keep firmware update and OTA behavior out of scope.

## Local setup

```bash
git clone https://github.com/pavlikru/ha-lumalou.git
cd ha-lumalou
uv sync --locked
uv run pre-commit install
```

Run all local checks before submitting changes:

```bash
uv run ruff check .
uv run ruff format --check .
uv run pytest
uv run pre-commit run --all-files
```

## Pull requests

- Keep each pull request focused.
- Add or update tests for behavior changes.
- Update user documentation and translations when behavior changes.
- Explain any hardware testing performed and the Lumalou firmware version.
- Use clear commits; Conventional Commit prefixes are preferred.

By contributing, you agree that your contribution is licensed under the MIT
License.
