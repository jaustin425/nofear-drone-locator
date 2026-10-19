# AetherLink Scout → TAK Server

Optional output from the existing shared live-track store, using DroneCOT's aircraft/operator converters and PyTAK 7.6.1's TLS transport. This adds no receiver and changes no interface, channel or Bluetooth behavior. Panda stays on 6, Techkey on 149 and legacy BLE on hci0. TAK output failure never stops local detection. A later endurance run observed nine RF frames on channel 149; positive channel-149 RID and BLE RID decoding remain unverified.

## Local setup and first check

The approved destination is **tls+wo://tak.c5solutionsgroup.com:8089**. Server certificate chain verification and hostname verification are mandatory; insecure options and other destination URLs are rejected by this profile. There is no enrollment, admin connection, sensor-location beacon or production server configuration change.

On this laptop, private copies of only `DRONECOT.p12` and `tak-ca-chain.pem` from the user-specified DRONECOT package were placed in ignored `local-data/tak/` (directory 700, files 600). The original `~/tak-dronecot/` paths were absent. Neither the package's standalone private key nor its password notes were opened. The path-only config was created as ignored `config/local-tak.ini` (600); the user subsequently entered the package password privately using setup below. Do not display or share that config.

```sh
cd /home/jason/Projects/nofear-drone-locator
# Fictitious aircraft/operator XML only; no socket or radio activity.
.venv/bin/python -m aetherlink_scout.tak --local-test
# Hidden prompt; stores password only in the private local config, never argv.
.venv/bin/python -m aetherlink_scout.tak --configure
# Mutual TLS only, no CoT or radio changes.
.venv/bin/python -m aetherlink_scout.tak --check
```

The user reported **PASS** for setup/check on October 19, 2026: dedicated client mutual TLS with the private CA and hostname checks enabled. A prior TCP-only check reached port 8089. That TLS-only check did not establish client delivery; the later live result below confirms marker display for the tested client/account. If this check fails, report its exact sanitized error and stop the live TAK test. Do not change router/firewall/server settings or turn off verification.

On a new machine, create a private local copy of [the sanitized output example](../config/scout-tak.example.ini), supply the dedicated sensor certificate/CA paths and run configure privately. The template contains no password. Never substitute an administrator or personal-phone certificate. Secrets stay out of browser responses, logs and Git. Existing DroneCOT standalone feed templates remain examples, not a replacement for Scout's working scanner.

## Start the sensor

Stop any older Scout server first. Both USB adapters must start managed under NetworkManager, Intel must be the preferred uplink and Bluetooth must already be powered on.

```sh
cd /home/jason/Projects/nofear-drone-locator
sudo -v
bash scripts/capture_dual.sh --live-tak
```

This explicitly disconnects only Panda and Techkey for the existing fixed-channel preparation; Intel stays connected. The launcher validates the private TAK configuration before any radio changes. Open **http://127.0.0.2:8765**. **START** starts the existing three-source detector; the optional output connects and sends only current live positions. The TAK status row reports connection/errors and successful socket writes; writes are not TAK delivery acknowledgements. **STOP** cancels detection/renewal and stops new output; retained map positions keep aging. **Ctrl+C** in the launcher exits the app/output before restoring both USB Wi-Fi connections. Bluetooth power is unchanged.

`bash scripts/capture_dual.sh --live` remains the same local-only detector with TAK disabled. For already separately prepared radios, the direct command is `.venv/bin/python -m aetherlink_scout --combined --tak-config config/local-tak.ini`; normal field use should use the launcher so it can restore original connections.

A live-input **local-file output** mode is also available for separately prepared radios:

```sh
.venv/bin/python -m aetherlink_scout --combined --cot-local-output local-data/scout-cot-test.xml
```

It requires a new output filename, creates it privately with mode 600, and opens no TAK socket. Do not paste real XML here: it contains aircraft identifiers and reported locations. Do not run alongside another capture instance. Historical replay and timed simulation reject both forwarding options and can never transmit to TAK. The `--local-test` command above needs no prepared hardware and is preferred for the first conversion check.

## Output semantics

- One bounded worker samples the shared live-track store. It coalesces to the latest positions at most once per second per marker; no persistent outbox, packet spool or historical forwarding exists. Radio threads never wait for a network send. There are at most the existing bounded track/alias counts in output state.
- The existing claimed-ID **plus ID-type** correlation remains authoritative, including source provenance and conflict flags. Aircraft and reported-operator UIDs are stable for a correlated track and distinct from each other. They are linked as a pair. Late merges expire already-sent provisional UIDs at their last reported points. Matching claimed IDs are not authentication; initial unidentified tracks can exist separately before a Basic ID is heard.
- Each point carries its own receipt observation time and expiry, independent of other fields. Default `COT_STALE=30` seconds, configurable 15–300 in the private file. Fresh metadata/aircraft data cannot renew an old operator fix. Missing positions produce no marker. Missing/old altitude and accuracy are unknown, never zero. Decoded coordinate precision is restored after the upstream point converter's rounding. No laptop testing preset or geocoded address is forwarded.
- Incoming TLS data is drained/discarded; there is no TAK receive queue or feedback into detection. No hello/status beacon is generated. This is application write-only behavior over a bidirectional TLS socket, as documented by PyTAK.
- Transport failures show sanitized errors and retry at 1, 2, 4 … 30 seconds while detection remains active. On reconnect only fresh current fixes are resent with **original** observation/expiry timestamps and stable UIDs. STOP closes output on the next bounded poll and cancels further retries; connection attempts have an 8-second bound and sends a 3-second bound. Already-buffered transport writes cannot be recalled. TLS/trust/authentication/configuration errors stop output retries until a new explicit detection session or application restart; local capture/map/history/alerts continue.
- CoT stale timestamps allow clients to age/remove reports; TAK Aware may keep a stale marker according to its display policy. Verify actual client behavior rather than assuming it removes every marker immediately.

## Observed live result — reported October 19, 2026

The user reported that the live test passed: **AetherLink Scout detected the aircraft and reported operator/controller, and both appeared on the TAK Aware map.** This confirms the detection → CoT → existing TAK Server → TAK Aware path for this test. Evidence is the user's direct observation; no new packet/log inspection was performed for this report. Exact flight time, receiver/channel attribution, client version, coordinate comparison and update intervals were not provided. This result does not establish positive reception on channel 149 or BLE, all-client/group visibility, stale expiry, field reconnects or multi-aircraft correlation. A reported operator point remains unverified location/identity information.

## Controlled TAK Aware test

1. Run the no-network local test, then the mutual-TLS-only check. Stop on any connectivity or TLS error; record the exact sanitized error, never passwords/config/XML. The laptop-to-server check requires no aircraft flight.
2. At a safe time, connect TAK Aware to this existing server and confirm the intended team sharing/group visibility. Run `--live-tak`, then START. Use an authorized broadcasting aircraft; a safe ground broadcast may suffice if that model transmits then. Do not change scanner channels to make this integration test positive.
3. Confirm the local dashboard first. Verify distinct **AetherLink Scout aircraft** and **AetherLink Scout reported operator** markers in TAK Aware when those fixes are actually broadcast. Compare coordinates, timestamps and pair links privately. A missing operator fix must remain absent; a reported point is not a verified person or residence. Record source/time/uncertainty/corroboration. Check multiple feeds yield one pair after identity correlation.
4. STOP detection (or stop the reference broadcast), record the last fix times, and wait at least 30 seconds plus network/client delay. Confirm no timestamp renewals and verify stale treatment in TAK Aware. Aircraft updates must not preserve an old operator fix. Saved history and page refresh must send nothing.
5. Reconnect behavior is verified with a local TLS test server that deliberately closes the connection. For field confirmation, exit the launcher normally, verify restoration, relaunch, then START with fresh authorized reports. Verify stable IDs/updated fresh fixes and no historical backfill. Do not interrupt the home router, modify TAK Server or disconnect the uplink to force a failure.
6. STOP, verify counters freeze, then Ctrl+C and verify both USB adapters restore and Intel stays the uplink. Record private aggregate results. Aircraft/reported-operator rendering is now user-confirmed for the reported live test. Client stale policy, field reconnects, multi-aircraft correlation and restoration checks remain pending until observed.

## Validation and references

The final full Python suite passed **94 tests**. Combined-detector, optional-TAK-error and existing live-dashboard browser checks passed, as did JavaScript state/location/replay checks, shell/Python syntax and Git whitespace checks. Secret/certificate paths are ignored, no protected artifacts are tracked, and private copies/config have mode 600 under a mode-700 certificate directory. Tests use fictitious reports, normal-user synthetic capture processes and temporary local PKI only. They cover precision, independent field expiry, unknowns, correlated feeds, provisional retirement, no output before START/after STOP, non-blocking failure/retry, fatal TLS handling, private config restrictions, replay guards, real PyTAK PKCS#12 mutual TLS, hostname/CA/password rejection and server-induced reconnects. Browser checks cover output status/errors while all three sources continue; launcher tests cover preflight-before-change, fixed feeds and app-before-restore ordering. Automated development tests sent no production CoT; the subsequent user-conducted live test successfully delivered aircraft/reported-operator markers through production TAK.

- [Official DroneCOT configuration](https://github.com/snstac/dronecot/blob/main/docs/configuration.md): CoT identity, aircraft/operator types and PyTAK TLS configuration delegation.
- [Official PyTAK configuration](https://pytak.readthedocs.io/en/stable/configuration/): `tls+wo`, PKCS#12 password, private-CA file, and server/hostname verification.
- Installed DroneCOT 2.3.10 converters and PyTAK 7.6.1 transport were inspected and exercised; supported features are distinguished above from production/client field results.
