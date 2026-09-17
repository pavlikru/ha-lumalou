# Hardware validation ledger

Read-only browser inspection was run on 2026-09-16 against a Lumalou already
connected by the user to the upstream Web Bluetooth client in Chrome. No light,
audio, clock, schedule, routine, raw-opcode, or other write control was used.
The integration itself was not installed or connected.

The same existing browser session was inspected again on 2026-09-17. Navigation
between read-only views caused schedule/routine queries but no reconnect and no
configuration command. The device emitted four-byte `CURRENT_DATE` payloads
through the local midnight boundary: `23:59:00`, weekday `03`, was followed by
`00:00:00`, weekday `04`. This supports a BCD hour/minute/second/weekday response
layout on this target only; product code and firmware are still unrecorded.

| Item | Status | Evidence |
| --- | --- | --- |
| Exact product code | Not recorded | Requires label inspection |
| Lumalou firmware | Not recorded | Requires advertisement/readback |
| HA installation and version | Configuration record only | Raspberry Pi 4 (4 GB), HAOS 18.2/aarch64, Core 2026.9.2, Supervisor 2026.09.0; live state not verified |
| Connectable adapter/backend | Not recorded | Requires target diagnostics |
| Browser BLE connection | Observed | Upstream web client showed connected and received fresh state/date responses |
| Advertising after power-on | Not tested | Required for unattended recovery |
| Integration read-only onboarding | Not tested on hardware | Browser evidence does not validate the HA adapter |
| Light/audio controls | Not tested on hardware | Setter side effects unknown |
| Browser schedule readback | Partially observed | Both 14-byte week blocks were all zero; 4-byte alarm block was inactive |
| Browser seven-routine readback | Partially observed | Seven distinct 14-byte day responses and a 7-byte task-status response were all zero |
| Full schedules and seven routines in Python | Blocked on release and hardware proof | A local upstream candidate has strict Python/JavaScript codecs and 389 passing Python tests; released upstream 0.1.0 still lacks them |
| Ten Lumalou power cycles | Not run | Acceptance test |
| 72-hour soak | Not run | Acceptance test |

Stable-release claims are prohibited until this ledger contains anonymized
results for the target device and the complete profile API is available.

## Authorization and stop conditions

Before any integration connection, record all of the following in this private
test session; do not commit identifiers or family schedules:

- a label-confirmed `GLD09` product code and the firmware version, with serial
  numbers and Bluetooth addresses redacted from retained evidence;
- explicit permission to deploy/restart the Raspberry Pi Home Assistant and a
  separate permission before changing the HomeKit Bridge filter;
- an agreed local-time window, maximum light level, maximum audio level and the
  number of permitted Lumalou-only power cycles;
- a fresh Home Assistant backup and exported Lumalou profile kept outside this
  public repository.

Stop immediately on an unexpected light/audio/routine activation, a request for
Pairing, any unknown GATT service/characteristic, malformed or incomplete fresh
readback, profile revision conflict, failure to preserve the pre-test profile,
or impact on another Home Assistant integration. Never continue by sending raw
opcodes, accessing DFU, editing `.storage`, restarting the host by cutting
power, or raising output levels beyond the agreed limits.

## Ordered acceptance runbook

Each phase requires the previous phase to pass. Record UTC and HA-local
timestamps, component/upstream versions, profile revision, anonymized result,
duration, and rollback result for every phase.

1. **Read-only target inventory.** Verify live HA/HAOS/Supervisor versions,
   Bluetooth adapter/backend, label and firmware. Close the Web Bluetooth
   session, confirm connectable advertising without Pairing, install the exact
   prerelease, onboard GLD09 and request fresh state. Confirm no device setting
   changes and no duplicate entry after reload.
2. **Minimal live controls.** After a second explicit go-ahead, test light at
   the agreed minimum level, then short audio at the agreed minimum volume.
   Test stop/off and one clock sync separately. Record setter side effects and
   restore the pre-test user state after each command group.
3. **Complete read and import.** Only with a released strict upstream API, read
   every mandatory block in one session, including playlist, clock settings,
   schedules and seven identified routines. Reject partial data. Preview and
   confirm import with restore suppressed; compare the saved normalized profile
   against a second complete fresh read.
4. **Manual restore.** Change one field per block at safe values, save one exact
   revision, power-cycle only Lumalou, and restore minimal diffs in the
   hardware-proven order. Require a complete fresh readback match and verify
   that Play, light-on, Nap and routine actions were not replayed.
5. **Recovery failures and automation.** Interrupt BLE before a write, between
   blocks and before verification. Confirm eventual convergence to one current
   revision, bounded backoff and no false verified state. Then enable
   auto-restore and run at least ten short/long Lumalou power cycles plus HA
   restart and clean Raspberry restart cases.
6. **External controller and Apple Home.** Enter Maintenance, edit through the
   browser, import with restore suppressed, leave Maintenance and repeat a
   power cycle. With separate approval, include only the Lumalou light and audio
   entities in HomeKit Bridge, reset/re-add cached accessories if required, and
   verify the documented light and switch-style audio controls.
7. **Prerelease and soak.** Install the generated ZIP through HACS as a custom
   repository on a clean test path, verify update/rollback and profile backup,
   then run a 72-hour soak. Stable release remains prohibited until this ledger
   records every required pass or an explicit documented limitation.

## Read-only observations

The browser reported the device in Soother mode with light and audio off,
brightness and volume at device level 5, clock enabled in 24-hour format at
brightness 2, and both routine reward sounds enabled. These values are runtime
observations, not an imported desired profile.

Routine and schedule views issued only documented request opcodes and returned:

- seven individually identified day-routine payloads, 14 bytes each;
- routine task status, 7 bytes;
- ready-to-rise and sleepy-time week payloads, 14 bytes each;
- ready-to-rise alarm payload, 4 bytes.

All time/routine bytes were zero on this device; alarm nibbles used the inactive
value. This establishes response lengths and per-day identity, but not nonzero
hardware behaviour, setter safety, or compatibility with another firmware
revision. A separate static audit found layouts in the deployed web bundle;
those codecs are not present in the released upstream library. No device
identifier or family schedule is recorded here.
