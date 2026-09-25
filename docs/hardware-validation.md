# Hardware validation

## Verified on hardware

The device owner checked every Bluetooth call the integration uses on one
Lumalou (firmware 0.3.7) with a library-level probe (`lumalou-gld09` 0.2.1),
watching the device. The results below are authoritative for this firmware and
replace the earlier design assumptions.

**Connection**

- The device accepts a single BLE central. Another central (the Fisher-Price
  app, a browser Web Bluetooth tab) blocks Home Assistant completely: connects
  time out while the device still advertises. A power cycle drops the other
  central.
- The device advertises after power-on without pairing mode.
- An immediate reconnect after a disconnect sometimes fails once; a pause of
  about 1.5 seconds avoids it.
- `establish_connection` through the Home Assistant Bluetooth stack works on a
  Raspberry Pi 4 onboard adapter.

**Reads**

- 33 of 35 named reads answer in one session. `nap_alarm_status` and
  `nap_alarm` time out on this firmware; the integration never sends them.
- The current-date read works in the same session as the profile reads; the
  weekday counts Sunday as 0.
- GLOBAL_STATE carries the live values that have no typed single-value
  decoder (light status, brightness and color, volume, timers, clock format,
  routine music and volume, reward sounds, playing state, stage).

**Pushes**

- After every command the device pushes an updated GLOBAL_STATE in the same
  session (often twice), plus the matching single-value response. Button
  presses on the device and the remote push GLOBAL_STATE too. CURRENT_DATE is
  pushed at least at every minute boundary.
- The strict library refuses a second explicit state request in one session;
  pushes make it unnecessary.
- Write acknowledgements follow every write (handled by the library since
  0.2.1).

**Light**

- SET_LIGHT_COLOR switches the light on at the stored brightness.
- SET_LED_BRIGHTNESS changes brightness only; it does not switch the light on.
  While off, the new brightness is stored and used by the next color command.
  Level 1 is invisible in daylight, level 3 visible.
- TURN_OFF_BACKLIGHT switches the light off and keeps brightness and color;
  GLOBAL_STATE keeps reporting both while off.
- Colors as seen: 0 warm (yellow slowly shimmering to pink), 1 red, 2 yellow,
  3 orange, 4 green, 5 blue, 6 purple, 7 night light (steady green), 8 cool
  (blue), 9 rainbow (cycling). No light command makes a sound.

**Sound**

- SET_VOLUME while silent does not start sound.
- PLAY_AUDIO 0 (sleep playlist) is the soother: sleep music and a
  color-cycling light. TURN_OFF_AUDIO stops the music; the light keeps cycling
  until TURN_OFF_BACKLIGHT. The remote's big button toggles the same soother.
- PLAY_AUDIO 2 plays white noise without light.

**Clock**

- SET_CLOCK_SETTINGS `[display, brightness << 4 | format]`: format 1 is the
  24-hour clock.
- SET_CURRENT_DATE sets the clock correctly.

**Persistent settings**

- Playlist order, light and playlist timers, routine volume, routine music and
  reward sounds, wake and bedtime times, alarm sound (alarms kept inactive),
  a daily routine (time and steps) and clock settings all write and read back
  exactly. Timer, volume and brightness writes do not switch light or sound
  on.

**Power loss**

Unplugging the device for about 10 seconds resets it to factory settings:
clock 05:00 on Sunday in 12-hour format; wake and bedtime times and all
routines 00:00 with empty slots; playlist 1–12; light timer 4, playlist timer
5, volume 5, routine volume 5, routine music 1, reward sounds 1/1, alarm sound
0, LED brightness 5, color 0; light and sound off. The integration therefore
treats a device clock far off on reconnect plus a differing profile as a reset
and restores the saved profile (see the README).

## Home Assistant acceptance checklist

Still to run on the target Home Assistant with this build. Record results
privately (never Bluetooth addresses, fingerprints, exported profiles or
family schedules) and publish only an anonymized summary. Use low light levels
and low volume. The owner does all physical power cycling. Stop on any
unexpected light, sound, nap or routine activation, a pairing request or a
profile revision conflict.

- [ ] **Install**: fresh backup; install through HACS; restart. After an
      update from 0.1.0b3, **Read the device profile** once (the saved
      profile format changed) and confirm it.
- [ ] **Setup**: discovery, confirmation and the first profile read succeed
      with the Fisher-Price app and Web Bluetooth tabs closed; exactly one
      entry; diagnostics contain no address, fingerprint or schedule.
- [ ] **Light**: on, off, two effects and a brightness change from Home
      Assistant; the entity follows within a second, also for changes made
      with the remote. No reconnect after a command in the debug log.
- [ ] **Speaker**: volume while silent does not start sound; media player on
      starts the soother, off stops the music and leaves the light on (the
      light entity shows it on).
- [ ] **Clock**: select **24-hour**; the device shows 24-hour time and the
      saved profile keeps it (export).
- [ ] **Power loss**: unplug the device for about 10 seconds. Home Assistant
      reconnects, sets the clock, restores the saved profile (24-hour clock,
      schedules, volume, brightness) without switching light or sound on, and
      raises no Repair. Repeat once with **automatic restore** off: the
      Repair appears and **Restore saved profile** verifies.
- [ ] **HomeKit Bridge**: add `light.lumalou_light` (and optionally
      `media_player.lumalou_audio`) to the existing bridge without resetting
      it; Apple Home shows a lightbulb with brightness and a switch; other
      accessories are unchanged; no configuration entities appear.

Remove the "Beta" warning from the README only after every item passed.
