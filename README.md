# AetherLink Scout

Primary prototype: drone Remote ID → local laptop map, aircraft/reported operator details and alerts. Optional secondary output: TLS CoT → TAK Server → TAK Aware.

The application name is **AetherLink Scout** and its launch module is `aetherlink_scout`. Existing repository and Windows mount paths retain their original names.

## Continuous detection and simulation

Continuous input, receiver status, **START/STOP**, and audible new-track alerts are implemented. Synthetic and saved-input checks passed. The latest live Mavic test decoded 804 reports with zero decode errors and reached four capture renewals on 2437 MHz; the user confirmed hearing the tone and seeing the expected positions and information. The user subsequently pressed STOP and confirmed counters stopped increasing; a read-only check corroborated stopped/inactive detection. TAK remains disabled.

The first continuous hardware attempt received 2,426 Wi-Fi packets but decoded no Remote ID, with no kernel drops or decode errors. Afterward Panda was observed in monitor mode on channel 44; the successful earlier recording contained all 185 Remote ID reports at 2437 MHz (channel 6). This is a channel-mismatch lead, not proof of the frequency throughout the failed test. START now requires channel 6/2437 MHz before capture and the receiver records radiotap frequencies, stopping with a clear error if live packets indicate another frequency. The UI shows expected channel and last packet frequency. A manual retune subsequently returned success while `iw` still reported channel 44; the launcher now checks the actual reported channel before starting and restores Panda on mismatch. A subsequent attempt to retune while down returned EBUSY and left Panda down. After bringing it up, the user successfully retuned Panda through channel 1 to channel 6; a read-only inspection confirmed monitor mode at 2437 MHz with Intel retaining the default route. [Panda troubleshooting](docs/panda-capture.md#channel-mismatch-retry) records the successful manual sequence and live UI result. The cause of the original channel mismatch remains unverified.

Try the controls with the saved hover-test recording, without changing Wi-Fi (stop any existing server before launching):

```sh
.venv/bin/python -m aetherlink_scout --simulate local-data/panda-capture-DyUwMhd1/capture.pcap
```

Open **http://127.0.0.2:8765** and press **START**. The **STREAM SIMULATION** banner stays visible; source packet times remain historical. Reports arrive at recorded timing after a two-second startup interval. `--simulation-speed 20` accelerates simulation only. `--simulate-demo` uses fictitious aircraft instead. Nothing is sent to TAK and no radio capture runs in simulation.

- START starts a fresh detection session and arms browser audio through that button click. One four-second sequence of eight alternating tones and a visible alert are generated for newly observed transmitter tracks. Repeated reports do not retrigger the sequence. A new detection replaces any currently sounding sequence; sounds never stack. Mute, STOP and acknowledgement silence it immediately. Replay pause/seek/restart also cancel it. **Sound on** mutes; **Enable sound** arms it again. Check laptop volume; browser audio may require a fresh click after page refresh or suspension. Background OS notifications are not implemented.
- STOP stops input processing/capture and retains positions, which keep aging. Track/position freshness uses monotonic reception time, separately from source packet time and current laptop location. Positions become stale after 30 seconds without updates. A new START resets the session and tracks; simulation restarts the recording.
- Receiver status distinguishes ready/stopped, waiting for input, running with no reports, completion and errors. A broken browser stream shows **receiver state unknown**; the receiver is not silently stopped by refresh, tab close or connection loss. Refresh/reconnect restores retained tracks without alert bursts; known reports are not re-alerted.
- In-memory storage is bounded to 512 tracks, 500 trail points per track, 2,048 pending report events and 50 alerts; event lag beyond that window resynchronizes a snapshot silently. Default views make no external requests. The app pushes one-way updates using [FastAPI streaming responses](https://fastapi.tiangolo.com/advanced/custom-response/#streamingresponse).

For live use, see [the launcher procedure and exact privileged commands](docs/panda-capture.md). `bash scripts/capture_panda.sh --live` accepts connected/disconnected managed Panda or an already prepared monitor radio. It preserves Intel connectivity, uses the tested channel-1 then channel-6 workaround only when retuning is needed, and verifies 6/2437 MHz before launching normal-user Python. START now listens **until STOP**: confirmed 120-second capture expiry renews the subprocess without resetting tracks, counts or alerts. Renewal rechecks the radio, route and authorization; other exits stop detection with an error. A brief reception gap can occur between segments. STOP cancels renewal and leaves Panda prepared. Ctrl+C stops the app before returning Panda to managed mode/NetworkManager and restoring a known original connection. The launcher refreshes cached sudo noninteractively every 45 seconds while open; it never stores passwords or changes sudo policy. Diagnostics remain in ignored local logs; live raw packets are not archived. The 60-second recording procedure remains available.

Stop the existing server with Ctrl+C in its own terminal, then run:

```sh
cd /home/jason/Projects/nofear-drone-locator
sudo -v
bash scripts/capture_panda.sh --live
```

Open **http://127.0.0.2:8765**. Opening or refreshing the page does not start detection; use START. The local testing-location preset still loads automatically.

### Clear old tracks and history

**Clear old tracks** removes aircraft/operator pairs whose transmitter has not reported for 30 seconds. Active pairs and My Location stay visible; detection and counters continue. Fresh reports can make a cleared transmitter reappear without another alert in the same retained session. Saving failure leaves the map unchanged.

**History** keeps the latest 1,000 track/session snapshots in ignored `local-data/scout-history.sqlite3`, private on creation. Reports update at most once every five seconds per active track, and flush on STOP, capture completion, clearing and eviction. A new START creates a separate session entry; capture renewal keeps the existing entry. These are latest-position snapshots with bounded trails, not every received packet. History pages show 100 entries with Older/Newer controls. Selecting an entry shows saved operator/aircraft details and dashed historical markers; **Return to live view** returns to current detections. Historical address/directions actions stay explicitly labeled. Simulation history is memory-only and does not enter the live database. One retained snapshot from the earlier successful Mavic live test was preserved locally before the upgrade.

Verification: `.venv/bin/python -m unittest discover -s tests -v`, `node tests/live_state.mjs`, `.venv/bin/python tests/browser_live.py`, and `.venv/bin/python tests/browser_audio.py`. Synthetic subprocess tests cover repeated expiry, cumulative counts/track identity, STOP during renewal/start, authorization failure and unexpected exit. Mock launcher tests cover managed/disconnected/monitor states, channel failures, preflight protection and app-before-radio cleanup. History tests cover private persistent storage, updates, retention/pagination, origin guards and saving failures. The isolated browser test on port 8768 covers sound calls, controls, paired selection, refresh, stale-only clearing, historical map review and mobile layout; screenshots are synthetic only. Hardware reception and continuous renewal are now observed: the October 4 Mavic test decoded 804 reports with zero decode errors, retained one track, and reached five capture segments (four renewals) after roughly nine minutes of listening. Four completed segment logs reported zero kernel drops. The user confirmed hearing the tone and seeing positions/information as expected. Manual STOP was subsequently confirmed by the user and stopped/inactive status was observed. The user also confirmed clearing old tracks and reviewing saved History worked, and reported the updated four-second alert working well. Repeated START, history across a server restart and launcher exit restoration still need field confirmation.

## Laptop replay dashboard

Launch using your authorized capture:

```sh
cd /home/jason/Projects/nofear-drone-locator
.venv/bin/python -m aetherlink_scout --capture /home/jason/mavic4pro-5min-test.pcap
```

Open **http://127.0.0.2:8765** on the laptop and select **Play replay**. That address is dedicated to this project and bound to IPv4 loopback; no LAN/Tailscale exposure. Stop the process with Ctrl+C. If the port is occupied, choose `--port 8767`; `--host` accepts only IPv4 loopback addresses.

For fictitious data with two transmitters and visible track expiry:

```sh
.venv/bin/python -m aetherlink_scout --demo
```

Features implemented:

- Aircraft and reported operator markers, aircraft trails, selectable details, per-field ages and stale markers.
- Play/pause, restart, speed selection and timeline seeking. Capture-clock timestamps remain historical.
- A visible alert once per transmitter track per playback, acknowledgement and optional browser sound (off initially). Seeking is silent; restart starts a new playback.
- A 30-second replay stale threshold and 35-second post-capture tail so expiry is visible. No reports received does not establish absence of drones. This prototype groups by transmitter MAC, falling back to Basic ID; IDs are unverified and not definitive counts of physical aircraft/people.
- Decoded positions are used directly with seven-decimal coordinate display; CoT truncation does not affect this dashboard. Unknown/sentinel altitude, zero-pair/invalid coordinates and missing fields are not plotted as valid fixes. Altitude is reported HAE, not height above ground; accuracy codes are not metres and RSSI is not distance.

This mode is **historical replay only**. It starts no capture worker and sends no CoT. Continuous modes are described above; background desktop notifications, field basemaps and optional TAK forwarding remain future work. The input path is chosen at startup; the browser has no file-reading endpoint. Detection controls are unavailable in replay mode. Replay datasets remain in process/browser memory and are not written into tracked files. Reports and IDs appear locally on screen; avoid sharing screenshots of real captures.

The offline coordinate map works without external requests. **Enable street map** optionally requests visible tiles from OpenStreetMap; that reveals the viewed area to the tile service. No reported IDs are sent. Tiles are not bulk-downloaded or packaged as offline maps. Leaflet 1.9.4 is vendored locally with its license. See [Leaflet documentation](https://leafletjs.com/reference.html), [FastAPI documentation](https://fastapi.tiangolo.com/advanced/custom-response/) and [OSM tile policy](https://operations.osmfoundation.org/policies/tiles/).

Runtime packages are pinned in `requirements.txt`; the full installed development environment is recorded in `requirements.lock.txt`. Verification tools are in `requirements-dev.txt`:

```sh
.venv/bin/python -m unittest discover -s tests -v
node tests/replay_state.mjs
```

Browser verification (`tests/browser_dashboard.py`) expects a synthetic server on `127.0.0.2:8766` and the historical Mavic server on `127.0.0.2:8765`. It uses Playwright/Chromium and saves only synthetic screenshots under ignored `local-data/dashboard-verification/`. Verified map markers, real-capture coordinate display, controls, alerts, acknowledgement/sound activation, stale positions, mobile layout, and zero default external requests.

### My location, operator address and driving directions

The right panel prioritizes **reported operator details → location actions → aircraft details**. Selecting a report in the aircraft list centers its operator position when available. Clicking either its aircraft marker or reported operator marker selects that same track and updates both right-side detail sections, address lookup and driving directions. Switching markers selects another pair; the list also works when map markers overlap. This remains reported Remote ID, not independent identification of a controller or person.

- **My location testing preset** loads automatically from the optional, ignored `aetherlink_scout/static/local-location.json` file (`{"lat": LATITUDE, "lon": LONGITUDE}`). The user-requested point is configured locally; exact coordinates stay out of source and documentation. It is labeled **FIXED**, **Testing preset**, **not GPS**, with accuracy unknown. A live map without reports initially centers on it. It remains a fixed point until changed; it is not aged as a GPS fix.
- **Use browser location** requests permission only when clicked. It draws a blue current-location marker and a reported accuracy circle, separate from Remote ID points. It may use an online browser location provider and is not necessarily a GPS fix. **Stop browser location** stops updates. Browser fixes are transient and become stale after 60 seconds; refresh restores the saved manual point or testing preset. No browser fix is saved or forwarded to TAK.
- **Enter coordinates manually** works offline without GPS/browser location permission and remembers the fixed point in this browser across refreshes. Accuracy is unknown. **Center on me** shows that point. **Clear** removes the marker and remembers that choice, suppressing the preset on refresh. Browser storage failure is reported and does not stop detection. Manual edits stay browser-local; the preset file is served only by the loopback dashboard. A future USB GPS/gpsd input is not yet implemented.
- **Driving directions** opens Google Maps only after the button and confirmation, using the reported operator coordinates as destination. It includes your fixed manual point or a fresh browser estimate as origin; otherwise Google Maps asks for a starting point. Fixed origins and historical destinations are explicitly identified. No route is requested automatically or embedded in this prototype. See [Google Maps URLs](https://developers.google.com/maps/documentation/urls/get-started).
- **Look up nearby address** pauses replay and queries only when requested. A result is a nearby mapped address, not proof of the operator's physical address. Results are escaped as text and cached for the exact queried point; moving to a different point cannot reuse the old address. No address requests happen during playback automatically.

The selected online provider is **Geoapify**. Create a project/key in [Geoapify MyProjects](https://myprojects.geoapify.com/) and review its applicable plan/limits. The key stays server-side in an environment variable; never paste it into chat, a command argument, tracked configuration, or screenshots. [Official reverse-geocoding API](https://apidocs.geoapify.com/docs/geocoding/reverse-geocoding/).

Stop the running server with Ctrl+C in its launch terminal, then enable address lookup using a private terminal prompt:

```sh
cd /home/jason/Projects/nofear-drone-locator
read -r -s -p 'Geoapify API key (hidden): ' AETHERLINK_SCOUT_GEOAPIFY_KEY
printf '\n'
export AETHERLINK_SCOUT_GEOAPIFY_KEY
.venv/bin/python -m aetherlink_scout --capture /home/jason/mavic4pro-5min-test.pcap --geoapify
unset AETHERLINK_SCOUT_GEOAPIFY_KEY
```

Until configured, the dashboard remains usable and its address button is disabled. No real credential or external geocoding call was used during development; Geoapify authentication/address quality still need verification with your account. Requests are serialized, spaced at least 1.1 seconds apart, and bounded to 128 cached results in server memory. Failures show a generic message without provider URLs or keys; other detector functions continue.

An existing local/approved Nominatim-compatible deployment can alternatively use `--geocoder-url http://127.0.0.1:8088/reverse` (example only, no geocoder installed). HTTPS is required for non-loopback providers; credentials in endpoint URLs and the public `nominatim.openstreetmap.org` endpoint are rejected. Its [public usage policy](https://operations.osmfoundation.org/policies/nominatim/) excludes personal/confidential submissions and requires tracking applications to use their own service. Reverse geocoding may return a different nearby street/place; [Nominatim explains this limitation](https://nominatim.org/release-docs/latest/api/Reverse/).

Verification includes mocked provider success/caching/errors/key isolation, invalid coordinates/cross-origin rejection, synthetic preset loading, manual persistence and remembered clear, transient browser fixes/accuracy, safe address text, fixed/fresh origin generation, and operator-first panel order. Run `node tests/location.mjs`, the Python tests above, and `.venv/bin/python tests/browser_location.py` (it owns a temporary synthetic server on port 8769). Browser location tests mock the private preset and do not use your real location or send real operator positions to providers.

## Current status (2026-10-04)

Earlier bounded live result: the user reports that the live Mavic test worked. Read-only `/api/health` inspection confirmed `live_stream`, 4,397 packets, 408 decoded reports, zero errors/rejected/unidentified reports, received frequencies `[2437]`, and stopped/inactive capture. Counts stayed unchanged across two reads. This corroborates live input decoding; sound audibility, individual marker observations, manual STOP versus automatic completion, repeated START, and radio restoration still need field notes. A sanitized timestamped aggregate result is stored in ignored `local-data/live-test-diagnostics/`. Observation time is not the flight start/end time.

The earlier saved Panda hover-test recording decoded 185 reports from 2,714 packets over a 58.88-second report span; capture/restore/decode succeeded, with Intel retaining connectivity. The user reports Odyssey SSH now works; dedicated sensor TLS authorization and TAK rendering remain pending.

Initial read-only inspection before the Panda was attached found:

- Ubuntu 24.04.4 LTS; Git 2.43.0; Python 3.12.3; pip 24.0.
- `iw`, `nmcli`, and `ip` available. System Python initially lacked DroneCOT, PyTAK, and Scapy. A project-local `.venv` now contains DroneCOT 2.3.10, PyTAK 7.6.1 and Scapy 2.8.0; no sudo or system permission changes.
- One Wi-Fi radio: Intel Wireless 3165, PCI `8086:3165`, driver `iwlwifi`, `phy0`, interface `wlp3s0`. `iw list` advertises monitor mode and 2.4/5 GHz bands; actual Remote ID reception has not been tested.
- `wlp3s0` is in managed mode, connected to an iPhone hotspot on channel 149 and carries the default route. Ethernet `enp2s0` has no carrier. No USB Wi-Fi adapter is attached; the Panda adapter is unavailable.
- Monitor is listed as a software interface mode, but concurrent capture/channel changes must not be assumed safe for the active connection.
- Repository initially clean with only this README. No network state changed, live capture attempted, or TAK TLS connection tested.

The user reports earlier DJI Mavic 4 Pro Open Drone ID capture on different hardware. That does not validate this laptop. The user-provided destination is the Odyssey TAK Server at `tak.c5solutionsgroup.com`, TLS TCP port `8089`; reachability, sensor authorization, and TAK Aware rendering remain unverified.

## First safe functional test

The Windows volume `/dev/nvme0n1p4` (430.1 GB NTFS) was successfully mounted by the user at `/tmp/nofear-windows-readonly`; `findmnt` confirms `ro`. The initial NTFS3 command failed: kernel logs confirm `Unknown parameter 'norecover'`. That option belongs to the installed NTFS-3G driver. Filename-only Windows searches found no captures in the searched folders. The user subsequently confirmed captures were on Ubuntu, where they were found and decoded (see results below). No Windows file contents were opened or modified. To mount it again if needed, enter your password only in your own terminal:

```sh
sudo mount -t ntfs-3g -o ro,norecover /dev/nvme0n1p4 /tmp/nofear-windows-readonly
```

Stop if mounting fails; do not force mount, repair, or disable Windows safeguards. Verify it is read-only with `findmnt /tmp/nofear-windows-readonly`. Then locate capture filenames in the Windows user directories, avoiding credential directories. Unmount after inspection with `sudo umount /tmp/nofear-windows-readonly`.

For an authorized capture, the dedicated summary tool avoids starting DroneCOT workers entirely:

```sh
.venv/bin/python scripts/replay_summary.py /absolute/path/to/capture.pcapng
```

It prints only packet/decode counts and aircraft/operator coordinate presence/range counts. It opens no sockets, changes no interfaces, and ignores transport environment settings. Exit status 1 means no reports decoded; status 2 means input/read failure. Counts are per packet, not unique aircraft; no track aggregation is performed. Coordinate range checks and a separately flagged zero pair do not establish validity, authenticity, or a violation.

### Offline results (2026-10-04)

The replay tool explicitly registers Scapy radiotap/802.11 layers before opening PCAPs. Fresh CLI runs against the existing Ubuntu captures produced:

| Capture | Packets | Decoded ODID reports |
| --- | ---: | ---: |
| `/home/jason/mavic4pro-5min-test.pcap` | 8,789 | 899 |
| `/home/jason/mavic4pro-ch6.pcap` | 2,195 | 220 |
| `/home/jason/DroneID/mavic4pro-5min-test.pcap` | 899 | 899 |
| Existing `odid_wifi_bcn_sample.pcap` decoder control | 21 | 21 |

All decoded reports included Basic ID and aircraft/operator coordinate pairs within numeric ranges; no zero pairs, rejected payloads or decode errors were counted. These are report counts, not aircraft counts. Captures were read in place and not copied, committed or transmitted. Historical decode now works; historical position accuracy, timestamps and TAK delivery/rendering remain unverified. The later live result is recorded under Current status.

### Local CoT validation

```sh
.venv/bin/python scripts/validate_cot.py /home/jason/mavic4pro-ch6.pcap
```

Creates a unique ignored directory under `local-data/cot-validation/` with `historical-cot.xml` and an aggregate `summary.json`. XML contains sensitive reported identities/locations; keep it local. This is a historical archive wrapper, not a transport feed. It never opens network connections or live capture workers.

All three Mavic captures passed: respectively 899/220/899 aircraft events and the same number of operator events, one unique aircraft UID and one distinct operator UID per capture, no cross-kind UID collisions or conversion validation errors. Altitudes were present and preserved as reported geodetic altitude; their accuracy/reference has not been independently confirmed. PCAP times were September 28, 2026; every event was already expired at validation. The exporter uses capture-clock time/start with a 120-second stale interval, replaces current-clock remarks/metadata, and prefixes UIDs/callsigns with `HISTORICAL-REPLAY`. Missing/sentinel altitude becomes the CoT unknown sentinel; zero/missing/invalid coordinate pairs are withheld for review. Synthetic edge-case checks passed.

PyTAK 7.6.1 truncates point coordinates to four decimals, so conversion validation allows at most 0.0001 degree change per coordinate. This loses up to roughly 11 metres of latitude precision; coordinate preservation at full RID precision remains a follow-up before operational use. Passing local XML checks does not establish TAK Aware rendering, altitude accuracy, RF reception or report authenticity.

### Hardware now attached

The bounded procedure in [Panda capture instructions](docs/panda-capture.md) and `scripts/capture_panda.sh` has now produced a local test recording. It uses a 60-second channel-6 capture, requires private terminal sudo authentication, keeps artifacts ignored and attempts to restore the original Panda connection on exit.

After returning home, the user reports SSH access to Odyssey works. The attached Panda presents as MediaTek USB `0e8d:7610`, driver `mt76x0u`, `phy1`, interface `wlx9cefd5f67d4c`; monitor mode and 2.4/5 GHz channels are advertised. After capture, both Panda and internal Intel interfaces are managed and connected to home Wi-Fi on channel 44; the inspected default route uses Intel with metric 600. Before another capture, explain that Panda's home-Wi-Fi connection will be disconnected and obtain approval; retain Intel connectivity and verify routing afterward. Dedicated sensor certificate and server TLS/rendering tests remain pending.

### First Panda reception test (2026-10-04)

The user reported completing a Mavic test flight. The local recording begins at **23:12:22 UTC** (16:12:22 PDT). It contains **185 decoded reports** across **58.88 seconds**, grouped into one transmitter track with one Basic ID. Every report has aircraft and reported operator coordinates within numeric ranges; both positions change during the recording. The largest gap between decoded reports is **0.96 seconds**. No rejected payloads or decode errors were counted; tcpdump reports zero kernel-dropped packets. These results validate reception and local decoding for this test, not position accuracy, authenticity or all operating conditions. Channel 6 was configured by the capture procedure; the decoder supplied no channel metadata.

The recording and aggregate `test-summary.json` remain in ignored `local-data/panda-capture-DyUwMhd1/`. No coordinates or identities are included in the summary. To replay:

```sh
.venv/bin/python -m aetherlink_scout --capture local-data/panda-capture-DyUwMhd1/capture.pcap
```

Compare aircraft/operator markers and capture times with known flight observations. Laptop location, altitude references and operator-location meaning still require review. No CoT was transmitted. The implemented continuous-input path and controls are described above; live control verification is the next field step.

Replay an authorized, known Remote ID PCAP locally. This needs no monitor mode, sensor certificate, or TAK connection. Captures remain outside this repository.

The environment is installed. Run the network-free synthetic smoke check now:

```sh
.venv/bin/python -m pip check
.venv/bin/python scripts/smoke_test.py
```

This checks aircraft/operator XML conversion and missing-position handling; it does not validate radio decoding or TAK Aware rendering. To recreate the environment on this Ubuntu/Python platform:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.lock.txt
```

For a future authorized PCAP replay (not executed):

```sh
cp config/dronecot-offline.example.ini config/local.ini
# Put an authorized PCAP in ignored local-data/ and edit config/local.ini.
# Check that COT_URL remains log://stdout and no transport environment override is set.
.venv/bin/dronecot -c config/local.ini
```

If venv support is missing, stop and show any proposed system installation before using sudo. Record installed package versions for reproducibility. Check for environment overrides locally without displaying their values. Treat replay output as potentially sensitive; do not upload it. Compare decoded aircraft/operator fields against the known capture and verify missing fields are not mistaken for real coordinates.

## Live capture and TAK follow-up

The Panda Wireless PAU0B AC600 Dual Band is attached and its USB identity, driver and advertised monitor capabilities have been inspected (see above). Use this separate capture radio while retaining Intel Wi-Fi. The first capture/restore/decode/replay test succeeded, and the user subsequently confirmed live Mavic reception, corroborated by aggregate receiver counters. The next bounded check in [Panda capture instructions](docs/panda-capture.md) should verify repeated START and radio restoration. Manual STOP froze counters as confirmed by the user and stopped/inactive receiver status. Audible alerts and expected on-screen positions/information were confirmed in the latest test. Browser/manual laptop location, address lookup and TAK are optional. Do not grant capabilities to system Python.

Odyssey management SSH is `jason@100.85.26.12` (user-provided). An earlier read-only SSH attempt produced no result and was stopped; the user subsequently reported successful SSH access from home. The remote 8089 listener and sensor TLS connection remain unverified by this project. For a future check, use existing authentication and verified host keys, with no forwarding or credential-file inspection. SSH is for management; the sensor uses the TLS hostname above. Do not replace it with the SSH IP without verifying the server certificate name. No server changes or software deployments are part of this setup.

`config/dronecot-tls.example.ini` illustrates documented Wi-Fi input and TLS output. It is a template, not a runnable test configuration. Replace interface/channel and certificate placeholders only after planning the capture and obtaining a dedicated sensor identity through the approved administrator process. Keep certificates and keys outside Git. Never reuse an admin or personal iPhone certificate. Keep CA and hostname verification enabled. Do not change TAK Server configuration, firewall, router, or forwarding.

After local decoding succeeds, plan a bounded, clearly identified test through the existing server using the sensor certificate and verify aircraft/operator reports in TAK Aware. Use [the field checklist](docs/field-test-checklist.md) to record results. A Remote ID report alone does not establish a violation or identify a person conclusively.

## Official references

Reviewed 2026-10-04; examples use upstream documentation, not guessed keys:

- [DroneCOT installation](https://github.com/snstac/dronecot/blob/main/docs/installation.md) and [quick start](https://github.com/snstac/dronecot/blob/main/docs/quickstart.md): pip installation and Wi-Fi extra.
- [DroneCOT feeds](https://github.com/snstac/dronecot/blob/main/docs/feeds.md) and [configuration](https://github.com/snstac/dronecot/blob/main/docs/configuration.md): `[dronecot]`, `FEED_URL`, Wi-Fi interface/channel and offline PCAP input.
- [PyTAK configuration](https://pytak.readthedocs.io/en/stable/configuration/): local `log://stdout`, `tls://host:8089`, and `PYTAK_TLS_CLIENT_CERT`, `PYTAK_TLS_CLIENT_KEY`, `PYTAK_TLS_CLIENT_CAFILE`.

This repository provides tested replay, continuous capture renewal, detection controls, stale-track clearing, local history, decoder checks and sanitized templates. Panda recording, live reception and continuous capture renewal succeeded; audible alerts and expected on-screen positions/information were confirmed by the user. Manual STOP, old-track clearing and saved-History review were also confirmed. Repeated START, history across a server restart, launcher exit restoration and TAK delivery/rendering still need field confirmation. Do not commit or push without explicit instruction.
