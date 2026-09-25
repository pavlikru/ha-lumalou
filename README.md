# Lumalou for Home Assistant

[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Validate](https://github.com/pavlikru/ha-lumalou/actions/workflows/validate.yml/badge.svg)](https://github.com/pavlikru/ha-lumalou/actions/workflows/validate.yml)

Local Bluetooth LE control of the Fisher-Price Lumalou Better Bedtime Routine
System (`GLD09`) from Home Assistant. No cloud account and no vendor app are
needed. The Bluetooth protocol lives in the
[`lumalou-gld09`](https://github.com/pavlikru/lumalou) Python package, a fork
of [`stramanu/lumalou`](https://github.com/stramanu/lumalou).

> [!NOTE]
> **Validated on real hardware**: a Lumalou with firmware 0.3.7 and Home
> Assistant on a Raspberry Pi 4 with its onboard Bluetooth adapter (see the
> [hardware validation](docs/hardware-validation.md) and the
> [known limitations](#limitations)). Other firmware versions and Bluetooth
> proxies have not been tested.

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
2. Open **Lumalou** in HACS and select **Download** (the latest release).
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
| `light.lumalou_light` | Light | On/off, brightness, palette colors as effects: `warm` (yellow shimmering to pink), `red`, `yellow`, `orange`, `green`, `blue`, `purple`, `night_light` (steady green), `cool` (blue), `rainbow` (cycling); translated in the UI. "On" without an effect uses the current color. While the soother cycles the colors, no effect is shown. |
| `media_player.lumalou_audio` | Media player (speaker) | On starts the sleep playlist (the soother: music and a color-cycling light); off stops sound only; volume; source selects a built-in sound (`sleep_playlist`, `custom_playlist`, `pink_noise` (heard as white noise), `ocean`, `rain`, `brown_noise`, `nature`, `highway`). The source shown is the playing sound; for the two playlists (which share songs) it is the one Home Assistant started, or `sleep_playlist` when the soother runs (also from the remote), otherwise empty. |
| Light duration, Playlist duration | Select (configuration) | Device timers (options such as `min_15`, `continuous`). |
| Clock format | Select (configuration) | `h12` or `h24` (24-hour). |
| Clock display | Switch (configuration) | Shows or hides the clock. |
| Clock brightness | Number (configuration) | 0–9. |
| Maintenance | Switch (configuration) | Releases Bluetooth and pauses all device I/O from Home Assistant. |
| Synchronize clock | Button (configuration) | Sets the device clock from Home Assistant's time zone. |
| Connection | Binary sensor (connectivity, diagnostic) | On while a Bluetooth session is live. |
| Firmware | Sensor (diagnostic) | Advertised firmware version; available while the device advertises, even without a session. |
| Profile sync status | Sensor (enum, diagnostic) | `empty`, `saved`, `pending`, `applying` or `error`. Revisions and the last error are in the diagnostics download. |
| `button.lumalou_start_routine` | Button | Starts today's routine now (see [Routines](#routines)); unavailable while a routine runs. |
| `button.lumalou_complete_task`, `button.lumalou_previous_task`, `button.lumalou_cancel_routine` | Button | Only while a routine runs. Complete task is the remote's check-mark button. |
| `sensor.lumalou_routine` | Sensor (enum) | `off`, `ready` (silent preview before the first task), `in_progress`, `completed`. |
| `sensor.lumalou_current_task` | Sensor (enum) | `none` or the current task (`get_dressed`, `wash_up`, `brush_teeth`, `toilet`, `backpack`, `meal`, `story`, `tidy_up`, `heart`, `swirl`, `star`). |
| `event.lumalou_routine` | Event | `task_completed` (attribute `task`), `routine_completed`, `routine_cancelled`, `routine_expired`. |
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
A setting (volume, brightness, timers, clock and routine settings) must also
show up in the next pushed state: if it does not, it is written once more,
and if it still does not, the action fails with an error. A command sent while
the session is being reopened (for example right after a profile write) waits
up to 15 seconds for it.
When Home Assistant Bluetooth sees the device advertising and no session is
live, the integration reconnects (at least 2 seconds after the previous
connection closed, retrying twice if the link drops while connecting; at most
once every 30
seconds, backing off up to 15 minutes after failures). Before the first
confirmed profile read it only reads the state. Afterwards every reconnect
reads the complete profile and the device clock in one session, sets the clock
if it is more than 60 seconds off, and compares the profile with the saved one
(see below). While connected, a clock that drifts is corrected from the pushed
clock at most once an hour; an offset of more than 10 minutes (such as a DST
change) at once. If an automatic clock write fails, a warning is logged, that
reconnect counts as failed and automatic clock writes pause for an hour (the
**Synchronize clock** button still writes at once). A session that sends no
state, clock or routine update for three minutes (90 seconds while a routine
runs) is treated as lost and reconnected; the minute clock alone keeps it
alive. A reconnect during a routine reads its progress, so the sensors show
the current task again. When Home Assistant
reports the device gone, entities become unavailable; loss and return are
logged once at info level.

### Profile restore and power loss

A power loss resets the Lumalou to factory settings: the clock (to 05:00 on
Sunday, 12-hour format), playlist, wake and bedtime times, alarms, routines,
timers, volume and brightness. On every reconnect Home Assistant compares a
fresh read with the saved **verified** profile:

- **Reset** — every setting on the device is at its factory default, or the
  device clock shows the power-loss restart, *and* the profile differs (a
  short outage can reset the settings while the clock keeps running). The
  power-loss clock is Sunday, running from 05:00 for no longer
  than Home Assistant has not heard from the device (at most 12 hours), more
  than 10 minutes off and not off by whole hours. An offset of whole hours
  (a DST or time zone change while Home Assistant was down) only sets the
  clock, unless Home Assistant heard the device within the last hour or every
  setting on the device is at its factory default. Home Assistant sets the clock first. With **automatic restore** on
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
new session. It counts as verified only if every block matches. Light and
sound values the device cannot show at that moment are not compared (all of
them while the soother runs, the brightness while the light is off); a
mismatch is logged with the saved and the device value of each field. Light
and sound changes made while the soother runs are not saved in the profile. It never
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
current with its music right away (if the Lumalou does not enter routine mode,
you get an error and nothing stays changed). "Today" is the weekday of the
device clock. With `tasks`, those tasks run instead of
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
`routine_cancelled` when Home Assistant cancelled it (**Cancel routine**), and
`routine_expired` when the device ended it before its last task (for example
an untouched routine after about two hours). Events come only from changes
Home Assistant sees while connected and while the routine runs: a step
completed during a reconnect fires nothing, but the sensors catch up.

Example: a notification when the teeth are brushed. An event entity keeps its
last event and shows it again after being unavailable (for example after a
reconnect or a restart), so ignore changes from `unavailable`:

```yaml
automation:
  - alias: Teeth brushed
    triggers:
      - trigger: state
        entity_id: event.lumalou_routine
        not_from: unavailable
        not_to: unavailable
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
- A YAML `homekit:` bridge without a `filter`, or one that includes the
  `button` domain, exports the routine buttons as switches (and every other
  button). Use a filter as in the example below.

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
| Controls fail with "Read and confirm the device profile…" | Open **Configure → Read the device profile** and confirm the result. |
| Repairs: "Lumalou settings differ from the saved profile" | Choose **Restore saved profile** or **Keep device settings**. If a restore fails, bring the adapter closer and try again; the diagnostics download shows which step failed. |
| Entities unavailable | The device is out of range or unpowered, **Maintenance** is on, or another client is connected. |
| Device moved to a new Bluetooth address | Use **Reconfigure** on the entry. |
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

- Only the `GLD09` Lumalou is supported, validated with firmware 0.3.7. Setup
  verifies the signed identity of the individual device, not the retail
  model.
- The Lumalou accepts a single Bluetooth connection: close the Fisher-Price
  app (and any Web Bluetooth page) or switch on **Maintenance** when you need
  the app.
- The soother (sleep playlist) light keeps cycling after its music stops;
  turn the light off separately.
- A scheduled routine plays no music on firmware 0.3.7 (observed on the
  device); a routine started from Home Assistant does.
- Naps are not supported.
- **Complete task**, **Previous task** and **Cancel routine** are available
  only while a routine runs; **Start routine** only while none runs.
- Routine events come only from changes seen while connected. An event
  entity shows its last event again after being unavailable; use
  `not_from: unavailable` in automations (see [Routines](#routines)).
- Power-loss detection: a reconnect that finds every setting at its factory
  default, or the device clock restarted around 05:00 on Sunday, is treated
  as a power loss and the saved profile is restored automatically (unless
  switched off). Other differences raise a Repair. Volume, brightness or
  timer changes made with the device buttons are not saved; a restore brings
  back the last values set in Home Assistant.
- Palette colors cannot be exported to Apple Home.
- Routine music, the reward sounds and the routine volume are read back as
  4-bit values. The entities use on/off and 0–9 (checked on hardware); the
  profile editor still accepts the raw 0–15 values.
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
