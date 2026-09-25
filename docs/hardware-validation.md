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
  about 1.5 seconds avoids it. In Home Assistant (0.1.0b4, Pi 4) a fresh
  session opened about 0.4 seconds after the live one closed failed twice in
  a row with "BLE connection was lost", while the recovery reconnect a few
  seconds later worked; 0.1.0b5 keeps 2 seconds from the finished close and
  retries twice.
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

**Routines** (validated with the owner watching)

- A day routine (`set_day_routine`) is a time plus up to 12 slots
  `step << 4 | task`; the app model is one task per step. Tasks 1–11: 1 get
  dressed, 2 wash up, 3 brush teeth, 4 toilet, 5 backpack, 6 meal, 7 story,
  8 tidy up, 9 heart, 10 swirl (seen on the face), 11 star (not yet seen).
  Task status is keyed by task id, so a task can appear only once.
- Routine mode `0x58 1` enables the automatic start: the device entered
  routine mode (GLOBAL_STATE `operationMode` 7) about a minute before the
  routine time, all routine icons blinking silently (step 0), and showed
  step 1 at the time.
- ROUTINE_TASK_STATUS (`0x94`) is pushed on every change: the current step and
  one nibble per task id (0 pending, 1 current, 2 done). Step 0 is not
  started; step N+1 is all done, after which the device resets the status and
  returns to `operationMode` 0.
- The remote's check-mark button completes the current task, the same as
  `routine_control 0`: a reward sound, then the next icon and its music. With
  music and both reward sounds on and routine volume 2: "wow", then music,
  then the icon.
- Manual start `0x7B` (no argument): `operationMode` 0 → 7 at step 0 (icons
  blink, silent). The first `routine_control 0` makes task 1 current with
  sound and music, so finishing N tasks takes a start plus N+1 check presses.
- `0x7B` is ignored while routine mode (`0x58`) is off: `operationMode` stays
  0. With routine mode on and every day routine empty, a one-off routine
  (tasks written for today, started, today's saved routine written back)
  works.
- In routine mode (`operationMode` 7) the device ignores light and audio
  commands (a rainbow light on, a source change, sound off): acknowledged,
  no effect.
- `routine_control` (`0x6B`) codes: 0 complete task/advance; 1 previous task;
  2 restart (no visible change at step 1); 3 complete the whole sequence
  (completion sound and animation, mode 0, statuses reset); 4 cancel (silent,
  icons disappear, mode 0).
- After codes 1, 2 and 3 the device sends a bare `01 50` frame on the
  application route. `lumalou-gld09` 0.2.1 rejected it and ended the
  session; 0.3.0, which the integration requires, ignores it.
- A routine sets no light color or brightness; its visuals are the face
  icons plus music and reward sounds.
- Routine music, task reward sound and routine reward sound are 0/1; routine
  volume is 0–9. All write and read back exactly.

**Power loss**

Unplugging the device for about 10 seconds resets it to factory settings:
clock 05:00 on Sunday in 12-hour format; wake and bedtime times and all
routines 00:00 with empty slots; playlist 1–12; light timer 4, playlist timer
5, volume 5, routine volume 5, routine music 1, reward sounds 1/1, alarm sound
0, LED brightness 5, color 0; light and sound off. The integration therefore
treats this power-loss clock (Sunday, running from 05:00) on reconnect plus a
differing profile as a reset and restores the saved profile (see the README).
In the 0.1.0b6 Home Assistant run a replug reset the settings (12-hour clock,
routines, routine settings) but the clock was only slightly off and was not
recognised; since 0.1.0b7 factory settings alone are a reset, and the raw
clock and every criterion are logged at info level. With 0.1.0b7 the
restore worked; the device clock read 04:59:07 on Sunday right after that
power loss, so the power-loss clock is accepted from 04:55.

After a scheduled routine started (0.1.0b7), the live session received no
state, clock or routine frame for more than four minutes while the device
ran the tasks; a session opened before the start (library probe) kept
receiving step changes. Since 0.1.0b8 such a session is reconnected after 90
seconds and the routine progress is read again.

## Home Assistant acceptance checklist

Run on 2026-09-25 with Home Assistant on a Raspberry Pi 4 (onboard
Bluetooth), firmware 0.3.7, and the builds 0.1.0b4 to 0.1.0b8; each failure
above was fixed and checked again. Results were recorded privately; this is
the anonymized summary.

- [x] **Install and setup** (2026-09-25): HACS install; discovery,
      confirmation and the first profile read; one entry.
- [x] **Light** (2026-09-25): on, off, effects and brightness from Home
      Assistant within about 0.3 seconds; changes made with the remote are
      reflected at once.
- [x] **Speaker** (2026-09-25): the media player starts the soother; stopping
      it leaves the light on; the source is shown while it plays.
- [x] **Clock** (2026-09-25): the 24-hour format is kept.
- [x] **Power loss** (2026-09-25, 0.1.0b7): after unplugging, Home Assistant
      reconnected, set the clock and restored the saved profile (24-hour
      clock, the day's routine, routine mode) automatically, verified by a
      fresh read, without switching light or sound on.
- [x] **Repairs** (2026-09-25): **Restore saved profile** wrote only the
      differing blocks and verified them.
- [x] **Routine setup** (2026-09-25): `lumalou.set_routine` wrote and verified
      the routine; with **Routines** on the device entered the preview
      (`ready`) and showed brush teeth at the time (`in_progress`).
- [x] **Routine progress** (2026-09-25, 0.1.0b8): the remote's check-mark
      button fired `task_completed` for each task, then `routine_completed`,
      and the sensor returned to `off`.
- [x] **Manual start and cancel** (2026-09-25): **Start routine** made task 1
      current after about 1.3 seconds; **Cancel routine** ended it.
- [x] **HomeKit Bridge** (2026-09-25): Apple Home shows the lightbulb and the
      audio switch; no configuration or routine entities.
- [x] **One-off routine** (2026-09-25, 0.1.0): with **Routines** on and all
      day routines empty, `lumalou.start_routine` with a meal task started;
      the check-mark fired `task_completed` and `routine_completed`, and the
      empty day was written back. With **Routines** off the device ignored
      the start (refused since 0.1.1).
- [ ] Not yet checked on hardware: **Previous task**; a one-off routine's
      write-back after a disconnect; the diagnostics download after its
      0.1.0b7 fix; Bluetooth proxies.
