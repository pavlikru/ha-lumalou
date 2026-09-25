# Hardware validation checklist

Manual acceptance test for one Lumalou (`GLD09`) on the target Home Assistant.
Run it together with the device owner, one phase at a time. Every phase depends
on the previous one passing. No stable release before all phases pass or a
limitation is written down in the README.

Automated tests use mocked Bluetooth only. They do not prove advertising,
payload layouts, setter side effects or power-loss behavior.

## Rules for every session

- Record privately (never in Git or a public issue): date and time, Home
  Assistant and integration versions, library version, Bluetooth adapter or
  proxy, profile revision, result. Publish only an anonymized summary.
- Never commit or post Bluetooth addresses, device fingerprints, factory tokens,
  exported profiles or family schedules.
- Use low light levels and low volume during the day. The owner does all
  physical power cycling. Restart Home Assistant and the host only cleanly.
- After each group of writes, restore the baseline recorded in phase 1 and
  confirm it with a fresh read.

**Stop immediately** on: unexpected light, sound, nap or routine activation;
a pairing request; an unknown GATT service or characteristic; incomplete or
malformed readback; a profile revision conflict; failure to restore the
baseline; any effect on another integration or HomeKit accessory. Never
continue with raw opcodes, DFU access, `.storage` edits or higher output
levels.

## Phase 0 – Preparation

- [ ] Fresh Home Assistant backup exists.
- [ ] Installed build and its exact library pin are recorded.
- [ ] The Fisher-Price app and any Web Bluetooth client are disconnected.
- [ ] The existing HomeKit Bridge configuration (mode, filter, entity list) is
      noted so it can be compared afterwards.

## Phase 1 – Read-only setup and baseline

- [ ] Lumalou is discovered by Home Assistant without pressing a pairing
      button.
- [ ] Setup confirmation creates exactly one entry; the fingerprint and
      factory token do not appear in the UI or logs.
- [ ] **Read the device profile** succeeds; the summary looks plausible;
      confirm it. Export the profile (`lumalou.export_profile`) and keep it
      privately as the baseline.
- [ ] Reload the entry and restart Home Assistant: still one entry and one
      device, entities come back, no settings changed on the device.
- [ ] Download diagnostics and check that they contain no address, fingerprint
      or schedule.

## Phase 2 – Light, one command at a time

Check the device physically and the Home Assistant state after each step.

- [ ] Light on at brightness 1; then brightness 3.
- [ ] Two palette effects (for example `WARM`, `BLUE`).
- [ ] Light off.
- [ ] Light duration select: change one step, then back.
- [ ] Note any side effects (does a brightness change turn the light on, does
      a color change affect sound?).
- [ ] Restore baseline; fresh read matches the export.

## Phase 3 – Sound at low volume

- [ ] Set volume to 1 **before** playing.
- [ ] Media player on (sleep playlist) for a few seconds, then off.
- [ ] Select one built-in sound source, then off.
- [ ] Volume up/down by one step.
- [ ] Playlist duration select: change one step, then back.
- [ ] Restore baseline; fresh read matches the export.

## Phase 4 – Clock and maintenance

- [ ] **Synchronize clock**: device shows Home Assistant local time.
- [ ] **Maintenance** on: Bluetooth is released, the Fisher-Price app can
      connect. Disconnect the app, Maintenance off: Home Assistant reconnects.

## Phase 5 – Power-loss recovery

Only with a build that implements restore and with **Automatic restore**
enabled.

- [ ] Change one persistent setting per block in the offline editors, save,
      and let it apply. Export the resulting profile privately.
- [ ] Owner unplugs the Lumalou for about 10 seconds, then plugs it back in.
- [ ] Record whether the device advertises again without any button press, and
      how long it takes.
- [ ] Home Assistant reconnects, sets the clock, re-applies only the settings
      that differ and confirms the whole profile with a fresh read.
- [ ] No sound, light, nap or routine was started by the restore.
- [ ] Repeat with a long outage (several minutes) and after a Home Assistant
      restart during the outage.
- [ ] Restore the original baseline.

## Phase 6 – Apple Home through HomeKit Bridge

- [ ] Add `light.lumalou_light` (and optionally `media_player.lumalou_audio`)
      to the existing bridge as described in the README. Do **not** reset or
      re-pair the existing bridge; keep its mode and filters.
- [ ] Apple Home shows a lightbulb with on/off and brightness; toggling it
      changes the device and Home Assistant state.
- [ ] If exported, the sound switch starts and stops sound.
- [ ] No maintenance switch, timers, buttons or diagnostic sensors appear in
      Apple Home.
- [ ] Other accessories, rooms and automations in Apple Home are unchanged.
- [ ] Restore baseline.

## Phase 7 – Release candidate

- [ ] Install the tagged pre-release through HACS; update from the previous
      build and roll back once.
- [ ] Run for several days with normal use. Record disconnects and errors
      (anonymized).
- [ ] Write the anonymized results into the release notes and remove the
      "development preview" warning from the README only if every phase
      passed.

## Evidence so far

Read-only observations on one device, before the integration could create an
entry:

- The device advertises connectably and is found by Home Assistant Bluetooth
  discovery on a Raspberry Pi built-in adapter. The standard Device
  Information Model Number is not readable, so setup cannot rely on it.
- The signed factory token was read once and its signature verified locally.
- With the forked library, one fresh session read every persistent block
  (state, playlist, clock settings, weekly times, alarms, seven routines);
  the clock settings agreed with the state. No setting was written.
- A browser session of the upstream web client saw routine and schedule
  responses of the expected lengths and a `CURRENT_DATE` reply in BCD
  hour/minute/second/weekday form.

No write, power-cycle or HomeKit test has been run.
