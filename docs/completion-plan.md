# Plan to complete ha-lumalou

Status baseline: 2026-09-24. This plan is derived from the technical
specification, private session notes kept outside Git, and
`implementation-status.md`. Recheck mutable upstream/HA state before executing
each deployment phase. A passing mock suite, a HACS download, and hardware
acceptance are separate results.

## Definition of done

A versioned HACS custom-repository release installs on the target HA, enrolls
the user's Lumalou without typing a model, reads and edits every supported
persistent block (including seven daily routines), and retains a private,
revisioned desired profile. After a Lumalou-only power cycle it reconnects,
sets correct local time, applies only necessary persistent changes, and proves
the full result with fresh readback. It never replays Play/Nap/Next or converts
an incomplete/reset read into a new desired profile. Light and audio work in
HA and through the existing Apple Home bridge. CI, clean installation,
rollback, ten power cycles, failure scenarios, and a 72-hour soak pass; the
public report contains no identifiers or family schedules.

## Critical path and gates

| Phase | Work | Exit evidence |
| --- | --- | --- |
| 0. Preserve and sanitize | Compare the dirty HA and upstream trees with their published heads; remove any device-specific identity material from code, tests, UI and docs; inspect staged diffs for secrets/identifiers. Put reviewed WIP on separate branches so HACS keeps using the known `709da59` branch. Keep the private spec and HA data outside public Git. | Another agent can clone both refs and reproduce their tests without access to these local worktrees. No device identifier appears in public changes. |
| 1. Enroll the selected device | Home Assistant presents a connectable candidate and the user confirms it is their Lumalou; no SKU is typed. Verify the signed FACTORY token, perform only supported fresh read requests to establish the expected protocol/profile shape, and bind future sessions to that same device using a private stable identity derived from its signed device key. An explicit conflicting GATT model or incompatible response rejects setup. A model-wide detector is not required for this user's single-device integration; never infer a retail SKU from a serial-derived value, numeric name, shared service, or handshake. | The confirmed unit sets up without a label/model entry, survives restart with the same signed identity, and rejects a different/invalid unit. A changed BLE address requires an explicit rediscovery/migration test before claiming automatic recovery. No raw identity appears in UI, logs, diagnostics or Git; public compatibility claims stay limited to tested hardware. |
| 2. Deliver strict protocol API | Reconcile the dirty upstream checkout with PR #2, especially additional target ACK forms and overlapping authentication changes. Finish typed fresh reads and safe setters, signature validation and per-session binding. Prefer an upstream/PyPI release; if upstream cannot release in time, assess the spec's small in-component extension option with license attribution and equal tests. Do not pin a Git branch or install code at runtime. | Immutable, HA-installable dependency/extension; upstream tests and HA boundary tests pass. The manifest/lock pin an exact released package (which may stay at 0.1.0 only if a self-contained extension supplies the missing API); the current four API failures disappear. |
| 3. Complete read-only HA setup | Integrate the released identity/read API into Bluetooth config flow and coordinator. Test duplicate discovery, reload, offline startup, missing/changed identity, and full fresh snapshot. Install a prerelease candidate through HACS and restart Core. Let the user preview and confirm the first private profile revision. | One entry with no SKU input; no setup writes to user settings; every required block is fresh and complete; a second independent HA read matches the saved normalized revision. |
| 4. Prove manual writes | Capture a private baseline. Test one safe persistent setter at a time on hardware, including light/audio side effects, playlist, clock settings, schedules and seven routines; restore baseline after each group. Establish a dependency-safe order (data before activation, current time before active schedules) and implement minimal-diff manual restore with revision lock and full readback. Keep one-shot commands out of desired profile. | Save → Lumalou-only power cycle → manual restore → fresh full comparison succeeds; no unintended light/audio/routine activation. Incomplete readback never marks a revision verified. |
| 5. Automate recovery | Enable auto-restore only after phase 4. Handle delayed advertising/Pairing limits, HA clock readiness, stale responses, interrupted writes, newer revisions, maintenance, external import and offline edits. Keep one serialized connection/recovery path with bounded backoff; verify HA reload, Core restart and orderly Pi reboot. | Ten short/long Lumalou power cycles and interruption scenarios converge to one current revision; no replay or false verified state; the device still executes schedules without HA or internet while powered. |
| 6. Apple Home | Use the existing HomeKit Bridge and preserve its exclude filter/exclusions. Export only actual Lumalou light and audio entities; check setup, names and commands on an Apple Home controller. Generic media player is expected to provide switch-style audio on/off, while light provides on/off/brightness. | Apple Home commands change the device and HA reports fresh state. Maintenance, editors and diagnostics are absent from Home. No bridge reset or re-pair is needed. |
| 7. Release and observe | Run locked lint/format/types/pytest, relevant HA/HACS validation, packaging and secret checks. Publish versioned prerelease with changelog and exact dependency pin; clean-install and rollback through HACS. Run the 72-hour soak with day change and real events, collect anonymized timings/errors, then tag stable only if all acceptance gates pass. | Green CI, installable artifact, private recoverable backup, public anonymized hardware report, documented limits and completed handoff. |

## Parallel work and ownership

- Critical identity/protocol and restore safety reviews: dedicated reviewers.
  Give each a bounded review or implementation area; one integrator resolves
  shared API decisions and hardware findings.
- HA flows, storage/restore integration and hardware execution: primary
  implementer with targeted review before enabling writes.
- Documentation, translations, HACS artifact checks, synthetic fixtures and
  routine CI work can proceed while identity research and upstream
  reconciliation run, provided they do not encode an unproven identity
  assumption.
- The device owner need only perform physical Lumalou power cycling and Apple
  Home observations that cannot be done remotely. Live tests, Core restarts,
  HomeKit Bridge changes and branch pushes require the owner's authorization
  and a privacy review; unrelated home-server configuration is out of scope.

## Stop and fallback rules

- A model-wide discriminator is optional for this user's device. Keep the
  user-confirmed per-device enrollment and private session binding as the
  delivery path. If its signature, key stability, protocol readback or
  conflicting-model checks fail, leave control disabled and report the exact
  missing evidence instead of guessing a model.
- If the device does not advertise/connect after power return without a
  physical action, record that hardware limit; a timer cannot satisfy automatic
  recovery.
- If a setter activates light, audio, Nap or a routine unexpectedly and no
  safe order is demonstrated, keep that part of automatic restore disabled.
- If upstream publication stalls, decide between waiting and the spec's
  isolated extension only after testing the full maintenance and packaging
  cost. Never ship a manifest pinned to an unpublished Git revision.

The 72-hour soak alone sets a three-day lower bound after implementation and
hardware acceptance. Signed-device binding, upstream release timing and live
device behavior determine the rest; a fixed completion date would be invented.
