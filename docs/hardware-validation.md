# Hardware validation ledger

Read-only browser inspection was run on 2026-09-16 against a Lumalou already
connected by the user to the upstream Web Bluetooth client in Chrome. No light,
audio, clock, schedule, routine, raw-opcode, or other write control was used.
The integration itself was not installed or connected.

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
| Full schedules and seven routines in Python | Blocked on release and hardware proof | A local upstream candidate has strict codecs and 313 passing tests; released upstream 0.1.0 still lacks them |
| Ten Lumalou power cycles | Not run | Acceptance test |
| 72-hour soak | Not run | Acceptance test |

Stable-release claims are prohibited until this ledger contains anonymized
results for the target device and the complete profile API is available.

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
