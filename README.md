# Lumalou for Home Assistant

[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Validate](https://github.com/pavlikru/ha-lumalou/actions/workflows/validate.yml/badge.svg)](https://github.com/pavlikru/ha-lumalou/actions/workflows/validate.yml)

Local Bluetooth LE control of the Fisher-Price Lumalou Better Bedtime Routine
System (`GLD09`) from Home Assistant. No cloud account and no vendor app are
needed. The Bluetooth protocol lives in the
[`lumalou-gld09`](https://github.com/pavlikru/lumalou) Python package, a fork
of [`stramanu/lumalou`](https://github.com/stramanu/lumalou).

> [!WARNING]
> **Development preview.** Automated tests use mocked Bluetooth. The
> integration has not yet passed the [hardware validation
> checklist](docs/hardware-validation.md), and there is no stable release. Do
> not rely on it for a child's bedtime routine yet.

## Features

- Discovery through Home Assistant Bluetooth (local adapter or proxy); no
  separate scanner.
- Night light: on/off, brightness (device levels 1–9) and the device's fixed
  color palette as light effects.
- Sound: play/stop, volume (0–9) and built-in sound source.
- Light and playlist timers, clock synchronization, manual refresh.
- A private, revisioned **profile** per device: light and sound defaults,
  playlist, clock display, routine settings, wake and bedtime schedules, alarms
  and all seven daily routines. Edit it offline in the options flow, read it
  from the device, and export or import it as JSON.
- **Maintenance** switch that releases the Bluetooth connection so the official
  app or another client can connect.
- Apple Home through Home Assistant's HomeKit Bridge (see below).
- Redacted diagnostics, Repairs for damaged profile storage, English and
  Russian translations.

Planned before the first release: restoring the saved profile automatically
after the Lumalou loses power. The option exists but cannot be enabled until
it passes hardware validation.

## Safety and privacy

- The integration never touches the Nordic DFU service and has no OTA,
  firmware-update or factory-reset command. Unknown opcodes, the time-prescaler
  and pairing-complete commands, and arbitrary GATT writes are blocked before
  any Bluetooth I/O.
- Controls stay blocked until you confirm the device during setup **and** a
  complete profile has been read from it once.
- Each session is bound to the device you confirmed. Home Assistant verifies
  the signed factory key of the device and stores only a private fingerprint
  of that key in the config entry. The fingerprint is never shown in the UI,
  logs or diagnostics. Diagnostics also leave out the Bluetooth address and the
  profile. (Like every Bluetooth device, the address appears on the Home
  Assistant device page.)
- Home Assistant never resends one-off commands (play, light off, clock sync)
  after a reconnect.

## Requirements

- Home Assistant 2026.9.2 or newer.
- A Bluetooth adapter or ESPHome Bluetooth proxy that can make active
  (connectable) connections, in range of the Lumalou. Proxies are expected to
  work through Home Assistant's Bluetooth stack but have not been tested yet.
- The Lumalou accepts one Bluetooth connection at a time. Close the Fisher-Price
  app or any Web Bluetooth client before setup.

## Installation

### HACS (custom repository)

1. In HACS, open **⋮ → Custom repositories**, add
   `https://github.com/pavlikru/ha-lumalou` with type **Integration**.
2. Open **Lumalou** in HACS and select **Download**. To test development
   builds, choose the `feat/lumalou-integration` branch as the version.
3. Restart Home Assistant.

### Manual

Copy `custom_components/lumalou` into your Home Assistant `config/custom_components/`
directory and restart Home Assistant.

## Setup

1. Home Assistant shows a discovered **Lumalou** under **Settings → Devices &
   services**. Select **Add**, or use **Add integration → Lumalou** to pick from
   the discovered devices.
2. Confirm that this is your Lumalou. Home Assistant connects once, checks the
   standard Device Information for a conflicting model and verifies the signed
   device identity. Nothing on the device is changed.
3. The profile options open next. Choose **Read the device profile** to read
   the complete profile, check the summary and confirm. This first complete
   read unlocks the controls. You can also **Create an offline profile** or
   **Import an exported profile**; controls stay locked until one successful
   read from the device.

If the Lumalou later appears with a different Bluetooth address, use
**Reconfigure** on the entry. It accepts only the same signed device.

### Options

**Settings → Devices & services → Lumalou → Configure**:

| Option | Meaning |
| --- | --- |
| Behavior options → Enable automatic restore | Reserved for power-loss restore; cannot be enabled yet. |
| Light and audio values, Playlist, Clock settings, Routine settings, Weekly schedule, Daily routines | Offline editors for the saved profile. Saving changes only Home Assistant's copy; nothing is sent to the device. |
| Import an exported profile | Paste a JSON export. A preview is shown before saving. |
| Read the device profile | Read the complete profile from the Lumalou and, after confirmation, save it. |

## Entities

Entity IDs below assume the device is named "Lumalou" and Home Assistant
runs in English; check the actual IDs in the entity settings.

| Entity | Type | Notes |
| --- | --- | --- |
| `light.lumalou_light` | Light | On/off, brightness, palette colors as effects (`WARM`, `RED`, `YELLOW`, `ORANGE`, `GREEN`, `BLUE`, `PURPLE`, `NIGHT_LIGHT`, `COOL`, `RAINBOW`). |
| `media_player.lumalou_audio` | Media player (speaker) | On starts the sleep playlist; off stops sound; volume; source selects a built-in sound. |
| Light duration, Playlist duration | Select (configuration) | Device timers. |
| Maintenance | Switch (configuration) | Releases Bluetooth and pauses all device I/O from Home Assistant. |
| Synchronize clock | Button (configuration) | Sets the device clock from Home Assistant's time zone. |
| Refresh | Button (diagnostic) | Reads current state. |
| Connection, Firmware, Profile revision, Profile verified revision, Profile sync status, Profile last error | Sensor (diagnostic) | Status only. |
| Profile pending, Profile present | Binary sensor (diagnostic) | Status only. |

### How data is updated

There is no polling. While connected, the Lumalou pushes state changes. When
Home Assistant Bluetooth sees the device advertising, the integration
reconnects and reads the state (read-only), backing off from 30 seconds up to
15 minutes after failures. When Home Assistant reports the device gone,
entities become unavailable.

## Actions

All actions take `config_entry_id`.

| Action | Description |
| --- | --- |
| `lumalou.refresh_state` | Read the current state. |
| `lumalou.sync_clock` | Set the device clock from Home Assistant's time. |
| `lumalou.set_maintenance` | `enabled: true` releases Bluetooth; `false` resumes. |
| `lumalou.export_profile` | Returns `{current_revision, profile}`. Keep it private. |
| `lumalou.import_profile` | Saves `profile` if `expected_revision` matches the current revision. Does not write to the device. |
| `lumalou.restore_profile` | Not available yet; returns an error. |

Example: a quiet night light at bedtime.

```yaml
automation:
  - alias: Lumalou bedtime light
    triggers:
      - trigger: time
        at: "19:30:00"
    actions:
      - action: light.turn_on
        target:
          entity_id: light.lumalou_light
        data:
          brightness_pct: 20
          effect: WARM
```

## Apple Home (HomeKit Bridge)

Use Home Assistant's built-in [HomeKit Bridge][homekit]. What Apple Home shows:

- **Light**: a lightbulb with on/off and brightness. The palette colors are
  light effects in Home Assistant and are **not** exported.
- **Audio**: a media player with device class `speaker` is exported as a
  switch that turns sound on/off. Volume and source stay in Home Assistant.
  Only `tv` (and `projector`) or `receiver` media players become Television
  accessories; the integration does not pretend the Lumalou is one.
- Configuration and diagnostic entities (maintenance, timers, buttons,
  status sensors) are not exported by default, even in include mode, unless
  you list them explicitly. Keep them out.

A bridge (the default mode) is fine; a separate accessory-mode instance is only
required for TVs, cameras, locks and activity remotes.

**Existing bridge (UI).** Open **Settings → Devices & services → HomeKit
Bridge → Configure**. Keep the current mode. In include mode add
`light.lumalou_light` (and optionally `media_player.lumalou_audio`) to the
selected entities; in exclude mode make sure they are not excluded. Finish the
dialog; the bridge reloads with the new accessories. Do not reset or re-pair
the whole bridge, which would remove rooms, names and automations of every
other accessory in Apple Home. To refresh a single accessory, use the
`homekit.reset_accessory` action for that entity only.

**YAML example** of a dedicated bridge that exports only the Lumalou:

```yaml
homekit:
  - name: Lumalou Bridge
    port: 21065 # must differ from other HomeKit Bridge instances
    filter:
      include_entities:
        - light.lumalou_light
        - media_player.lumalou_audio # optional
    entity_config:
      light.lumalou_light:
        name: Night Light
      media_player.lumalou_audio:
        name: Lumalou Sound
        feature_list:
          - feature: on_off
```

A new YAML bridge must be paired separately in Apple Home and cannot be edited
in the UI.

## Troubleshooting

| Symptom | What to do |
| --- | --- |
| Lumalou is not discovered | Close other apps connected to it, move the adapter or proxy closer, and check that the adapter supports active connections. |
| "Could not connect for the read-only identity probe" | Another client is connected, or the device is out of range. Retry. |
| "The installed Lumalou library cannot verify…" | The installed integration version bundles an incompatible library. Update the integration. |
| Controls fail with "Read and verify the complete Lumalou profile" | Open **Configure → Read the device profile** and confirm the result. |
| Entities unavailable | The device is out of range or unpowered, or **Maintenance** is on. |
| Device moved to a new Bluetooth address | Use **Reconfigure** on the entry. |
| Repairs: "Lumalou profile needs recovery" | Follow the Repair and import your last export. Do not edit `.storage` by hand. |

For debug logs add:

```yaml
logger:
  logs:
    custom_components.lumalou: debug
    lumalou: debug
```

When reporting a bug, attach the diagnostics download (already redacted). Do not
post Bluetooth addresses, exported profiles or schedules.

## Updating and removal

Make a Home Assistant backup and export the profile (`lumalou.export_profile`)
before updating. HACS updates the integration; restart Home Assistant
afterwards. To roll back, redownload the previous version in HACS and restart.

To remove: delete the Lumalou entry under **Settings → Devices & services**,
remove the integration in HACS (or delete `custom_components/lumalou`), and
restart. The saved profile stays in Home Assistant's private storage
(`.storage/lumalou.<entry_id>.profile`) and is not reused by a new entry;
import your export into the new entry instead.

## Limitations

- Only the `GLD09` Lumalou is supported. Setup verifies the signed identity of
  the individual device, not the retail model.
- Palette colors cannot be exported to Apple Home.
- The official app and Home Assistant cannot be connected at the same time;
  use **Maintenance** when you need the app.
- Power-loss restore and write behavior of the individual settings are not yet
  hardware-validated.

## Documentation

- [Hardware validation checklist](docs/hardware-validation.md)
- [Architecture](docs/architecture.md)
- [Persistent profile schema](docs/profile-schema.md)
- [Protocol audit](docs/protocol-audit.md)
- [Установка на русском](docs/installation.ru.md)
- [Changelog](CHANGELOG.md)

## Development

Requires Python 3.14.2+ and [uv](https://docs.astral.sh/uv/). See
[CONTRIBUTING.md](CONTRIBUTING.md).

## Credits

- [`stramanu/lumalou`](https://github.com/stramanu/lumalou) by Emanuele
  Strazzullo: the original reverse engineering of the Lumalou protocol and its
  Python library (MIT).
- [`pavlikru/lumalou`](https://github.com/pavlikru/lumalou), published as
  `lumalou-gld09`: fork adding strict fresh reads, schedule and routine codecs
  and signed device identity (MIT).

This is an independent interoperability project, not affiliated with or
endorsed by Mattel or Fisher-Price. "Fisher-Price" and "Lumalou" are trademarks
of their respective owners.

## License

MIT. See [LICENSE](LICENSE).

[homekit]: https://www.home-assistant.io/integrations/homekit/
