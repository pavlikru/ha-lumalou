# Lumalou for Home Assistant

[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Validate](https://github.com/pavlikru/ha-lumalou/actions/workflows/validate.yml/badge.svg)](https://github.com/pavlikru/ha-lumalou/actions/workflows/validate.yml)

Local Bluetooth LE control of the Fisher-Price Lumalou Better Bedtime Routine
System (`GLD09`) from Home Assistant. No cloud account and no vendor app are
needed. The Bluetooth protocol lives in the
[`lumalou-gld09`](https://github.com/pavlikru/lumalou) Python package, a fork
of [`stramanu/lumalou`](https://github.com/stramanu/lumalou).

> [!WARNING]
> **Beta.** Every Bluetooth command the integration uses was checked on a real
> Lumalou (firmware 0.3.7) with a library-level probe. The Home Assistant-level
> [acceptance checklist](docs/hardware-validation.md) is still open, and there
> is no stable release. Do not rely on it for a child's bedtime routine yet.

## Features

- Discovery through Home Assistant Bluetooth (local adapter or proxy); no
  separate scanner.
- Night light: on/off, brightness (device levels 1–9) and the device's fixed
  color palette as light effects. The device keeps brightness and color while
  the light is off, so a plain "on" (for example from Apple Home) switches it
  on in the last color at the last brightness.
- Sound: play/stop, volume (0–9) and built-in sound source. The sleep playlist
  is the device's soother (music plus a color-cycling light).
- Clock: 12/24-hour format, display on/off and brightness as entities; the
  device clock is kept on Home Assistant time.
- Light and playlist timers.
- **Routines**: set each day's routine (start time and tasks such as "brush
  teeth"), switch the automatic start on or off, start a routine from Home
  Assistant (today's or other tasks just this once), complete tasks like the
  remote's check-mark button, and react in automations when a task or the
  whole routine is done. See [Routines](#routines).
- Live state without polling: one Bluetooth session stays open and the device
  pushes every change, including changes made with its own buttons.
- A private, revisioned **profile** per device with everything a power loss
  resets: playlist, clock settings, routine settings, wake and bedtime
  schedules, alarms, all seven daily routines, the light and playlist timers,
  volume and light brightness. It is read from the device, can be edited in
  the options flow, and can be exported or imported as JSON through actions.
  Volume, brightness, timer and clock changes made in Home Assistant are kept
  in it.
- **Power-loss recovery**: a power loss resets the Lumalou to factory settings.
  Home Assistant notices it on reconnect, sets the clock and writes the saved
  profile back automatically (can be switched off), then proves it with a
  fresh read. A restore never turns the light or sound on.
- **Maintenance** switch that releases the Bluetooth connection so the official
  app or another client can connect.
- Apple Home through Home Assistant's HomeKit Bridge (see below).
- Redacted diagnostics, Repairs, English and Russian translations.

## Safety and privacy

- The integration never touches the Nordic DFU service and has no OTA,
  firmware-update or factory-reset command. Unknown opcodes, the aggregate
  state and soother commands, nap commands, the time-prescaler and
  pairing-complete commands, and arbitrary GATT writes are blocked before any
  Bluetooth I/O. Routine start and routine control are sent only as their
  exact hardware-checked payloads, and only when you press a button or call
  an action.
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
  fresh complete read, never a color, play, soother, nap or routine start, and
  writes the Ready-to-Rise and routine on/off flags last, after the schedules
  they activate.

## Requirements

- Home Assistant 2026.9.0 or newer.
- A Bluetooth adapter or ESPHome Bluetooth proxy that can make active
  (connectable) connections, in range of the Lumalou. Proxies are expected to
  work through Home Assistant's Bluetooth stack but have not been tested yet.
- The Lumalou accepts **one** Bluetooth connection at a time. While the
  Fisher-Price app or a Web Bluetooth page (for example the upstream web
  client) is connected, Home Assistant cannot connect at all. Close them before
  setup; if the device stays blocked, unplug it for a few seconds (a power
  cycle drops the other connection; Home Assistant then restores the settings).

## Installation

### HACS (custom repository)

1. In HACS, open **⋮ → Custom repositories**, add
   `https://github.com/pavlikru/ha-lumalou` with type **Integration**.
2. Open **Lumalou** in HACS and select **Download**. To test a development
   build, choose its branch as the version.
3. Restart Home Assistant.

### Manual

Copy `custom_components/lumalou` into your Home Assistant `config/custom_components/`
directory and restart Home Assistant.

## Setup

1. Home Assistant shows a discovered **Lumalou** under **Settings → Devices &
   services**. Select **Add** and confirm that this is your Lumalou, or use
   **Add integration → Lumalou** and choose it from the discovered devices
   (choosing it is the confirmation).
2. Home Assistant connects once and reads and verifies the signed device
   identity. Nothing on the device is changed.
3. The profile options open next. Choose **Read the device profile**, check
   the summary and confirm. This first complete read becomes the saved
   profile, unlocks the controls and makes the profile editors available.

If the Lumalou later appears with a different Bluetooth address, use
**Reconfigure** on the entry (or confirm the new discovery). It accepts only
the same signed device and changes only the address; the saved profile and
unlocked controls stay.

### Options

**Settings → Devices & services → Lumalou → Configure**:

| Option | Meaning |
| --- | --- |
| Read the device profile | Read the complete profile from the Lumalou and, after confirmation, save it as the verified profile. Until this has been done once, only this and Behavior options are offered. |
| Playlist, Clock settings, Routine settings, Weekly schedule, Daily routines | Editors for the saved profile, prefilled from it. Confirming writes the change to the Lumalou right away and checks it with a fresh read (the same path as a restore; volume and brightness are taken from the device, not reverted). If the Lumalou cannot be reached (out of range, another app connected, **Maintenance** on), the change is still saved and you are told so; when Home Assistant reconnects, the Repair offers to write it (after a power loss it is restored automatically). |
| Behavior options → Restore the saved profile automatically | On by default. See [Profile restore and power loss](#profile-restore-and-power-loss). |

## Entities

Entity IDs below assume the device is named "Lumalou" and Home Assistant
runs in English; check the actual IDs in the entity settings.

| Entity | Type | Notes |
| --- | --- | --- |
| `light.lumalou_light` | Light | On/off, brightness, palette colors as effects: `warm` (yellow shimmering to pink), `red`, `yellow`, `orange`, `green`, `blue`, `purple`, `night_light` (steady green), `cool` (blue), `rainbow` (cycling); translated in the UI. "On" without an effect uses the current color. |
| `media_player.lumalou_audio` | Media player (speaker) | On starts the sleep playlist (the soother: music and a color-cycling light); off stops sound only; volume; source selects a built-in sound (`sleep_playlist`, `custom_playlist`, `pink_noise` (heard as white noise), `ocean`, `rain`, `brown_noise`, `nature`, `highway`). |
| Light duration, Playlist duration | Select (configuration) | Device timers (options such as `min_15`, `continuous`). |
| Clock format | Select (configuration) | `h12` or `h24` (24-hour). |
| Clock display | Switch (configuration) | Shows or hides the clock. |
| Clock brightness | Number (configuration) | 0–9. |
| Maintenance | Switch (configuration) | Releases Bluetooth and pauses all device I/O from Home Assistant. |
| Synchronize clock | Button (configuration) | Sets the device clock from Home Assistant's time zone. |
| Connection | Binary sensor (connectivity, diagnostic) | On while a Bluetooth session is live. |
| Firmware | Sensor (diagnostic) | Advertised firmware version; available while the device advertises, even without a session. |
| Profile sync status | Sensor (enum, diagnostic) | `empty`, `saved`, `pending`, `applying` or `error`. Revisions and the last error are in the diagnostics download. |
| `button.lumalou_start_routine` | Button | Starts today's routine now (see [Routines](#routines)). |
| `button.lumalou_complete_task`, `button.lumalou_previous_task`, `button.lumalou_cancel_routine` | Button | Only while a routine runs. Complete task is the remote's check-mark button. |
| `sensor.lumalou_routine` | Sensor (enum) | `off`, `ready` (silent preview before the first task), `in_progress`, `completed`. |
| `sensor.lumalou_current_task` | Sensor (enum) | `none` or the current task (`get_dressed`, `wash_up`, `brush_teeth`, `toilet`, `backpack`, `meal`, `story`, `tidy_up`, `heart`, `swirl`, `star`). |
| `event.lumalou_routine` | Event | `task_completed` (attribute `task`), `routine_completed`, `routine_cancelled`. |
| Routines | Switch (configuration) | Automatic start at each day's routine time. |
| Routine music, Task reward sound, Routine reward sound | Switch (configuration) | Routine sounds. |
| Routine volume | Number (configuration) | 0–9. |

Controls and configuration entities are available only after the device
profile was read and confirmed once.

**Soother.** The sleep playlist (media player on, the Apple Home sound switch,
or the big button on the remote) plays sleep music *and* switches on a
color-cycling light. Turning the media player off stops the music only; the
light stays on until you turn the light off. Both entities show exactly what
the device reports.

### How data is updated

There is no polling. One Bluetooth session stays open while the device is
reachable, and the Lumalou pushes its full state after every command and every
button press on the device, and its clock every minute. A command counts as
done when the device acknowledges the write; nothing is read back or resent.
When Home Assistant Bluetooth sees the device advertising and no session is
live, the integration reconnects (after a short pause, at most once every 30
seconds, backing off up to 15 minutes after failures). Before the first
confirmed profile read it only reads the state. Afterwards every reconnect
reads the complete profile and the device clock in one session, sets the clock
if it is more than 60 seconds off, and compares the profile with the saved one
(see below). While connected, a clock that drifts or misses a DST change is
corrected from the pushed clock, at most once an hour. If an automatic clock
write fails, a warning is logged and automatic clock writes pause for an hour
(the **Synchronize clock** button still writes at once). When Home Assistant
reports the device gone, entities become unavailable; loss and return are
logged once at info level.

### Profile restore and power loss

A power loss resets the Lumalou to factory settings: the clock (to 05:00 on
Sunday, 12-hour format), playlist, wake and bedtime times, alarms, routines,
timers, volume and brightness. On every reconnect Home Assistant compares a
fresh read with the saved **verified** profile:

- **Reset** — the device clock is more than 10 minutes off *and* the profile
  differs. Home Assistant sets the clock first. With **automatic restore** on
  (the default) it then writes only the differing settings back and proves
  the whole profile with a fresh read. After two failed attempts it stops and
  raises the Repair *Lumalou settings differ from the saved profile*. With
  automatic restore off, the Repair appears right away.
- **Settings changed without a reset** (for example in the Fisher-Price app):
  the Repair appears; nothing is written automatically. Volume, brightness and
  timers are not compared here, because the device buttons change them in
  everyday use.

In the Repair choose **Restore saved profile** or **Keep device settings** (the
current device settings become the new saved profile). The Repair also
appears for a change saved in Home Assistant that could not be written yet
(an editor or `lumalou.set_routine` while the Lumalou was unreachable), and a
reset restores that change automatically. A reconnect that finds the device
already matching such a change just marks it verified.

A restore (automatic, from the Repair, or `lumalou.restore_profile`) opens a
fresh session, reads everything, corrects the clock, writes only what differs
in a fixed order (clock settings, playlist, timers, volume and brightness,
routine sound and volume, weekly times, alarms and routines, then the
Ready-to-Rise and routine on/off flags), and then reads everything again in a
new session. It counts as verified only if every block matches. It never
writes a color and never starts sound, the soother, a nap or a routine; the
hardware check confirmed that none of these writes switches light or sound on.
Nothing is restored while **Maintenance** is on.

## Actions

All actions take `config_entry_id`. Clock sync and maintenance are a button
and a switch on the device (see [Entities](#entities)).

| Action | Description |
| --- | --- |
| `lumalou.export_profile` | Returns `{current_revision, profile}`. Keep it private. |
| `lumalou.import_profile` | Saves a complete exported `profile` if `expected_revision` matches the current revision. Does not write to the device. |
| `lumalou.restore_profile` | Writes the saved profile to the device and verifies it (see above). Optional `expected_revision` (default: the current revision) fails the action if the profile changed meanwhile. Can return `{revision, verified, applied_steps, clock_synced}`. |
| `lumalou.set_routine` | Sets the routine of the given `days` (`time`, ordered `tasks`) in the saved profile and on the device, verified. See [Routines](#routines). |
| `lumalou.start_routine` | Starts today's routine now; optional `tasks` run instead, this time only. See [Routines](#routines). |

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

## Routines

The Lumalou shows a routine as task icons on its face (get dressed, wash up,
brush teeth, toilet, backpack, meal, story, tidy up, heart, swirl, star), with
music and reward sounds. Each weekday has its own routine: a start time and up
to 11 tasks in order, one task per step, each task at most once. With
**Routines** on, the device enters routine mode about a minute before the time
(all icons blink, silently) and shows the first task at the time. The child
presses the remote's check-mark button when a task is done: a reward sound,
then the next task with its music. After the last task the routine completes
and the device returns to normal.

**Set a routine** in **Configure → Daily routines** or with the
`lumalou.set_routine` action. Both write it to the device right away and
verify it with a fresh read.
An empty task list means no routine on those days. Example: Monday to Friday
at 20:00, brush teeth, then toilet, then a story:

```yaml
action: lumalou.set_routine
data:
  config_entry_id: YOUR_ENTRY_ID
  days: [monday, tuesday, wednesday, thursday, friday]
  time: "20:00"
  tasks: [brush_teeth, toilet, story]
```

Then switch **Routines** on (configuration entity). Routine music, the two
reward sounds and the routine volume are configuration entities too. All of
these are part of the saved profile and come back after a power loss.

**Start a routine now** with the **Start routine** button or
`lumalou.start_routine`. Like the scheduled start, the first task becomes
current with its music right away. With `tasks`, those tasks run instead of
today's routine, this time only: Home Assistant writes them as today's routine
(keeping today's time), starts it, and writes today's saved routine back when
the routine ends. If Home Assistant is disconnected at that moment (or
restarts), it writes it back on the next connection; the one-off routine never
raises a Repair or counts as a reset.

```yaml
action: lumalou.start_routine
data:
  config_entry_id: YOUR_ENTRY_ID
  tasks: [tidy_up, story]
```

**Follow progress** with `sensor.lumalou_routine`, `sensor.lumalou_current_task`
and `event.lumalou_routine`. The event fires `task_completed` with the `task`
when the child completes a task, `routine_completed` after the last task, and
`routine_cancelled` when a routine ends before its last task (the **Cancel
routine** button, or on the device). Events are only seen while Home Assistant
is connected. Example: a notification when the teeth are brushed:

```yaml
automation:
  - alias: Teeth brushed
    triggers:
      - trigger: state
        entity_id: event.lumalou_routine
    conditions:
      - condition: template
        value_template: >-
          {{ trigger.to_state.attributes.event_type == 'task_completed'
             and trigger.to_state.attributes.task == 'brush_teeth' }}
    actions:
      - action: notify.notify
        data:
          message: Teeth brushed!
```

## Apple Home (HomeKit Bridge)

Use Home Assistant's built-in [HomeKit Bridge][homekit]. What Apple Home shows:

- **Light**: a lightbulb with on/off and brightness. The palette colors are
  light effects in Home Assistant and are **not** exported.
- **Audio**: a media player with device class `speaker` is exported as a
  switch. On starts the soother (sleep music and a color-cycling light); off
  stops the music, and the light stays on until the lightbulb is turned off.
  Volume and source stay in Home Assistant.
  Only `tv` (and `projector`) or `receiver` media players become Television
  accessories; the integration does not pretend the Lumalou is one.
- Configuration and diagnostic entities (maintenance, timers, clock settings,
  routine settings including the Routines switch, status sensors) are not
  exported by default, even in include mode, unless you list them explicitly.
  Keep them out.
- Routine buttons, sensors and the event are not in the default HomeKit Bridge
  domains, so they are not exported either. To start a routine from Apple
  Home, expose a script instead, for example:

  ```yaml
  script:
    lumalou_start_routine:
      alias: Start bedtime routine
      sequence:
        - action: button.press
          target:
            entity_id: button.lumalou_start_routine
  ```

  and add `script.lumalou_start_routine` to the bridge (include the entity, or
  the `script` domain). Apple Home shows it as a switch that turns itself off.

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
| "Could not connect for the read-only identity probe", or entities stay unavailable although the device is near | Another client (Fisher-Price app, a browser tab with Web Bluetooth) holds the single connection. Close it; if that does not help, unplug the Lumalou for a few seconds. |
| Controls locked after updating to 0.1.0b4 | The saved profile format changed. Open **Configure → Read the device profile** and confirm it once. |
| Controls fail with "Read and confirm the device profile…" | Open **Configure → Read the device profile** and confirm the result. |
| Repairs: "Lumalou settings differ from the saved profile" | Choose **Restore saved profile** or **Keep device settings**. If a restore fails, bring the adapter closer and try again; the diagnostics download shows which step failed. |
| Entities unavailable | The device is out of range or unpowered, **Maintenance** is on, or another client is connected. |
| Device moved to a new Bluetooth address | Use **Reconfigure** on the entry. |
| Setup fails: "created by a development build" | Remove the entry and add the device again. |
| Controls locked again after a restart | The saved profile could not be read (Home Assistant keeps an unreadable file as `.corrupt.<time>` and raises its own Repair). Read the device profile again, or import your last export. Do not edit `.storage` by hand. |

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
- Reset detection relies on the clock that a power loss resets. Volume,
  brightness or timer changes made with the device buttons are not saved;
  a restore brings back the last values set in Home Assistant.
- Routine music, the reward sounds and the routine volume are read back as
  4-bit values. The entities use on/off and 0–9 (checked on hardware); the
  profile editor still accepts the raw 0–15 values.
- Routine progress (sensors and events) comes from the device's pushes while
  Home Assistant is connected. After a reconnect in the middle of a routine,
  the step is unknown until the next change, and a routine that ended while
  disconnected fires no event. A routine that ends without reaching its last
  step (also "complete all" on the device) counts as cancelled.
- The routine start and control commands were checked on hardware with a
  library-level probe; the Home Assistant-level routine steps of the
  [acceptance checklist](docs/hardware-validation.md) are still open.
- Only sources `sleep_playlist` and `pink_noise` were checked on hardware; the
  other built-in sounds come from the protocol description.

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
