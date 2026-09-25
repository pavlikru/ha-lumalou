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
- [ ] **Automatic restore** is off (the default).

Items marked **A1**–**A12** check the hardware assumptions listed at the end of
this document. Record each as confirmed, refuted or not observed.

## Phase 1 – Read-only setup and baseline

- [ ] Lumalou is discovered by Home Assistant without pressing a pairing
      button.
- [ ] Setup confirmation creates exactly one entry; the fingerprint and
      factory token do not appear in the UI or logs.
- [ ] **A12**: setup and the first profile read connect reliably through the
      adapter in use (record the adapter model, for example a Raspberry Pi 4
      onboard adapter, or proxy). Note any retries in the debug log.
- [ ] **Read the device profile** succeeds; the summary looks plausible;
      confirm it. Export the profile (`lumalou.export_profile`) and keep it
      privately as the baseline.
- [ ] **A10**: the same read session answered the current-date request (no
      read error; diagnostics show `last_clock_offset`).
- [ ] **A9**: the Firmware sensor matches the version shown by the official
      app or the device documentation.
- [ ] Reload the entry and restart Home Assistant: still one entry and one
      device, entities come back, no settings changed on the device.
- [ ] Download diagnostics and check that they contain no address, fingerprint
      or schedule.

## Phase 2 – Light, one command at a time

Check the device physically and the Home Assistant state after each step.

- [ ] Light on at brightness 1; then brightness 3.
- [ ] Two palette effects (for example `warm`, `blue`).
- [ ] Light off.
- [ ] **A3**: with the light off, changing brightness or color does not turn
      the light (or sound) on unexpectedly; a fresh read reports the new
      brightness and color while the light stays off. Brightness 0 is never
      sent by a live control; note whether a restore of brightness 0 has a
      visible effect.
- [ ] Light duration select: change one step, then back.
- [ ] Note any side effects (does a brightness change turn the light on, does
      a color change affect sound?).
- [ ] Restore baseline; fresh read matches the export.

## Phase 3 – Sound at low volume

- [ ] Set volume to 1 **before** playing.
- [ ] **A3**: setting the volume while sound is off does not start sound.
- [ ] Media player on (sleep playlist) for a few seconds, then off.
- [ ] Select one built-in sound source, then off.
- [ ] Volume up/down by one step.
- [ ] Playlist duration select: change one step, then back.
- [ ] Restore baseline; fresh read matches the export.

## Phase 4 – Clock, reconnects and maintenance

- [ ] **Synchronize clock**: device shows Home Assistant local time.
- [ ] **A6**: set the device clock wrong by several minutes with the official
      app (then disconnect the app), let Home Assistant reconnect: the clock
      is corrected in the same session, the weekday is right (device counts
      Sunday as 0), and an offset below 60 seconds is left alone.
- [ ] **A8**: leave the device idle for an hour and count disconnects and
      reconnects in the debug log. Each reconnect performs one full profile
      read, at most one per 30 seconds; record whether that frequency is
      acceptable.
- [ ] **Maintenance** on: Bluetooth is released, the Fisher-Price app can
      connect. Disconnect the app, Maintenance off: Home Assistant reconnects.

## Phase 5 – Profile restore without power loss

- [ ] In the options editors change one setting per block (playlist, clock
      settings, routine music and rewards, routine volume, Ready-to-Rise and
      Sleepy times, alarms, and at least one day routine), keeping sound and
      light levels low. Profile pending turns on; nothing changes on the
      device.
- [ ] Run `lumalou.restore_profile` with `return_response: true`. Record the
      applied steps.
- [ ] **A1**: every setter persisted and the verification read returned
      exactly the saved values (`verified: true`); repeat for all seven day
      routines.
- [ ] **A2**: routine music and routine volume (limited to 0–15 by the
      editor) read back exactly as saved; include one low nonzero value for
      each.
- [ ] **A4**: enabling Ready-to-Rise and routine mode (written last) does not
      start a routine, alarm or sound immediately.
- [ ] **A5**: a restore of every block (about 22 writes) succeeds with the
      library's 150 ms write spacing and a fresh verification session; note
      any dropped write or timeout.
- [ ] **A11**: a second full read right after the writes returns the new
      values, not cached old ones (compare with **Read the device profile**
      preview a minute later).
- [ ] Change a setting with the official app, reconnect Home Assistant: the
      *settings differ* Repair appears. Test **Keep device settings** once and
      **Restore saved profile** once.
- [ ] Restore the original baseline.

## Phase 6 – Power-loss recovery

- [ ] **A7**: owner unplugs the Lumalou for about 10 seconds, then plugs it
      back in. Record whether the clock and/or the profile were reset, and
      whether the device advertises again without a button press or pairing
      mode, and how long it takes.
- [ ] With automatic restore **off**: the clock is corrected and, if the
      profile was reset, the Repair appears; **Restore saved profile**
      verifies.
- [ ] Enable **Automatic restore** and repeat: Home Assistant corrects the
      clock, re-applies only the settings that differ and confirms the whole
      profile with a fresh read.
- [ ] No sound, light, nap or routine was started by the restore.
- [ ] Repeat with a long outage (several minutes) and after a Home Assistant
      restart during the outage.
- [ ] Disable automatic restore again unless the owner wants it, and restore
      the original baseline.

## Phase 7 – Apple Home through HomeKit Bridge

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

## Phase 8 – Release candidate

- [ ] Install the tagged pre-release through HACS; update from the previous
      build and roll back once.
- [ ] Run for several days with normal use. Record disconnects and errors
      (anonymized).
- [ ] Write the anonymized results into the release notes and remove the
      "development preview" warning from the README only if every phase
      passed.

## Hardware assumptions to validate

The code relies on these assumptions; none is proven on hardware yet.

1. **A1** Each profile setter persists and reads back exactly: playlist, clock
   settings, routine music and rewards, routine volume, weekly times, alarms
   and the seven day routines.
2. **A2** Routine music and routine volume read back as 4-bit values from
   the global state, so the integration limits both to 0–15; values 0–15
   written by restore read back unchanged.
3. **A3** Brightness, color and volume setters, including brightness 0, cause
   no unwanted light or sound activation; brightness and color read back
   correctly while the light is off.
4. **A4** Writing `ready_to_rise.enabled` and `routine_settings.enabled` last
   does not start a routine, alarm or sound.
5. **A5** The library's 150 ms write spacing and a fresh verification session
   are enough for bursts of up to about 22 writes.
6. **A6** Setting the clock in the middle of a session works, a 60-second
   tolerance is appropriate, and the device weekday counts Sunday as 0.
7. **A7** A power loss actually resets the clock and/or the profile, and the
   device advertises again after power-on without pairing mode.
8. **A8** Idle disconnects are rare enough: each one triggers a full read,
   rate-limited to one per 30 seconds.
9. **A9** The firmware version in the advertisement matches the device's
   firmware.
10. **A10** The current-date read is accepted in the same session as the other
    profile reads.
11. **A11** A second full read right after writes returns the new values.
12. **A12** `establish_connection` with the service cache works on the
    Raspberry Pi 4 onboard adapter (BCM43438).

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
