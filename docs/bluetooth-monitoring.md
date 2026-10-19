# Bluetooth Remote ID integration

Status: a legacy-BLE dashboard source and bounded discovery test are implemented using existing Ubuntu BlueZ bindings. Synthetic Remote ID decoding/dashboard tests passed, and a real 20-second discovery test completed cleanly with 50 service-data updates at its last heartbeat and no Remote ID candidates. Real BLE Remote ID reception remains unverified. Wi-Fi channels 6 and 149 remain in the intended receiver plan; concurrent multi-source dashboard operation is now implemented as a separate launch mode; its hardware validation remains pending.

## Inspected laptop

On October 18 local time, the laptop exposed one Intel USB Bluetooth controller (`8087:0a2a`), `hci0`, alongside its Intel Wireless 3165 Wi-Fi adapter. BlueZ reported controller version 0x08 (Bluetooth 4.2), powered off and not discovering. `bluetoothctl`, `btmgmt`, `btmon` and `hciconfig` are installed. A bounded unprivileged `btmgmt info` query produced no usable report, so do not claim detailed LE-feature verification from that command. No Sniffle receiver appeared in USB inspection. The user subsequently powered Bluetooth on; read-only verification confirmed powered=yes and discovering=no before the approved live test.

Intel's specifications identify the Wireless-AC 3165 as Bluetooth 4.2. This is a candidate for legacy BLE advertising reception, subject to a real test; it does not supply Bluetooth 5 extended advertising or Coded PHY coverage. No Remote ID reception over Bluetooth has been demonstrated on this laptop. A generic newer Bluetooth marketing version also would not by itself establish controller/driver/scanner support for the required advertising formats.

Installed DroneCOT is pinned at 2.3.10. Its local package includes `ble_parse.py` (ASTM service-data decoding) and `ble_capture.py` (Sniffle wrapper); it does not include the newer onboard-HCI reader described in current upstream documentation. Bleak and dbus-fast are not installed in the project environment. Ubuntu system Python already provides dbus-python and PyGObject/GLib; the new project helper uses those existing modules as a normal user, with no dependency installation or system permission changes. Do not assume the current upstream `ble+hci://hci0` example works with this installed version, silently upgrade dependencies, or grant capabilities to Python to make an example run.

## Intended receiver layout

| Receiver | Intended task | Evidence / status |
| --- | --- | --- |
| Panda | Fixed Wi-Fi channel 6 | Mavic Remote ID decoding verified |
| Techkey | Fixed Wi-Fi channel 149 | Monitor mode/channel setting verified; retained in coverage plan; no packets observed during the simultaneous Mavic test |
| Internal Intel Bluetooth | Legacy BLE Remote ID | Bluetooth 4.2 controller powered on; live discovery tested, synthetic RID decoding tested; real RID transmitter test pending |
| Dedicated Sniffle-compatible receiver | BLE legacy plus extended/Coded PHY coverage | Not present; choose supported hardware/firmware before claiming coverage |

Keeping 149 is an operational coverage choice for additional transmitters, not evidence that all DJI models avoid it or that reception there has been field-verified. The prior simultaneous test remains recorded accurately. Standalone input modes remain available. The dual-radio launcher records separate files by default; its new --live mode starts the [combined detector](combined-detector.md).

## Proposed implementation stages

1. Retain fixed Wi-Fi receivers on 6 and 149. Introduce independent source lifecycles and per-source status/counters behind the existing explicit START/STOP controls. A source with no reports should remain visibly listening, without being labeled as a tested receive path. Label missing/failed sources explicitly instead of presenting complete coverage.
2. Implemented: reuse the installed BLE service-data parser with synthetic Basic ID, aircraft location and reported operator/system wire messages. Accept actual Remote ID advertisements, not arbitrary nearby Bluetooth device discovery as a drone detection. Preserve transport, receiver, observation time and signal metadata when available. Do not invent aircraft/operator locations for messages that lack them.
3. Implemented and tested with approval: bounded legacy-BLE discovery on the user-powered BlueZ controller. The helper never changes controller power. Inspect controller state first; stop only Scout's discovery session and restore any power change Scout made, accounting for other clients. Keep Wi-Fi capture interfaces and settings unchanged. Verify permission requirements without modifying system policy or Python capabilities.
4. Implemented: a project-local BlueZ D-Bus adapter using the documented Adapter1 API. A future raw-monitor or dependency-update path requires separate review. Current upstream distinguishes a raw HCI monitor reader from a D-Bus ServiceData reader; the latter can coalesce repeated advertisements and lose individual Remote ID message updates. Prefer the complete report path where safely available, and expose a limited/coalescing mode honestly. No upgrade is authorized merely by this plan.
5. For Bluetooth 5 long-range coverage, add a supported Sniffle-compatible receiver with matching firmware/host tools. Upstream Sniffle documents TI CC1352/CC26x2 hardware, Coded PHY and extended advertising. Do not assume a stock Zigbee dongle or arbitrary Bluetooth USB adapter works without the appropriate firmware and supported receive path.
6. Normalize all sources into the existing aircraft/reported-operator model, map, alerts and private history. Preserve source provenance. Correlate claimed Basic IDs carefully across different radio MACs; conflicting identities/positions must remain reviewable. Shared observations must not produce duplicate tracks or alert bursts, and late identity arrival must be tested. Stop/cancel every source on STOP before terminal radio restoration. Optional TAK forwarding stays independent and secondary.

The current session/alert logic must be deliberately tested for source restarts, missed messages, silent refresh, changed MACs, duplicate reports, conflicts and partial field updates. Repeated advertising data may carry a different Remote ID message type; do not suppress it just because the address repeats. Use synthetic and offline checks before hardware, followed by a known BLE Remote ID transmitter. The Mavic's observed Wi-Fi Beacon broadcasts do not establish it as a usable Bluetooth test source.

## First safe test / permission boundary

Offline BLE decoding and lifecycle tests have passed. The user powered on hci0 and approved proceeding with the explained 20-second discovery test. Future tests still need an explained, explicitly selected discovery session; opening the dashboard must not start one. Explain the effect on existing Bluetooth clients, show the exact bounded command and cleanup first, and keep private sudo authorization in the user's terminal if a raw monitor reader needs it. Do not start scanning or capture all HCI traffic as an automatic diagnostic; unrestricted HCI logs can retain unrelated sensitive Bluetooth activity. Store only necessary test data privately in ignored local-data and emit aggregate results.

No software was installed and Wi-Fi state was preserved. The user powered Bluetooth on; the approved test started and released only Scout's discovery session and left controller power unchanged. Nothing was committed or pushed.

## Official references checked

- [Intel Wireless-AC 3165 specifications](https://www.intel.com/content/www/us/en/products/sku/89450/intel-dual-band-wirelessac-3165/specifications.html): Bluetooth 4.2 hardware.
- [Bluetooth SIG assigned numbers](https://www.bluetooth.com/wp-content/uploads/Files/Specification/HTML/Assigned_Numbers/out/en/index-en.html): HCI version 0x08 mapping.
- [DroneCOT configuration](https://github.com/snstac/dronecot/blob/main/docs/configuration.md): onboard BlueZ versus Sniffle inputs, reader tradeoffs and documented legacy limitation of the onboard path. These are current upstream capabilities, not locally tested Scout functionality.
- [DroneCOT feeds](https://github.com/snstac/dronecot/blob/main/docs/feeds.md): supported input transports and BLE prerequisites.
- [BlueZ Adapter API](https://github.com/bluez/bluez/blob/master/doc/org.bluez.Adapter.rst): discovery sessions, LE filtering and duplicate/service-data delivery semantics.
- [NCC Group Sniffle](https://github.com/nccgroup/Sniffle): supported hardware, PHYs, extended advertising and firmware/tool compatibility.

## Run the legacy BLE dashboard

Stop any existing Scout server in its own terminal, then run as your normal user:

```sh
cd /home/jason/Projects/nofear-drone-locator
.venv/bin/python -m aetherlink_scout --live-bluetooth hci0
```

Open `http://127.0.0.2:8765` and press START. No sudo is needed on the tested Ubuntu setup. Opening/refreshing the page does not discover or decode devices. START runs an unprivileged bounded helper, with explicit legacy-only/coalescing coverage in the status display. STOP confirms process exit, releases Scout's discovery session and retains tracks with continuing freshness age. The user-powered controller remains on; other BlueZ clients are not deliberately stopped. Confirmed duration expiry renews the 120-second session without resetting track identity, counters or alert state; unexpected exit, adapter loss, power-off or cleanup failure stops renewal visibly. There is no Wi-Fi preparation or TAK output.

The helper uses `/usr/bin/python3` only to access already installed Ubuntu dbus/GLib bindings. Dashboard/decoding still use the project virtual environment. Neither Python has elevated privileges or added capabilities. The helper has its own lifetime limit in addition to its parent timeout. It filters LE discovery events for ASTM service data (UUID 0xFFFA, app code 0x0D); ordinary Bluetooth devices do not become drone tracks. Only actual ServiceData update/new-device signals are decoded; cached Device properties and RSSI-only updates are not replayed as fresh positions. Messages without position fields do not invent fixes.

The standalone commands select **one** live source: Wi-Fi or BLE. `--combined` selects all three prepared inputs. Simultaneous Wi-Fi 6/149 plus BLE is now implemented as the separate [combined mode](combined-detector.md); its hardware validation remains pending. Persistent history, operator-first details, alerts and retained freshness reuse the existing dashboard. Source error messages are sanitized, and BLE input lines and payload sizes are bounded.

For a separate bounded diagnostic, run normally:

```sh
bash scripts/capture_ble.sh
```

The selected discovery command is `timeout --signal=INT --kill-after=3s 20s /usr/bin/python3 -I -u scripts/ble_bluez.py --adapter hci0`. No power/pair/connect operations are performed. Raw Remote ID candidate events, if any, remain private in ignored `local-data/ble-capture-*`; unrelated device addresses/names/service bytes are not retained. Logs and events must not be uploaded or dumped into chat. Inspect aggregates offline.

## First live discovery result

The approved test ran 2026-10-19 01:34:04–01:34:24 UTC (October 18 local time), exiting with expected timeout status 124. It produced one ready event, 19 heartbeat/status events, and no Remote ID candidates or reader-log errors. The last heartbeat counted 50 ServiceData updates; that is not a count of unique devices or all RF advertisements and may omit events after that heartbeat. Zero RID candidates means no BLE RID was observed during this interval, not proof no nearby drone existed. The test had no known BLE Remote ID transmitter.

Read-only follow-up confirmed powered=yes, discovering=no, both USB Wi-Fi adapters still managed on channel 44 and Intel still the preferred uplink. Test files/directories were 0600/0700. A private aggregate result is in ignored `local-data/ble-capture-rBlMj9Np/summary.json`.

Validation includes real synthetic Basic ID/location/system decoding, malformed/non-RID rejection, partial-field merging, session/counter preservation across confirmed renewals, STOP, sanitized failures, oversized input bounds, cache/RSSI exclusion, power-loss handling, helper expiry/cleanup failures and CLI mode guards. The synthetic BLE browser check verified explicit START, decoded aircraft/operator map markers, coverage/counters, STOP and retained refresh without external requests. Existing Wi-Fi/map/history browser regressions also passed. Hardware BLE RID decoding, extended/Coded PHY coverage and concurrent multi-source hardware behavior remain untested; synthetic combined tests are documented separately.

Final regression check: all 69 Python tests passed, along with `tests/browser_ble.py`, the existing `tests/browser_live.py`, three Node state/location tests, JavaScript/shell syntax and `git diff --check`. Temporary test servers exited. These checks do not substitute for a known real BLE Remote ID transmitter test.

## User dashboard test with Mavic 4 Pro

After the user tested BLE mode and reported no detections, read-only dashboard health showed stopped/inactive detection, one bounded segment, 273 BLE service-data updates, zero RID candidates, zero decoded reports and zero decode errors. The reader's private log was empty. Bluetooth remained powered on and discovery had ended. The user confirmed the test aircraft was the Mavic 4 Pro.

This establishes ordinary BLE service-data activity during the scan, not BLE Remote ID transmission from the Mavic. The earlier Mavic Wi-Fi reception remains independently verified. BLE mode selects a Bluetooth-only input, so it cannot display the aircraft's Wi-Fi reports. A known legacy-BLE Remote ID transmitter is still needed for a real positive BLE decoding test; do not classify arbitrary Bluetooth devices as drones to make a test appear successful.

The counters now prominently show BLE service-data updates separately from RID candidates and decoded reports, avoiding the impression that zero candidate packets means no Bluetooth activity. The synthetic BLE browser test passed with the distinct counters. Aggregate notes are private in ignored local-data/ble-test-diagnostics. No discovery session, power change, commit or push was performed during this diagnostic inspection.
