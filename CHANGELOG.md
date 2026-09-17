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
- Add static type checking to local and CI validation.
