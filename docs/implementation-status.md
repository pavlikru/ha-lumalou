# Implementation status

This ledger keeps the full technical specification in scope. A green mocked
test suite is evidence for adapter behavior only; it is not hardware acceptance
or a release claim.

| Requirement | Current evidence | Status / next proof |
| --- | --- | --- |
| HA Bluetooth discovery and one config entry per device | Local flow confirms a candidate without model entry, verifies its signed key and stores a private fingerprint; repeat sessions bind to that fingerprint; an existing BLE address is rejected before another probe | Complete typed profile read unlocks control; all behavior is mocked locally against an unreleased upstream worktree. Reconfigure can rebind a changed BLE address after fingerprint verification, but unattended address-change recovery and live HA setup remain unverified |
| Serialized, cancellation-safe BLE lifecycle | Coordinator lock/generation, bounded teardown, no independent scanner, one read-only recovery loop with 30-second-to-15-minute exponential backoff, and mocked stale/cancel/maintenance-reset tests | Implemented against released client limitations; verify timing on target HA |
| Desired profile separate from observed state | Immutable revision record, schema-v2 logical model and private per-entry Store | Full structure exists; partial intent remains explicitly incomplete |
| Durable revisions, previous value, CAS import | Atomic Store write plus independent readback, strict expected revision, fail-closed export, backed-up v1→v2 migration, and a confirmation-gated Repair that preserves corrupt bytes before importing a validated backup | Implemented offline; recovery from an exported profile is tested through the HA Repairs manager |
| Fresh complete readback | Released `lumalou==0.1.0` exposes only unsafe partial behavior; the upstream candidate adds strict envelopes, schedule codecs, typed playlist/clock reads and signed device-key binding | Upstream PR head `e030bfd` passed 700 Python tests on 3.12; HA passed 395 tests, 91.69% coverage against that temporary local build. The pinned release still lacks the API and yields four boundary failures. One target returned modeled persistent blocks via a standalone read; full read through HA, release and verified restore remain |
| Full persistent profile | Schema v2 validates every source-backed persistent field/block; a public coordinator CAS API merges and durably saves offline logical changes without BLE codecs or invented defaults | Offline editors cover every modeled block; fresh population and verified application remain incomplete |
| Seven daily routines and weekly schedules | Native multi-step options editors preserve null versus midnight, alarm offsets/sound, seven exact 12-slot routines, unnamed task ID 0, independent routine and schedule copy-to-days, and a captured-revision CAS save | Offline UI implemented with mocked tests; no BLE write or hardware acceptance exists |
| Manual import and restore | First-run Create/Import/Read choice follows entry creation; versioned schema-v1/v2 JSON import/export has an offline preview, explicit confirmation and CAS save; coordinator obtains a strict fresh snapshot and produces a revision-bound changed-block preview without writes; strict device restore still rejects execution | Exported-profile import and read-only restore preview are mocked; hardware-verified restore remains blocked pending upstream release and setter acceptance |
| Automatic restore after power loss | Option is visible but cannot be enabled | Deliberately blocked until manual restore is freshly verified on hardware |
| Native controls and offline editing | Light, media player, light/playlist duration selects, maintenance switch, buttons, and schema-v2 offline editors for every modeled block with explicit CAS confirmation | Home Assistant options flows have no native drag-reorder control, so playlists and routines use 12 fixed ordered rows; device application remains incomplete |
| Clock sync | Explicit HA-timezone action with weekday conversion and trust threshold | Mocked; DST/timezone and simultaneous reboot need hardware acceptance |
| Diagnostics and privacy | Redacted diagnostics plus offline presence/revision/verified-revision/pending/sync/error entities; an entry-scoped fixable Repair imports a validated backup only after preserving unreadable private storage; no addresses/raw payloads/session material | Verified-restore timestamps remain blocked with restore; target UI must be checked |
| Apple Home | Standard light on/off/brightness and switch-style generic media-player on/off; config/diagnostic entities are categorized out of default HomeKit export; runbook preserves bridge filter/exclusions | Home Assistant 2026.9.2 accessory dispatch/filter contracts have mocked tests; pairing/control and actual entity list still need verification on target HA and Apple Home |
| HACS packaging and CI | HACS downloads branch source for development tests; CI passed at `709da59`; validation and release share a deterministic root-layout ZIP builder; clean archive extraction imports under local HA 2026.9.2; tag workflow rejects placeholder/mismatched versions and publishes only prereleases | HACS source download and Core restart passed live; release still pins incompatible `lumalou==0.1.0`; no stable-release or complete hardware claim |
| Hardware acceptance | Read-only browser lengths/state, live HA discovery/GATT probe, signed FACTORY read and full typed snapshot via candidate library recorded without publishing identity fields | Firmware, confirmed enrollment, strict profile read through HA, first low-output control acceptance, setter semantics, power cycles and soak remain |
| Public release | HA and upstream feature branches are published in forks; no HA integration release exists | Requires versioned prerelease artifact and an explicit acceptance report; stable release requires hardware acceptance and complete upstream API |

## Current local branches

- HA integration: `feat/lumalou-integration`.
- Upstream protocol candidate: public PR head `e030bfd` on
  `feat/strict-readback-schedules`; original local checkout is at `655aa7d` with six
  modified files and an untracked lockfile. Compare the overlapping changes
  before carrying them forward.
- Isolated upstream branch `codex/signed-device-fingerprint` starts from
  earlier PR head `1437dcc`; its head `e030bfd` is now pushed to PR #2 and
  contains signed device-key binding without the serial-suffix API.

Only the earlier HA branch state is published. Its 28 modified tracked files
and four untracked files remain local. HACS installed `709da59` on the target
HA and Core restarted. The device was discovered and a read-only standard
GATT connection completed, but no Model Number was available; no config entry
or control session was created. The locked local test run has 379 passes and
four failures caused by missing upstream identity APIs as of the earlier
2026-09-24 run. On 2026-09-25, the revised HA worktree passed 395 tests against
the isolated upstream candidate, while the locked released dependency gave
391 passes and the same four boundary failures. Reconfigure now accepts a
changed BLE address only after matching the private signed fingerprint.
See `completion-plan.md` for the remaining blockers and acceptance gates.
