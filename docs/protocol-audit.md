# Lumalou protocol audit

Audit date: 2026-09-16 (America/Argentina/Buenos_Aires).

Status: source audit and isolated synthetic checks only. No Lumalou or Home
Assistant installation was contacted. This document does **not** establish that
the integration is installable, functional, or ready for automatic restoration.
The library is an independent MIT-licensed reverse-engineering project, not a
Mattel library. Its documented model is GLD09; GWM53 compatibility is unverified.

## Pinned evidence

| Source | Audited identity |
| --- | --- |
| Upstream `main` | `9fa5ecfc7f6e82ec02e13d01f00fca7be6852567`, committed 2026-07-28 |
| Deployed web client bundle | `index-CP__DzxD.js`, SHA-256 `30bef51fe4ed6728ccd4a811b7f855368cc587d804a578660da368c2ece70b09`; source commit unknown |
| Annotated `py-v0.1.0` tag object | `9c275fac2daaf6f3c4abff4303553e8c4fb5dcca` |
| Release tag's commit | `b79ee9bee39aaf919b942c8388710d2856732f5a`, committed 2026-07-23 |
| Published dependency | `lumalou==0.1.0`, uploaded 2026-07-23; Python >=3.10 |
| Wheel SHA-256 | `b869ea08c7c832d8bda6d0f5e2be7274b807270d5d68382959b2ed98a700012a` |
| Source archive SHA-256, from PyPI metadata | `8248458d3e2400836e4bf2e6fa0dde3dd6b8b89a6d79c157438557db3528474a` |
| Customer HA / firmware / Bluetooth backend | Unknown; no compatibility claim |

The downloaded wheel's eight Python module files are byte-for-byte identical to
the audited `main`. The release-to-main diff changes only README/protocol prose
and adds the reversing write-up; no Python, JS, machine-readable specification,
or vector changes. No upstream commits after the specification's 2026-09-16
date were present at inspection. The package declares `bleak>=0.22` and
`cryptography>=41`; an exact top-level pin does not pin those transitive versions.
Sources: [release commit][release], [main commit][main], [PyPI release metadata][pypi].

## What exists, versus what is merely named

These capabilities are **confirmed by source**, not by this project's hardware
tests. `LumalouClient.send()` accepts arbitrary application bytes; that escape
hatch is not a supported, validated full-profile API. Only GLOBAL_STATE is
decoded and delivered by its notification handler. No other response reaches
the caller through the public client. [Client][client], [builders][commands],
[response decoder][responses].

### Existing write builders

| Area | Python builders and opcodes | Restoration implications |
| --- | --- | --- |
| Light | `set_light_color` 0x3C, `set_led_brightness` 0x3A, `set_light_duration` 0x6C, `turn_off_backlight` 0x3E | Colour/brightness/duration are potential profile fields; whether setters activate light needs hardware evidence. Off is a one-shot action. |
| Audio | `play_audio` 0x3F, `turn_off_audio` 0x38, `set_music_playlist` 0x40, `set_playlist_duration` 0x42 | Playlist/duration are configuration; Play/Off must never be queued for recovery. |
| Volume | `set_volume` 0x37, `set_routine_volume` 0x77 | Separate levels; allowed physical ranges are not specified by builders. |
| Clock | `set_current_date` 0x30, `set_clock_settings` 0x79 | Current time is computed afresh, never restored from a stored timestamp. |
| Sleep/Nap | `set_r2r_status` 0x44, `set_nap_alarm` 0x4F, `start_nap` 0x4D | Nap start is transient. Alarm configuration is not the same as an independent clock-time field. |
| Routine | `set_routine_status` 0x58, `start_routine_mode` 0x7B, `routine_control` 0x6B | Start, task movement, restart, complete and cancel are transient commands. |
| Aggregate | `set_global_on` 0x03, `set_global_state` 0x01 | Can affect active light/audio/routine state; not a safe bulk-restore primitive. |

The high-level client wraps only light colour/brightness/off/duration,
volume/play/audio-off/playlist, nap, global-on (`soother`) and time sync. Other
builders require calling `send()` explicitly. A completed TX write uses
`response=False`; it does not acknowledge successful configuration application.
The session bootstrap write uses `response=True`, which still is not profile
verification. [Client][client].

### Existing query builders

`commands.request(name)` supports precisely the following names. The response
opcodes below come from the specification; only the first has an implemented
Python payload decoder/client delivery path. [Builders][commands],
[opcode specification][spec].

| Name | Request → response (hex) |
| --- | --- |
| `global_state` | 53 → 02 |
| `current_date` | 31 → 13 |
| `toyic_fw_version` | 35 → 12 |
| `led_brightness` | 3B → 17 |
| `light_color` | 3D → 18 |
| `light_duration` | 6D → 95 |
| `volume` | 39 → 15 |
| `routine_volume` | 78 → 98 |
| `song_playing` | 55 → 14 |
| `music_playlist` | 41 → 19 |
| `playlist_duration` | 43 → 1A |
| `operation_mode` | 72 → 1E |
| `activity_state` | 74 → 1F |
| `current_stage` | 75 → 20 |
| `clock_settings` | 7A → 99 |
| `transmission_mode` | 76 → 1D |

### Configuration blocks missing from released libraries

The following opcodes are generated constants, **not** complete Python APIs.
The specification and published Python/JS libraries contain no typed payload
layouts/decoders for the seven daily routines or sleep/wake schedule blocks.
The deployed web client contains additional minified codecs, audited separately
below. Those codecs establish the web client's encoding rules, but are not an
API available to this integration and have no identifiable source commit.

| Block | Set | Request → response (hex) |
| --- | --- | --- |
| Ready-to-rise times | 46 | 47 → 22 |
| Sleepy times | 48 | 49 → 23 |
| Ready-to-rise alarms | 4A | 4C → 27 |
| Ready-to-rise status / alarm status | 44 / aggregate | 45 → 21 / 4B → 26 |
| Nap status / alarm status / alarm value | transient / aggregate / 4F | 4E → 1C / 50 → 24 / 51 → 25 |
| Routine mode / music status | 58 / 69 | 59 → 92 / 6A → 93 |
| Routine task status | 68 | No dedicated request named; response 94 |
| Sunday routine | 5A | 5B → 2B |
| Monday routine | 5C | 5D → 2C |
| Tuesday routine | 5E | 5F → 2D |
| Wednesday routine | 60 | 61 → 2E |
| Thursday routine | 62 | 63 → 2F |
| Friday routine | 64 | 65 → 90 |
| Saturday routine | 66 | 67 → 91 |

The non-contiguous Friday/Saturday response IDs matter: do not compute every
response as a single base plus weekday. There is no complete restore, import,
export, weekly snapshot or request/response transaction API in the released
libraries. The published JS package has the same limited builders/decoder; its
deployed web application has extra code not present there. [Specification][spec],
[JS builders][js-commands], [JS decoder][js-responses], [deployed bundle][web-bundle].

### Additional codecs in the deployed web client

The deployed client at the audited URL and hash implements these exact layouts:

- Ready-to-rise (`46 / 47 / 22`) and sleepy-time (`48 / 49 / 23`) weeks are
  14 bytes: seven Sunday-first BCD hour/minute pairs. `FF FF` means no time.
  The builder requires exactly seven entries, hours 0–23 and minutes 0–59.
- A daily routine is 14 bytes. Bytes 0–1 are BCD hour/minute or `FF FF`.
  Bytes 2–13 are up to 12 task slots; zero slots are skipped, the high nibble is
  the step number and the low nibble is task ID. The builder numbers steps from
  one, rejects empty steps, allows at most 12 tasks with IDs 0–11, and pads with
  zeros. Its clear value is `FF FF` followed by 12 zero bytes.
- The same verified bundle labels task IDs 1–11 as Get dressed, Wash up, Brush
  teeth, Bathroom, Backpack, Meal, Story, Tidy up, Heart, Swirl, and Star. Its
  task ID 0 entry is only an em dash and is not a proven selectable task label.
- Ready-to-rise alarms (`4A / 4C / 27`) are four bytes: seven Sunday-first,
  high-first alarm nibbles, followed by a sound nibble. Alarm values are
  `0=ACTIVE`, `1..8=AFTER_15..AFTER_120`, `9=INACTIVE`, `10=AFTER_1`; the
  builder accepts sound values 0–15. Web UI options do not prove that every
  accepted sound value works on hardware.
- Routine task status request opcode is `68` and response is `94`. The response
  is seven bytes: current-step byte followed by 12 high-first task-state nibbles.
  Task-state enum meanings were not found. The upstream specification calls
  `68` a setter, while the deployed client uses it as a request; this conflict
  must be corrected upstream before use.
- Clock settings SET payload is two bytes packing `[0, display, brightness,
  format]`, with brightness 0–9 and format `0=12h`, `1=24h`. Routine rewards SET
  is a music byte plus task-reward/routine-reward nibbles. Playlist SET contains
  12 song IDs; the web builder filters zero, truncates to 12 and pads with zero.

Important representation limits:

- all-zero daily routine decodes as midnight with no steps, not disabled;
- the web UI treats `00:00` as ambiguous and rewrites it to `FF FF` on save,
  but this is UI policy, not proof that the raw values are equivalent;
- routine decode groups and sorts steps, so decode/encode is not a byte-preserving
  backup for arbitrary raw layouts;
- the deployed client has no dedicated response parser for clock settings,
  rewards or playlist; it reads clock/rewards from `GLOBAL_STATE` and does not
  apply playlist response data;
- its `GLOBAL_STATE` parser silently zero-pads short data, so it cannot provide
  strict restore verification.

These codecs belong in upstream `lumalou`, with strict parsers and shared golden
vectors. The HA integration must consume a reviewed, exactly pinned release; it
must not copy protocol code locally merely because it appears in a web bundle.

## GLOBAL_STATE is not a backup

The decoder consumes 26 nibbles (13 bytes) and returns 25 fields: operation mode,
activity state, music status, current song (two nibbles), current volume, playlist
duration, light status/brightness/colour, nap status/duration, ready-to-rise
status/alarm status, time prescaler, current stage, clock display/brightness/
format, routine music status, task/routine reward sounds, light duration,
routine volume/mode status and executing alarm. [Decoder][responses].

Missing: full playlist order/content, current clock time, sleep/wake times,
alarm timing payloads and every day's complete routine. Runtime status fields
are mixed with configuration fields. Persisting this dict does not satisfy a
full profile, and replaying it can restart or alter active behaviour.

Known representation constraints from source:

- Fixed colours are 0–9; audio sources 0–7; song IDs 0–18, with 0 `NO_SONG`.
  These are not RGB, streaming or seek capabilities.
- Light duration enum: 15/30/60/90 minutes, continuous, 1 minute. Playlist:
  15/30/60/90/120 minutes, continuous, 1 minute. Preserve enum mappings, not their
  apparent numeric order.
- Nap enum 0 is inactive, but `Alarm.ACTIVE` is 0 and `Alarm.INACTIVE` is 9.
  Zero must not universally mean disabled. Other alarm values are relative
  offsets, not evidence of arbitrary independent alarm-time fields.
- Playlist builder filters zero IDs, truncates to 12 nonzero IDs and pads zeros.
  HA must reject overlength input before encoding, not silently lose entries.
- Brightness and volume builders wrap through `& 0xFF`; aggregate fields wrap
  through `& 0x0F`. Clock brightness also masks a nibble. Encodable range is not
  a confirmed valid hardware range. Validation must precede these builders.
- Aggregate `None` becomes the no-modify nibble 0xF, not a disable command.
- Time builder sends BCD hour/minute/second/weekday, no calendar date. `None`
  becomes 0xFF, but this does not establish a schedule-disable format. The client
  maps Python Monday=0 to device Sunday=0 and defaults to naive local time;
  HA must supply its own timezone-aware, trustworthy current time.

[Enums][spec], [encoding behaviour][commands], [time wrapper][client].

## Integrity, freshness and lifecycle defects

1. `request_state()` returns `_state` on timeout, including a previous session's
   cached value. There is no receive timestamp or freshness flag. A caller
   cannot distinguish a fresh result from this fallback.
2. Every accepted GLOBAL_STATE resolves every pending state waiter. The write
   lock serializes writes, not complete request/response transactions. An
   unsolicited or delayed notification can resolve an unrelated request.
3. State, waiters and sequence are not reset by reconnect. Disconnect does not
   fail pending waiters; timeout retains its cancelled waiter until a later
   state response. A send failure occurs outside the wait/timeout `try` block.
   There is no session-generation binding or disconnected callback updating
   `connected` when the remote drops the link. Setup failure cleanup is absent.
4. Short GLOBAL_STATE payloads are silently zero-padded. Extra data and unknown
   enum values are not rejected. A truncated response can therefore look like
   a valid factory-reset configuration.
5. RX checks body CRC, but ignores MPID header CRC and declared length.
   `parse_response_frame()` searches for FE, accepts truncated declared bodies
   and never checks the FE XOR checksum. RX sequence is returned by the frame
   function but not used for replay/duplicate rejection by the client.
6. A notification is treated as one frame; no demonstrated fragmentation or
   reassembly contract exists. Do not invent fragments without transport
   evidence. The MFG token helper slices fields without verifying its signature;
   describing the token as signed is not evidence of signature verification.

[Client implementation][client], [frame implementation][protocol],
[state decoder][responses], [token helpers][crypto]. These are concrete code
limitations; the audit does not claim an observed attack or damaged hardware.

Adding an external timestamp to the returned dict cannot repair missing frame
validation or distinguish cache fallback. A reliable coordinator requires an
upstream client contract change before it may mark a profile verified.

## Safety boundary

Allow only the documented main service `4cea0001-c678-4202-b5d3-712dbb5e5b14`:
read factory (`4cea0004-…`), subscribe RX (`4cea0003-…`), and write only session
(`4cea0005-…`, handshake) and TX (`4cea0002-…`, allowlisted frames). Handshake
requires ephemeral session setup and `ENABLE_RX`; read-only onboarding is not
literally zero GATT writes. It must still perform no configuration/time writes.
[GATT and handshake][protocol-doc].

Never access the DFU service `00001530-1212-efde-1523-785feabcd123`; do not expose
OTA, firmware, reset, arbitrary characteristics or raw-opcode actions. Explicitly
deny `SET_TIME_PRESCALER` (0x52) and `SEND_PAIRING_COMPLETE` (0x34), both marked
unsafe upstream. `OTA_COMPLETE` (response 0x11) and `FIRMWARE_UPDATE` (mode 5)
are names for interpretation, not permission to enter firmware mode. Unknown
commands are denied by default. [Upstream blacklist and unsafe commands][spec].

Released `send()` does not enforce this policy. The HA adapter now keeps raw
dispatch private, checks application opcodes, and wraps the pinned client's
transport so only factory read, RX subscribe, SESSION write and TX write exist.
Tests reject handles, characteristic objects, DFU UUIDs and arbitrary GATT APIs
before backend I/O. This version-specific private assignment hook must be
re-audited on every upstream upgrade; reusable enforcement still belongs
upstream. A colour/brightness setter must not be admitted to automatic restore
until its activation side effects and idempotency are demonstrated on hardware.
Do not replay global-on, Play/Off, Nap, routine start/control, or active-state
fields after reconnect. Do not use a cached aggregate setter for recovery.

## Required contract for the HA coordinator

This section is a **design requirement**, not an implemented upstream feature.
Repository policy keeps protocol and cryptography in the upstream package;
do not copy codecs into this integration, edit installed packages or monkeypatch
global clients to hide these gaps. Prepare a reviewed upstream extension and a
released dependency before enabling dependent features.

1. Accept the current HA-provided connectable `BLEDevice` through an explicit,
   tested API or connection factory; create a new client for every connection.
   Do not invoke `LumalouClient.scan()` or independent Bleak discovery. Use HA's
   scanner/callback lifetime and at least a 10-second connect timeout; 20 seconds
   is a provisional project default. [HA Bluetooth guidance][ha-bluetooth],
   [HA Bluetooth APIs][ha-api].
2. One coordinator owns one device session and a lock spanning each complete
   operation, not just TX. Entity methods never create connections. Each
   connection has a monotonic generation; invalidation fails pending requests,
   drops stale callbacks and clears old observations on disconnect/unload.
3. Typed queries return a validated response envelope containing block identity,
   session generation, receive sequence/time and decoded values. Timeout,
   disconnect, malformed payload and unsupported block are distinct failures;
   no implicit cached result. Cached observations are explicitly labelled stale.
4. Match exact response type and day. Establish RX sequence behaviour before
   relying on it. MPID RX sequence is not proven to echo the TX request ID.
   Serialize requests; after an ambiguous timeout do not let a late reply close
   the next same-type request. Without a proven correlation/drain mechanism,
   invalidate and re-establish the session before retrying that query.
5. Validate full framing and block schema before publishing observations.
   Reject unknown enums, invalid BCD, malformed lengths and missing blocks.
   A complete profile snapshot includes playlist, clock configuration, all
   supported sleep/alarm/routine parameters and seven individually identified
   days; each block must be obtained in the current session. Never convert an
   absent block to defaults or promote a partial snapshot to desired profile.
6. Keep immutable desired revision separate from observations. Import suspends
   automatic writes, reads all blocks, previews differences and requires user
   confirmation. Missing/corrupt stored profiles disable recovery writes.
7. Restore uses one saved revision, minimal changed blocks and a hardware-proven
   safe order. Read before any clock correction; compute fresh time from HA.
   Apply activation flags last only where supported and safe. After writes,
   obtain fresh complete readback and compare normalized persistent fields.
   TX success, a state callback or partial matching blocks never mean synced.
8. Do not automatically retry an ambiguous transient action. Retry only proven
   idempotent configuration writes, with bounded backoff. Preserve the upstream
   150 ms inter-write spacing as a starting lower bound, not a performance
   guarantee. Its handshake also waits 400 ms and 300 ms around ENABLE_RX.
9. Maintenance and import suppress recovery; pending profile edits survive HA
   restart. Recovery must not depend on UI subscribers. Cleanup cancels requests,
   tasks and callbacks and closes BLE even during failed setup or cancellation.
   Redact addresses, MFG tokens, session secrets and family schedules.

## Verification ledger

### Executed automatically in isolation

- Compared release tag to main; downloaded the PyPI wheel, checked its SHA-256
  and compared all eight package module files against main: identical.
- Ran upstream Python golden-vector tests: **20 passed**. The suite covers CRC,
  key derivation, command/frame encoding, RX decryption, response framing and
  GLOBAL_STATE fixtures. It does not test BLE sessions, schedules, persistence,
  safe restore or invalid-input rejection. [Audited upstream tests][tests].
- Ran synthetic in-memory assertions against the installed release wheel:
  empty GLOBAL_STATE produces 25 zero-valued fields; brightness/volume wrap;
  playlist truncates/filters; truncated FE with no valid checksum is accepted;
  incorrect MPID header length/CRC is accepted when body CRC is valid;
  request timeout returns cached state and retains its timed-out waiter.
- Audit execution environment: Python 3.10.20, Bleak 3.0.2, cryptography 50.0.1.
  This is not the target HA runtime, and no BLE connection was attempted.

Reproduction of the vector run, from a checkout of the pinned upstream commit:

```sh
uv run --no-project --with lumalou==0.1.0 --with pytest \
  python -m pytest -q packages/python/tests
```

Upstream `conftest.py` imports its checkout's `src`; the separate module-byte
comparison above establishes equivalence to the audited release. The synthetic
probes were audit experiments, not committed HA regression coverage.

### Local upstream remediation candidate

An unpublished worktree based on audited upstream commit `9fa5ecf` now contains
strict MPID/SSI/FE validation, session-bound response envelopes, exact-opcode
queries without cache fallback, cancellation-safe disconnect cleanup, and typed
codecs for weekly times, alarms, task status, and all seven daily routines. It
accepts only the source-backed `01 50` RX route and treats every previously
observed response opcode as ambiguous until a clean reconnect. Exhaustive route
tests cover all 65,536 two-byte prefixes, including unsolicited responses before
and during a request.

The candidate passes **389 Python tests** in the current local run; the earlier
360-test state also passed the Python 3.10, 3.11 and 3.12 matrix. The generated
JavaScript contract passes type checking, **20 tests**, and a
production/declaration build. All 28 source-backed read-only query opcodes are
exposed with literal request/response vectors. Strict playlist, clock and routine
music/reward SET models reject truncation/coercion; JavaScript has parity for the
schedule codecs. A strict four-byte `CURRENT_DATE` decoder follows the recorded
read-only midnight transition but remains transient and target-specific. This is
development evidence only: no commit was pushed, no pull request or release
exists, playlist and clock-settings response layouts remain raw, and no setter
has hardware acceptance.
The HA manifest therefore remains pinned to released `lumalou==0.1.0`.

### Automatable once the upstream contract is released

Strict malformed-frame/enum/BCD tests; complete block encode/decode round trips;
seven-day independence; playlist/task-order preservation; duplicate/reordered/
late RX tests; session invalidation and cancellation; denylist/allowlist tests;
all-or-nothing import; partial restore and revision conflicts; offline edits;
atomic durable-save failure handling; maintenance and recovery without listeners.
Golden vectors alone do not establish any of these guarantees.

### Requires explicit hardware acceptance

Exact label and firmware; supported HA/backend versions; actual advertising
matcher and connectability after power-on without Pairing; GATT discovery and
all block formats; numeric limits/disable semantics; setter side effects;
safe write order/idempotency/pacing; seven-day readback completeness; internal
schedule execution with HA stopped; reconnect preserving an active routine;
at least ten agreed Lumalou power cycles; partial-link-failure recovery;
72-hour soak and actual restore latency. None has been performed in this audit.

Do not infer reboot from BLE disconnect, absence of advertisements during an
active connection, or clock drift. Customer model/firmware/HA/backend must be
recorded before any configuration write. Full automatic recovery remains gated
on complete fresh readback and the customer's hardware evidence.

### Read-only Web Bluetooth observation

On 2026-09-16 the user supplied an already connected Chrome session running the
upstream web client. Navigating its read-only Routine, Schedule, and Settings
views produced fresh typed responses without invoking an output/configuration
control. This is hardware evidence for response identity and length only:

- every day routine response contained 14 bytes;
- `ROUTINE_TASK_STATUS` contained 7 bytes;
- `READY_TO_RISE_TIMES` and `SLEEPY_TIME_TIMES` contained 14 bytes each;
- `READY_TO_RISE_ALARM_TIMES` contained 4 bytes.

The observed routine/time payloads were all zero and the alarm block represented
inactive days. A zero hardware vector cannot validate nonzero field behaviour or
distinguish midnight from disabled. Static audit of the deployed bundle establishes
its packing rules, while the web UI still reports that ambiguity and rewrites the
whole week when saving. Device identifiers were not retained. This browser session
does not validate Raspberry Pi Bluetooth reachability or this integration.

[main]: https://github.com/stramanu/lumalou/commit/9fa5ecfc7f6e82ec02e13d01f00fca7be6852567
[release]: https://github.com/stramanu/lumalou/commit/b79ee9bee39aaf919b942c8388710d2856732f5a
[pypi]: https://pypi.org/pypi/lumalou/0.1.0/json
[client]: https://github.com/stramanu/lumalou/blob/9fa5ecfc7f6e82ec02e13d01f00fca7be6852567/packages/python/src/lumalou/client.py
[commands]: https://github.com/stramanu/lumalou/blob/9fa5ecfc7f6e82ec02e13d01f00fca7be6852567/packages/python/src/lumalou/commands.py
[responses]: https://github.com/stramanu/lumalou/blob/9fa5ecfc7f6e82ec02e13d01f00fca7be6852567/packages/python/src/lumalou/responses.py
[protocol]: https://github.com/stramanu/lumalou/blob/9fa5ecfc7f6e82ec02e13d01f00fca7be6852567/packages/python/src/lumalou/protocol.py
[crypto]: https://github.com/stramanu/lumalou/blob/9fa5ecfc7f6e82ec02e13d01f00fca7be6852567/packages/python/src/lumalou/crypto.py
[spec]: https://github.com/stramanu/lumalou/blob/9fa5ecfc7f6e82ec02e13d01f00fca7be6852567/spec/protocol.json
[protocol-doc]: https://github.com/stramanu/lumalou/blob/9fa5ecfc7f6e82ec02e13d01f00fca7be6852567/docs/protocol.md
[js-commands]: https://github.com/stramanu/lumalou/blob/9fa5ecfc7f6e82ec02e13d01f00fca7be6852567/packages/js/src/commands.ts
[js-responses]: https://github.com/stramanu/lumalou/blob/9fa5ecfc7f6e82ec02e13d01f00fca7be6852567/packages/js/src/responses.ts
[web-bundle]: https://lumalou.emanuelestrazzullo.dev/assets/index-CP__DzxD.js
[tests]: https://github.com/stramanu/lumalou/blob/9fa5ecfc7f6e82ec02e13d01f00fca7be6852567/packages/python/tests/test_vectors.py
[ha-bluetooth]: https://developers.home-assistant.io/docs/bluetooth/
[ha-api]: https://developers.home-assistant.io/docs/core/bluetooth/api/
