# Hardware validation ledger

Read-only browser inspection was run on 2026-09-16 against a Lumalou already
connected by the user to the upstream Web Bluetooth client in Chrome. No light,
audio, clock, schedule, routine, raw-opcode, or other write control was used.
The integration itself was not installed or connected.

On 2026-09-24, HACS installed development commit `709da59` from the source
branch after release-ZIP mode had caused a 404. Home Assistant Core restarted
and discovered one connectable device matching the integration's manufacturer
filter. The user-triggered standard GATT Device Information probe connected,
found no readable Model Number, and disconnected. The config flow refused to
create an entry. No Lumalou control command was sent. The product code remains
unconfirmed; do not enter `GLD09` based on the advertisement or product name.

Later the same day, a one-shot standalone diagnostic matched the HA-observed
advertisement, connected once, read only the approved FACTORY characteristic,
verified its Mattel ECDSA signature locally, and disconnected. The signature
authenticates a device identity; it does not establish a retail SKU or
cross-device compatibility. The token, signed identity fields, address and
numeric advertised name were not retained here. No Lumalou setting was
written. Enrollment must use explicit user confirmation, strict complete
profile compatibility, and private fingerprint binding for later sessions.

The local upstream candidate's strict client was then tested from the
development host against the same advertised device. A fresh read-only
GLOBAL_STATE request passed after accounting for exact target transport ACKs.
Separate fresh sessions returned CURRENT_PLAYLIST with a 12-byte ordered
payload (song IDs 1–12) and CLOCK_SETTINGS with a 2-byte payload matching the
independently observed display/brightness/format state. These reads did not
change settings. The strict client is still only an unpublished fork version;
the HA integration was not connected through it.

After adding the target-observed transport acknowledgements and the `0x19`/`0x99`
decoders to the local fork candidate, a final fresh-session read decoded every
modeled persistent block: 25 GLOBAL_STATE fields, 12 playlist slots, clock
settings, two seven-day time blocks, seven alarms and seven 12-slot routines.
Clock reply matched GLOBAL_STATE. Weekly values were midnight, all alarms were
inactive, and routine slots were empty; no schedule values are repeated here.
No settings write occurred. This remains a one-device candidate-library
result, not HA integration acceptance.

The same existing browser session was inspected again on 2026-09-17. Navigation
between read-only views caused schedule/routine queries but no reconnect and no
configuration command. The device emitted four-byte `CURRENT_DATE` payloads
through the local midnight boundary: `23:59:00`, weekday `03`, was followed by
`00:00:00`, weekday `04`. This supports a BCD hour/minute/second/weekday response
layout on this target only; product code and firmware are still unrecorded.

The already-connected session was rechecked read-only on 2026-09-21. It again
returned fresh `CURRENT_DATE`, seven separately identified 14-byte routine
responses, two 14-byte weekly-time responses, one 4-byte alarm response and a
7-byte routine-task response. The observed clock payload `16:40:00`, weekday
`01`, matched Monday in the configured local timezone. All schedule/routine
payloads remained empty; no control, form value or device setting was changed.

| Item | Status | Evidence |
| --- | --- | --- |
| Protocol compatibility | One-device read shape observed; model-wide support not established | Label and standard Model Number unavailable. A strict complete profile read passed on one target using an unpublished candidate library; no cross-device proof exists |
| Lumalou firmware | Not recorded | Standard Firmware Revision was not surfaced by the flow; passive advertisement may provide a candidate version, but must be validated |
| HA installation and version | Partially verified live | Raspberry Pi 4, HAOS with Core 2026.9.2 visible; historical Supervisor 2026.09.0 record needs recheck |
| Connectable adapter/backend | Verified for discovery | HA Bluetooth uses the Raspberry Pi's built-in bcm43438-bt adapter; one connectable advertisement reached Lumalou discovery. Connection and bounded disconnect worked for standard GATT read |
| Browser BLE connection | Observed | Upstream web client showed connected and received fresh state/date responses |
| Advertising after power-on | Not tested | Required for unattended recovery |
| Integration read-only onboarding | Partial pass | HA discovery and standard GATT read completed; missing Model Number stopped onboarding. A standalone FACTORY read verified the signature, but confirmed enrollment and strict profile compatibility have not been implemented and verified through HA |
| Candidate upstream read APIs | Partial hardware pass | On the development host, session/ACK handling, fresh GLOBAL_STATE, playlist length/order and clock response shape passed on the target; no setter or HA transport path used |
| Light/audio controls | Not tested on hardware | Setter side effects unknown |
| Browser schedule readback | Partially observed | Both 14-byte week blocks were all zero; 4-byte alarm block was inactive |
| Browser seven-routine readback | Partially observed | Seven distinct 14-byte day responses and a 7-byte task-status response were all zero |
| Full schedules and seven routines in Python | Read-only target-shape pass with candidate library | Fresh typed weekly, alarm and seven routine blocks passed; setters, persistence and cross-firmware behavior remain untested. Released upstream 0.1.0 lacks this API |
| Ten Lumalou power cycles | Not run | Acceptance test |
| 72-hour soak | Not run | Acceptance test |

Stable-release claims are prohibited until this ledger contains anonymized
results for the target device and the complete profile API is available.

## Authorization and stop conditions

Before the next live test, record the following in a private test session; do
not commit identifiers or family schedules:

- confirmation that the selected candidate is the user's Lumalou, a valid
  signed device identity, a strict complete protocol-compatible read and
  firmware version if available; the physical label and standard Model Number
  are unavailable;
- the user's existing authorization covers Lumalou tests, Core restart and
  HomeKit Bridge changes; physical power cycling must be done by the user;
- a current device baseline and the state needed to restore it after tests;
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

1. **Read-only target inventory.** Verify live HA/HAOS/Supervisor versions and
   Bluetooth adapter/backend. Close the Web Bluetooth session, confirm
   connectable advertising without Pairing, then read only the standard Device
   Information model/firmware characteristics. Do not treat `MB` advertising,
   the shared MPID service or a successful handshake as a product code. If an
   `GLD09` is returned, treat it only as a conflict check, not authorization.
   The 2026-09-24 probe returned no Model Number, so normal onboarding stopped.
   Verify the signed FACTORY token, confirm the candidate is the user's unit,
   then use only supported read requests to validate the expected full profile
   shape. Bind later sessions privately to the same signed device key; a
   different key or explicit conflicting model fails closed. No SKU guess or
   raw token/serial disclosure is needed. Confirm no device setting changes
   and no duplicate entry after reload.
2. **Minimal live controls.** After identity and baseline checks pass, test
   light at a low level, then short audio at a low volume.
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
  power cycle. Preserve the existing HomeKit Bridge
  mode and all existing exclusions. After HA registers the Lumalou entities,
  include only its light and audio media-player entity; explicitly keep
  Maintenance, buttons, selects and diagnostics out. Do not reset or re-pair
  the whole bridge as a routine step. Verify light on/off/brightness and
  switch-style audio on/off; fixed HA light effects are not a native Apple Home
  color control.
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

On 2026-09-24, a separate headed Playwright session opened the upstream web
client and invoked only its connection control. The page remained at
`scanning…`; no target chooser or connected state appeared, and all device
controls remained disabled. The test browser was closed without selecting a
device or invoking any setting control. This is not evidence that the target
was offline: the separate browser session did not inherit the user's prior
Web Bluetooth grant, and Codex's browser-tab API returned an unavailable auth
token. No device baseline was read and no Lumalou setting was written.
