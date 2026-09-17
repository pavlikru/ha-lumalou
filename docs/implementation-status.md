# Implementation status

This ledger keeps the full technical specification in scope. A green mocked
test suite is evidence for adapter behavior only; it is not hardware acceptance
or a release claim.

| Requirement | Current evidence | Status / next proof |
| --- | --- | --- |
| HA Bluetooth discovery and one config entry per device | Config flow, address unique ID, explicit GLD09 label gate, passive HA callbacks, mocked duplicate/offline tests | Implemented in development; confirm the target label, advertisement and adapter |
| Serialized, cancellation-safe BLE lifecycle | Coordinator lock/generation, bounded teardown, no independent scanner, mocked stale/cancel tests | Implemented against released client limitations; verify on target HA |
| Desired profile separate from observed state | Immutable revision record, schema-v2 logical model and private per-entry Store | Full structure exists; partial intent remains explicitly incomplete |
| Durable revisions, previous value, CAS import | Atomic Store write plus independent readback, strict expected revision, fail-closed export, and backed-up v1→v2 migration | Implemented; corrupted-store recovery UX remains open |
| Fresh complete readback | Released `lumalou==0.1.0` exposes only unsafe partial behavior; unpublished upstream branch adds strict envelopes, schedule codecs and SET models | Blocked on upstream review/release, standalone response layouts, and hardware evidence |
| Full persistent profile | Schema v2 validates every source-backed persistent field/block without BLE codecs or invented defaults | Structurally modeled; fresh population, UI editing and verified application remain incomplete |
| Seven daily routines and weekly schedules | HA model preserves seven exact 12-slot routines; unpublished upstream Python/JS codecs pass exhaustive synthetic tests | Not yet wired into flows/coordinator or accepted on hardware |
| Manual import and restore | Versioned schema-v1/v2 JSON import/export exists; structural completeness gate is separate; restore action rejects execution | Import preview, fresh device import and verified restore remain incomplete |
| Automatic restore after power loss | Option is visible but cannot be enabled | Deliberately blocked until manual restore is freshly verified on hardware |
| Native controls and offline editing | Light, media player, light/playlist duration selects, maintenance switch, buttons and subset profile edits | Schedule/routine/clock editors and complete offline profile editing remain incomplete |
| Clock sync | Explicit HA-timezone action with weekday conversion and trust threshold | Mocked; DST/timezone and simultaneous reboot need hardware acceptance |
| Diagnostics and privacy | Redacted diagnostics plus offline revision/pending/sync/error entities; no addresses/raw payloads/session material | Implemented subset; Repair flow and verified-restore timestamps remain open |
| Apple Home | Standard light and generic media-player entities documented for HomeKit Bridge | Code path implemented; pairing/control must be verified on target HA and Apple Home |
| HACS packaging and CI | HACS metadata plus lint/type/test/hassfest/HACS/artifact jobs | Local checks pass; public CI and clean HACS installation require push/release |
| Hardware acceptance | Read-only browser lengths/state recorded without identifiers | Product label, firmware, HA Bluetooth backend, writes, power cycles and soak are missing |
| Public release | Local feature branches and commits exist; nothing pushed | Requires user authorization, upstream release, prerelease artifact, and acceptance report |

## Current local branches

- HA integration: `feat/lumalou-integration`.
- Upstream protocol candidate: `4b9ae91` on
  `feat/strict-readback-schedules` (all 28 source-backed queries, strict SET
  models and Python/JavaScript schedule codecs are exposed).

Neither branch has been pushed. The target Home Assistant configuration and the
device have not been modified by these development commits.
