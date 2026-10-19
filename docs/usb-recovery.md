# USB recovery and unattended startup

Status: implemented and checked with simulated USB-generation changes, real normal-user synthetic capture processes and mocked radio commands. Panda and Techkey physical unplug/replug recovery and STOP cancellation were verified October 19, 2026. Final launcher restoration passed after both tests. Automated development checks changed no radio state or system permissions; the physical tests below were operator-controlled.

## What changed

A USB adapter can return under the same interface name with a new kernel ifindex/PHY and managed mode. Existing tcpdump is attached to the old instance; bounded renewal alone does not restore that radio. The reported laptop inspection found Panda returned managed as PHY 3/ifindex 14 while Techkey remained monitor/channel 149 and Intel remained the uplink. This is consistent with the reported failure, not proof of its electrical/driver cause.

The opt-in recovery policy is separate from ordinary confirmed-timeout renewal. It applies only to physical absence or a changed kernel interface generation of the inspected Panda/Techkey. Other source errors still stop the group. Existing commands without `--recover` retain the previous stop-on-loss behavior.

## Run on this Ubuntu laptop

Stop the existing launcher with Ctrl+C in its own terminal and allow restoration to finish. Both USB radios must begin managed under NetworkManager, Intel must be the preferred uplink, and hci0 must already be powered on. The existing dedicated TAK identity/private config remains unchanged.

```sh
cd /home/jason/Projects/nofear-drone-locator
sudo -v
bash scripts/capture_dual.sh --live-tak --recover
```

This terminal selection authorizes initial USB preparation and later re-preparation of only the same two dedicated adapters after physical reconnect. Intel stays the uplink. Open **http://127.0.0.2:8765**, then START. For local-only operation, substitute `--live` for `--live-tak`.

Optional process-launch autostart:

```sh
bash scripts/capture_dual.sh --live-tak --recover --autostart
```

This starts detection once when Scout launches, without a browser START click. Refreshing/opening the page cannot start it again. STOP cancels detection/recovery until explicit START or an explicitly autostarted **new process**. Browser sound still requires a user gesture; a headless process does not produce the browser alarm. No boot service, sudo policy, Python capabilities or system package was installed.

The direct prepared-radio CLI is `.venv/bin/python -m aetherlink_scout --combined --recover-radios --autostart --tak-config config/local-tak.ini`; use the launcher on the laptop to retain original connection/restoration context. Recovery is unavailable for historical replay, simulation, BLE-only and channel-hopping modes.

## Recovery behavior

- Idle capture reads check the pinned USB identity/generation at most once per second. A confirmed outage stops and confirms exit of the old capture before any radio reconfiguration. It then waits 1, 2, 4, 8 and up to 15 seconds between attempts. Absence can remain pending indefinitely while START is active; STOP interrupts the wait immediately.
- Healthy Wi-Fi/BLE sources and optional TAK forwarding continue. The dashboard shows a yellow **Detection degraded** state, per-source recovering status, attempts and successful USB recoveries. Positions continue aging normally, and genuinely new tracks can alert during recovery. The session, existing tracks, counters, history and alert deduplication are preserved. Recovery counts are separate from ordinary capture renewals.
- Only the same inspected MAC, driver and interface name are accepted. The current profiles pin Panda `wlx9cefd5f67d4c`/`mt76x0u` to channel 6 and Techkey `wlx1cbfce038535`/`rtl88x2bu` to channel 149. A new PHY number is allowed; an unexpected name, address or driver is not adopted. The device must still be a USB interface. This is a protection against accidental device selection, not cryptographic hardware authentication.
- Before mutation, the controller requires Intel's default route, no default route through the capture adapter, no sibling interface on that PHY, advertised monitor support, unrestricted target channels and existing noninteractive sudo authorization. It relinquishes that adapter from NetworkManager, sets it down/monitor/up, sets its fixed channel, and verifies the result. Panda retains the known channel-1 → channel-6 preparation workaround. Normal-user Python invokes only individually bounded terminal-authorized commands; Python is never privileged.
- STOP is checked between every command. Each privileged recovery command uses a four-second timeout with a one-second kill grace. STOP waits for in-flight preparation/cleanup through the existing combined supervisor; no later command or capture is allowed after cancellation. The launcher restores radios only after the app exits with confirmed cleanup.
- Wrong hardware, authorization loss, busy/failed preparation, regulatory restrictions, channel drift, malformed input and ordinary capture errors are **not** retried as USB recovery. They stop the group visibly. Unconfirmed capture exit prevents re-preparation/restoration. Another unplug during preparation returns to waiting only if physical absence/new generation is confirmed. No blind driver reloads, USB power cycling or OS reboot is performed.
- Bluetooth power/reader behavior is unchanged; this feature recovers the two Wi-Fi USB sources. Channel-149 positive reception and BLE RID decoding remain unverified by this work.

## Physical test, when approved

1. Launch the recovery mode and START; record session/source counters privately. Use a known authorized reference broadcaster if available; a flight is not needed to exercise USB recovery itself.
2. Unplug only Panda. Confirm the source shows recovering and the group degraded, with Techkey/BLE still active. Verify retained positions age and no old track is re-alerted.
3. Plug the same Panda back in. Within a polling/backoff interval plus setup time, verify monitor/channel 6, an incremented USB recovery count, the original session and resumed fresh reports. Repeat separately for Techkey/channel 149 without treating zero RID reception as receiver proof.
4. Unplug again, press STOP while recovering, then reconnect. Confirm no preparation/capture restart until explicit START. Exit the launcher and verify both adapters restore, Intel stays connected and Bluetooth power is unchanged.
5. Record actual hardware results, exact observation time, adapter identities, counters and uncertainty privately. Investigate recurring USB/driver/power faults separately; successful recovery does not establish their root cause.

## Raspberry Pi 4 deployment prerequisites

This remains a laptop-specific profile, not a finished Pi appliance. Before deployment, inspect the actual Pi OS/Python/architecture, Wi-Fi chipsets/drivers and monitor/channel capabilities, permanent adapter identities/naming, Bluetooth reader support and the intended management/uplink interface. The current code explicitly protects/requires laptop Intel `wlp3s0`; it must be replaced by a reviewed platform profile for the Pi rather than bypassed. Establish how boot handles absent radios, missing network, power loss and permanent failure, and validate ARM dependencies in a local venv.

A later service setup should keep the application unprivileged, provide a narrowly scoped privileged radio/capture helper, start the explicitly approved detection profile at boot, supervise process failures, stop child processes before restoration, protect sensor credentials and report degraded/error state without a browser. Interactive `sudo -v` plus cached authorization is the laptop workflow, not a complete boot-time authorization design. Watchdog policy must account for a running web process with a failed detector; process restart alone does not establish receiver health. No Pi service or permission changes are authorized by this document; show the concrete deployment commands for review on the target.

Official references consulted for that deployment plan: [Raspberry Pi configuration/headless documentation](https://www.raspberrypi.com/documentation/computers/configuration.html) and [systemd service supervision](https://www.freedesktop.org/software/systemd/man/latest/systemd.service.html). The current change adds explicit process autostart, not an installed system service.

## Offline validation

The full Python suite passed **108 tests**. Recovery, combined-detector and TAK-error browser checks passed, as did JavaScript state, syntax and Git whitespace checks. New coverage includes USB generation loss/reappearance, preserved session/tracks/history/counters, continued healthy feeds and alerts, no repeated original alert, STOP during waiting/preparation, unconfirmed old-process cleanup, unexpected non-USB failure, private profile/uplink/PHY/channel/authorization guards, explicit autostart and no browser-triggered restart. Temporary browser ports were released. Panda and Techkey physical replug, STOP cancellation and final launcher restoration were subsequently verified as recorded below.

## Panda physical verification — October 19, 2026

During an operator-controlled bench test, Panda was unplugged while detection ran. Scout entered recovering/degraded status with two of three inputs listening, confirmed the old Panda capture exited and kept Techkey/channel 149, Bluetooth, TAK and Intel connectivity active. Reconnecting the same Panda restored monitor/channel 6 and resumed packet reception automatically. The recorded active session stayed unchanged; the Panda USB recovery counter advanced to one with zero decode errors.

Panda was unplugged again, then the operator pressed STOP while it was absent. All three captures exited, cleanup was confirmed and retry attempts froze at ten. The operator then reconnected Panda without START. Two later status checks confirmed detection stayed stopped, all capture processes remained exited, total packet count stayed 3,779 and recovery count stayed one. Panda remained managed/unprepared, Techkey remained monitor/channel 149 and Intel remained the preferred uplink. This is the intended STOP behavior: reconnecting hardware alone does not authorize reconfiguration. After a replug while stopped, exit/relaunch the normal terminal launcher to prepare both radios before the next START.

Evidence is operator-reported physical actions corroborated by read-only health, aggregate recording and interface/route checks. No RID decoded during this bench test, so field freshness/duplicate-alert behavior with a broadcasting aircraft was not exercised. Techkey was checked separately below; launcher-exit restoration was subsequently confirmed as recorded below. Private aggregate notes remain in the ignored reliability recording. No software/radio changes were made by the observer.

After the operator exited the launcher with Ctrl+C, read-only checks confirmed Panda and Techkey were both managed, NetworkManager-controlled and connected on channel 44. Intel remained connected and the preferred uplink. Bluetooth remained powered, Scout discovery was released, and the loopback dashboard was no longer reachable. This completes the Panda recovery → STOP cancellation → normal launcher restoration bench sequence. The observer performed no radio/network changes.

## Techkey physical verification — October 19, 2026

The operator unplugged Techkey while detection ran. Read-only checks confirmed recovering/degraded status with two of three inputs listening, the old Techkey capture exited, Panda continued packet reception on channel 6, Bluetooth and TAK remained active and Intel remained the preferred uplink. Reconnecting the same Techkey restored monitor/channel 149 and a running bounded capture. The session matched its private baseline, with one Techkey USB recovery and zero decode errors. No Techkey packets/RID were observed during this particular bench test, so returned capture readiness is distinct from positive over-the-air reception.

The operator unplugged Techkey again and pressed STOP while absent. All three captures exited with confirmed cleanup, retry attempts froze at eleven and TAK became idle. After reconnecting Techkey without START, two read-only checks still showed stopped/inactive detection, no live capture processes, 9,543 unchanged total packet inputs and one USB recovery. Techkey remained managed/unprepared, Panda stayed monitor/channel 6 and Intel remained preferred. STOP therefore prevented a hardware reconnect from resuming capture or reconfiguration. Final launcher exit/restoration for this Techkey run subsequently passed, as recorded below. Private aggregate evidence is ignored; no radio/network changes were performed by the observer.

Final read-only checks after the operator exited the Techkey-test launcher confirmed both USB adapters and Intel managed/NetworkManager-connected on channel 44, Intel still the preferred uplink, Bluetooth still powered with discovery released, and the dashboard closed. Both adapters have now completed physical loss/recovery, STOP-before-return cancellation, and normal launcher restoration checks. These bench tests decoded no RID; field report/alert behavior under USB loss and channel-149/BLE RID reception remain separate checks.
