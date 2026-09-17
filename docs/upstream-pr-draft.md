# Upstream pull request draft

Proposed title: `feat: add strict response sessions and schedule codecs`

## Summary

- add session-bound `ResponseEnvelope` reads with exact expected opcodes;
- remove cached success after timeout and invalidate ambiguous sessions;
- validate MPID declared length/header CRC/body CRC, the source-backed `01 50`
  receive route, and exact FE length/XOR;
- accept a caller-provided `BLEDevice`/Bleak factory and expose disconnect state;
- add byte-preserving codecs for weekly times, alarms, seven daily routines and
  routine task status;
- expose all 28 source-backed read-only queries in Python and JavaScript;
- add strict Python/JavaScript SET models for playlist, clock and routine
  music/rewards without inferring their standalone response layouts;
- add JavaScript parity for weekly schedules, alarms and seven daily routines;
- correct opcode `0x68` from a setter name to the source-backed request name;
- add shared literal read vectors and synthetic schedule vectors.

## Safety and compatibility

This intentionally changes unsafe `0.1.0` behavior. A timeout no longer returns
cached state, malformed `GLOBAL_STATE` is rejected instead of padded, and an
opcode already requested or observed in a session requires a clean reconnect.
No retry loop, restore workflow, DFU/OTA access, or setter behavior is added.

Playlist, clock and the additional scalar replies remain raw envelopes because
the pinned source bundle proves their identities but not standalone payload
layouts. The pull request does not claim hardware-certified backup or restore.

## Evidence

- Base: `9fa5ecfc7f6e82ec02e13d01f00fca7be6852567`.
- Source bundle SHA-256:
  `30bef51fe4ed6728ccd4a811b7f855368cc587d804a578660da368c2ece70b09`.
- Local commits: `bb59b87`, `cdc6f4b`, `4b9ae91` on
  `feat/strict-readback-schedules`.
- The final 360-test suite passes on Python 3.10, 3.11 and 3.12.
- JavaScript typecheck, 20 tests and production/declaration build pass.
- Code generation is deterministic and `git diff --check` passes.

## Review focus

1. Conservative one-observation-per-opcode session policy.
2. Exact SSI receive-route allowlist and treatment of non-FE notifications.
3. Cancellation-safe disconnect cleanup and callback generation isolation.
4. Routine slot preservation, `FF FF` versus midnight, and non-contiguous
   Friday/Saturday response IDs.
5. Whether the source-backed raw query surface should be released before more
   payload layouts receive hardware evidence.
