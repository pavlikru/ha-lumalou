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
  and all seven daily routines. It is read from the device, can be edited in
  the options flow afterwards, and can be exported or imported as JSON through
  actions.
- **Profile restore**: writes only the settings that differ from the saved
  profile and proves the result with a fresh read. After a power loss or any
  other change on the device, a Repair lets you restore the saved profile or
  keep the device settings; **automatic restore** is an option, off by
  default.
- The device clock is set from Home Assistant whenever it drifts by more than
  60 seconds.
- **Maintenance** switch that releases the Bluetooth connection so the official
  app or another client can connect.
- Apple Home through Home Assistant's HomeKit Bridge (see below).
- Redacted diagnostics, Repairs, English and Russian translations.

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
- Home Assistant never resends one-off commands (play, light off) after a
  reconnect. A restore writes only allowlisted profile setters, only after a
  fresh complete read, and writes the Ready-to-Rise and routine on/off flags
  last, after the schedules they activate.

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
   services**. Select **Add** and confirm that this is your Lumalou, or use
   **Add integration → Lumalou** and choose it from the discovered devices
   (choosing it is the confirmation).
2. Home Assistant connects once, checks the standard Device Information for a
   conflicting model and verifies the signed device identity. Nothing on the
   device is changed.
3. The profile options open next. Choose **Read the device profile**, check
   the summary and confirm. This first complete read becomes the saved
   profile, unlocks the controls and makes the profile editors available.

If the Lumalou later appears with a different Bluetooth address, use
**Reconfigure** on the entry. It accepts only the same signed device.

### Options

**Settings → Devices & services → Lumalou → Configure**:

| Option | Meaning |
| --- | --- |
| Read the device profile | Read the complete profile from the Lumalou and, after confirmation, save it as the verified profile. Until this has been done once, only this and Behavior options are offered. |
| Light and audio values, Playlist, Clock settings, Routine settings, Weekly schedule, Daily routines | Editors for the saved profile, prefilled from it. Saving creates a pending revision in Home Assistant only; `lumalou.restore_profile` writes it to the device. |
| Behavior options → Restore the saved profile automatically | Off by default. See [Profile restore and power loss](#profile-restore-and-power-loss). |

## Entities

Entity IDs below assume the device is named "Lumalou" and Home Assistant
runs in English; check the actual IDs in the entity settings.

| Entity | Type | Notes |
| --- | --- | --- |
| `light.lumalou_light` | Light | On/off, brightness, palette colors as effects (`warm`, `red`, `yellow`, `orange`, `green`, `blue`, `purple`, `night_light`, `cool`, `rainbow`; translated in the UI). |
| `media_player.lumalou_audio` | Media player (speaker) | On starts the sleep playlist; off stops sound; volume; source selects a built-in sound (`sleep_playlist`, `custom_playlist`, `pink_noise`, `ocean`, `rain`, `brown_noise`, `nature`, `highway`). |
| Light duration, Playlist duration | Select (configuration) | Device timers (options such as `min_15`, `continuous`). |
| Maintenance | Switch (configuration) | Releases Bluetooth and pauses all device I/O from Home Assistant. |
| Synchronize clock | Button (configuration) | Sets the device clock from Home Assistant's time zone. |
| Refresh | Button (diagnostic) | Reads current state. |
| Connection | Binary sensor (connectivity, diagnostic) | On while a Bluetooth session is live. |
| Firmware, Profile revision, Profile verified revision, Profile sync status, Profile last error | Sensor (diagnostic) | Status only. |
| Profile pending, Profile present | Binary sensor (diagnostic) | Status only. |

### How data is updated

There is no polling. While connected, the Lumalou pushes state changes. When
Home Assistant Bluetooth sees the device advertising and no session is live,
the integration reconnects at most once every 30 seconds, backing off up to
15 minutes after failures. Before the first confirmed profile read it only
reads the state. Afterwards every reconnect reads the complete profile in one
session, sets the device clock if it is more than 60 seconds off, and compares
the profile with the saved one. When Home Assistant reports the device gone,
entities become unavailable; loss and return are logged once at info level.

### Profile restore and power loss

The Lumalou has no documented power-loss marker. The integration therefore
treats a reconnect whose fresh read no longer matches the saved **verified**
profile as "settings changed on the device" (for example reset by a power
loss). Live changes from Home Assistant keep the profile verified when the
device reports the new value; edits made in the options flow stay pending
until they are restored.

- **Automatic restore off** (default): a Repair, *Lumalou settings differ from
  the saved profile*, appears. Choose **Restore saved profile** or **Keep
  device settings** (the current device settings become the new saved
  profile).
- **Automatic restore on**: Home Assistant writes the saved profile back right
  away. Every other change made on the device, including in the Fisher-Price
  app, is overwritten too. After two failed attempts for the same event it
  stops and raises the Repair instead.

A restore (automatic, from the Repair, or `lumalou.restore_profile`) opens a
fresh session, reads everything, corrects the clock, writes only the blocks
that differ in a fixed order (display, timers, audio, routine sound, weekly
schedules and routines, color and brightness, then the Ready-to-Rise and
routine on/off flags), and then reads everything again in a new session. It
counts as verified only if every block matches. Nothing is retried silently,
and nothing is restored while **Maintenance** is on.

## Actions

All actions take `config_entry_id`. Refresh, clock sync and maintenance are
buttons and a switch on the device (see [Entities](#entities)).

| Action | Description |
| --- | --- |
| `lumalou.export_profile` | Returns `{current_revision, profile}`. Keep it private. |
| `lumalou.import_profile` | Saves `profile` if `expected_revision` matches the current revision. Does not write to the device. |
| `lumalou.restore_profile` | Writes the saved profile to the device and verifies it (see above). Optional `expected_revision` (default: the current revision) fails the action if the profile changed meanwhile. Can return `{revision, verified, applied_steps, clock_synced}`. |

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
          effect: warm
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
| Controls fail with "Read and confirm the device profile…" | Open **Configure → Read the device profile** and confirm the result. |
| Repairs: "Lumalou settings differ from the saved profile" | Choose **Restore saved profile** or **Keep device settings**. If a restore fails, bring the adapter closer and try again; **Profile last error** shows which step failed. |
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

To remove: export the profile if you want to keep it, then delete the Lumalou
entry under **Settings → Devices & services**, remove the integration in HACS
(or delete `custom_components/lumalou`), and restart. Deleting the entry also
deletes its private saved profile. To reuse an export, import it into the new
entry with `lumalou.import_profile` and write it with
`lumalou.restore_profile`.

## Limitations

- Only the `GLD09` Lumalou is supported. Setup verifies the signed identity of
  the individual device, not the retail model.
- Palette colors cannot be exported to Apple Home.
- The official app and Home Assistant cannot be connected at the same time;
  use **Maintenance** when you need the app.
- Power-loss detection is a heuristic (a verified profile that no longer
  matches the device). Restore and the write behavior of the individual
  settings are not yet hardware-validated.
- Routine music and routine volume are read back as 4-bit values, so they
  are limited to 0–15.

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
