# Changelog

## Unreleased

- Begin config-entry based Home Assistant integration.
- Add Home Assistant Bluetooth discovery without an independent scanner.
- Add persistent profile revision storage for the safely supported subset.
- Add standard light and media-player entities suitable for HomeKit Bridge.
- Add protocol audit and explicit hardware-validation gates.
- Publish valid current-session notifications without changing saved intent.
- Make profile commits cancellation-safe and require revision checks on import.
- Document schedule/routine codecs found in the deployed upstream web client.
- Prepare an unpublished upstream strict-readback and schedule-codec candidate;
  keep HA restore disabled until it is released and accepted on hardware.
- Add offline CAS editors for every modeled profile block, including ordered
  playlists, clock settings, weekly schedules, and all seven daily routines.
- Add local HACS brand assets and current metadata validation.
- Add a guarded, validation-gated GitHub prerelease workflow and HACS release
  archive metadata; placeholder version `0.0.0` cannot be published.
- Expose profile presence and last verified revision as diagnostics, and create
  an entry-scoped Home Assistant Repair when private profile storage is corrupt.
- Document target-observed, read-only `CURRENT_DATE` evidence and its strict
  unpublished upstream decoder.
- Add static type checking to local and CI validation.
- Replay Home Assistant's newest cached advertisement so reload can recover
  presence without waiting for changed BLE payload bytes.
- Declare fixed-palette light effects through the standard HA feature flag so
  normal service calls and HomeKit-facing state retain palette control.
- Add a strict passive-advertisement codec to the unpublished upstream branch;
  HA consumption remains blocked until a released version can be exact-pinned.
