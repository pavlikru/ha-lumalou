# Implementation status

This ledger keeps the full technical specification in scope. A green mocked
test suite is evidence for adapter behavior only; it is not hardware acceptance
or a release claim.

| Requirement | Current evidence | Status / next proof |
| --- | --- | --- |
| HA Bluetooth discovery and one config entry per device | Config flow, address unique ID, explicit GLD09 evidence gate, user-triggered read-only standard GATT Model Number probe, passive HA callbacks, mocked duplicate/offline tests | Implemented in development; confirm the target through label or exact standard Model Number, advertisement and adapter |
| Serialized, cancellation-safe BLE lifecycle | Coordinator lock/generation, bounded teardown, no independent scanner, one read-only recovery loop with 30-second-to-15-minute exponential backoff, and mocked stale/cancel/maintenance-reset tests | Implemented against released client limitations; verify timing on target HA |
| Desired profile separate from observed state | Immutable revision record, schema-v2 logical model and private per-entry Store | Full structure exists; partial intent remains explicitly incomplete |
| Durable revisions, previous value, CAS import | Atomic Store write plus independent readback, strict expected revision, fail-closed export, backed-up v1→v2 migration, and a confirmation-gated Repair that preserves corrupt bytes before importing a validated backup | Implemented offline; recovery from an exported profile is tested through the HA Repairs manager |
| Fresh complete readback | Released `lumalou==0.1.0` exposes only unsafe partial behavior; unpublished upstream branch adds strict envelopes, schedule codecs, SET models, and a target-evidenced transient `CURRENT_DATE` decoder | Blocked on upstream release, remaining standalone response layouts, and hardware evidence |
| Full persistent profile | Schema v2 validates every source-backed persistent field/block; a public coordinator CAS API merges and durably saves offline logical changes without BLE codecs or invented defaults | Offline editors cover every modeled block; fresh population and verified application remain incomplete |
| Seven daily routines and weekly schedules | Native multi-step options editors preserve null versus midnight, alarm offsets/sound, seven exact 12-slot routines, unnamed task ID 0, independent routine and schedule copy-to-days, and a captured-revision CAS save | Offline UI implemented with mocked tests; no BLE write or hardware acceptance exists |
| Manual import and restore | First-run Create/Import/Read choice follows entry creation; versioned schema-v1/v2 JSON import/export has an offline preview, explicit confirmation and CAS save; strict device Read and restore reject execution | Exported-profile import is implemented; fresh device import and verified restore remain blocked |
| Automatic restore after power loss | Option is visible but cannot be enabled | Deliberately blocked until manual restore is freshly verified on hardware |
| Native controls and offline editing | Light, media player, light/playlist duration selects, maintenance switch, buttons, and schema-v2 offline editors for every modeled block with explicit CAS confirmation | Home Assistant options flows have no native drag-reorder control, so playlists and routines use 12 fixed ordered rows; device application remains incomplete |
| Clock sync | Explicit HA-timezone action with weekday conversion and trust threshold | Mocked; DST/timezone and simultaneous reboot need hardware acceptance |
| Diagnostics and privacy | Redacted diagnostics plus offline presence/revision/verified-revision/pending/sync/error entities; an entry-scoped fixable Repair imports a validated backup only after preserving unreadable private storage; no addresses/raw payloads/session material | Verified-restore timestamps remain blocked with restore; target UI must be checked |
| Apple Home | Standard light on/off/brightness (fixed-palette HA effects are not native HomeKit colors) and switch-style generic media-player on/off are documented; the runbook preserves the existing bridge/exclude filter and forbids routine whole-bridge reset | Code path implemented; pairing/control and entity-scoped filter change must be verified on target HA and Apple Home |
| HACS packaging and CI | HACS downloads branch source for development tests; lint/type/test/hassfest/HACS/artifact jobs passed at `4e3c104`; validation and release share a deterministic root-layout ZIP builder; tag workflow rejects placeholder/mismatched versions and publishes only prereleases | HACS download failed with ZIP mode before any release asset existed; branch-source fix requires push and live retry. CI/archive success does not prove installation |
| Hardware acceptance | Read-only browser lengths/state recorded without identifiers | Exact model (label inaccessible, standard GATT probe pending), firmware, HA Bluetooth backend, writes, power cycles and soak are missing |
| Public release | HA and upstream feature branches are published in forks; no HA integration release exists | Requires versioned prerelease artifact and an explicit acceptance report; stable release requires hardware acceptance and complete upstream API |

## Current local branches

- HA integration: `feat/lumalou-integration`.
- Upstream protocol candidate: `648eb86` on
  `feat/strict-readback-schedules` (all 28 source-backed queries, strict SET
  models, Python/JavaScript schedule codecs, and strict transient current-clock
  and passive-advertisement decoding are exposed).

Both branches are published. The Lumalou device has not been modified by these
development commits. HACS custom-repository registration and one failed
download attempt occurred on the target Home Assistant; no integration setup
or device connection has been verified.
