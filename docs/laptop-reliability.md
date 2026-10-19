# Laptop reliability validation

Scope: finish and validate AetherLink Scout on this Ubuntu laptop. Raspberry Pi deployment remains a future consideration; no service installation, platform migration or system-permission work is part of this checkpoint.

## Added safeguards

- The dashboard and `/api/health` distinguish listening, starting, stopped, degraded recovery, failed capture and an overdue input-worker heartbeat. Quiet inputs remain listening: no packets or Remote ID is not proof of failure or absence of aircraft. A live worker/process is not proof that every over-the-air protocol/channel can be received. TAK status stays separate; a TAK error does not turn healthy local receivers off.
- `/api/health` also reports process uptime, current/peak resident memory, CPU seconds, open file descriptors, filesystem free space and runtime-log counters. These are read-only process/storage measurements, not radio commands or network probes. Resource measurements are cached for one second; worker heartbeat overdue threshold is ten seconds while running. Recovery/startup phases remain explicit instead of being mistaken for a quiet RF failure.
- New capture/BLE stderr goes into private `local-data/runtime-logs/` (directory 700, files 600). Each file is capped at 256 KiB, including an omission marker; excess stderr is drained/discarded so a full log cannot block capture. Closed logs are pruned on segment open/close to the latest 100 files, 16 MiB and seven days. Active logs are protected by locks and may temporarily exceed the closed-log limits; each remains individually capped. Pruning considers only Scout-named regular owned files in this dedicated directory, excludes symlinks and never touches captures/history/credentials/old field folders. Maintenance/write errors appear in health runtime-log status; inability to create a required private log is a visible capture startup error.
- The health recorder is GET-only against the fixed laptop loopback address. It does not START/STOP detection, configure radios, read credentials, open captures or contact TAK. It saves an allowlist of counts, states, heartbeat ages and resource metrics. A hashed internal session marker allows resets to be noticed without recording raw session IDs; no aircraft IDs, coordinates, raw messages or server configuration are retained. Records are observations, never an automatic field-test PASS.

These changes take effect after restarting Scout. Old capture logs are preserved; existing processes keep their original logging behavior until restarted.

## Start a laptop validation session

Exit the old launcher with Ctrl+C in its own terminal and wait for restoration. Bluetooth must already be powered on, both USB radios must start managed under NetworkManager and Intel must be the preferred uplink. The existing dedicated certificate/private config stays unchanged.

```sh
cd /home/jason/Projects/nofear-drone-locator
sudo -v
bash scripts/capture_dual.sh --live-tak --recover
```

This explicitly selects the already documented fixed 6/149 USB preparation and same-device recovery; it disconnects only those two dedicated adapters and preserves Intel. Open **http://127.0.0.2:8765**, then START. Use `--live` for a local-only session. No browser refresh starts detection. STOP cancels detection/recovery; Ctrl+C exits the app before restoring Wi-Fi adapters. No radio state was changed by the development tests.

## Two-hour observation

In a second terminal, after starting the intended live session:

```sh
cd /home/jason/Projects/nofear-drone-locator
.venv/bin/python scripts/monitor_health.py --duration 7200 --interval 10
```

The recorder creates a new ignored, mode-700 `local-data/reliability-*` folder with mode-600 `run.json`, `health.jsonl` and `summary.json`. It stops at the requested duration, 10,000 samples or 16 MiB of sample output. Ctrl+C finishes a partial summary; it does not stop Scout. The terminal prints only the folder path and aggregate completion counts. Older servers without the new health fields yield unknown/null fields; restart Scout first. Each recording is separately bounded; old reliability folders remain under user control.

Check source health, confirmed renewals, USB recoveries, memory/FD trends, free disk space and TAK state. A quiet observation can verify process/renewal stability but does not verify Remote ID reception. Use an authorized reference broadcaster when available; record reception results separately and privately. Browser sound requires a user gesture and the browser remaining available.

Acceptance: no unexplained source errors or overdue running-worker heartbeats; normal renewals preserve the session and cumulative counters; memory/file descriptors settle rather than rising with each segment; closed runtime logs obey retention; STOP freezes counters while map positions age; exit restores radios. An unavailable dashboard sample is recorded explicitly. A completed timer alone is not a successful endurance test. Avoid sharing raw field captures or screenshots with actual reported locations.

## USB bench check

A flight is not needed to exercise USB recovery. Follow [the physical recovery procedure](usb-recovery.md#physical-test-when-approved): remove/reinsert one dedicated adapter at a time, confirm degraded status/healthy peer feeds, verify returned monitor channel and preserved session, then test STOP while absent. The recorder can observe this sequence. Physical changes remain an operator-controlled test, not something the recorder does. Actual hardware validation remains pending until observed.

## Remaining TAK checks

- **Stale reports:** after a reference detection, STOP and observe the last report times. At the default 30-second expiry plus client/network delay, verify TAK Aware's stale treatment and that no timestamps renew. Aircraft updates must not refresh an old operator point. Saved history must not transmit.
- **Reconnects:** local TLS tests already deliberately close the connection. Field-check normal launcher exit/relaunch with fresh reports and confirm stable correlated marker IDs/no historical backfill. Do not interrupt production TAK or the router to force a failure.
- **Outside-home access:** when the laptop is on an operator-selected outside connection, run only `.venv/bin/python -m aetherlink_scout.tak --check`. This checks the existing hostname, CA and client certificate without flying or sending CoT. Report PASS or the exact sanitized connectivity/TLS error. Home-network success does not prove external reachability. Stop the test on failure; do not change server/router/firewall settings or disable verification.

Follow the [controlled TAK test](tak-forwarding.md) for the full procedure. The previous user-conducted test already confirmed aircraft and reported-operator marker delivery/rendering; stale/client-reconnect/external checks remain pending.

## Coverage checks after reliability

Keep the current feed configuration. Channel 6 has positive Mavic reception evidence. Channel 149 remains selected and RF reception was subsequently observed in the endurance run below; positive Remote ID reception there still needs a known transmitter on that channel. Legacy BLE needs a known BLE RID transmitter: ordinary Bluetooth service updates are not a positive RID test, and this controller does not establish Bluetooth 5 extended/Coded PHY coverage. Then verify two authorized aircraft simultaneously: independent marker pairs, selection, alerts, history and correlated feeds without duplicates. Reported operator information remains a report, not conclusive identification or evidence of a violation.

## Verification status

The final full Python suite passed **121 tests**. Recovery/health, TAK-error and existing live-dashboard browser checks passed, along with JavaScript state/location/replay, syntax and Git whitespace checks. Automated checks use synthetic sources/local TLS and temporary data. They cover capped stderr draining, count/age/byte retention, active-file and symlink protection, repeated segment FD release, honest quiet/degraded/TAK-independent health, private aggregate recording, and existing recovery/map/alert behavior. The later endurance result below records the completed two-hour observation and longer user-reported run. Physical USB recovery, TAK stale behavior and outside-network tests remain pending. No production CoT was sent by these automated tests; no radios, network state, server settings or system permissions were changed.

## Observed endurance result — October 19, 2026

The user reported a **188-minute** run. The read-only recorder completed its configured 120 minutes: 717 samples, zero unavailable responses, 713 healthy observations, three starting/renewing observations and one transient degraded observation. That transient was bracketed by healthy samples; all three sources advanced from segment 3 to 4 in the next sample, with fresh worker heartbeats, no decode errors and no recovery attempts. A normal-renewal sampling race is consistent with the evidence, but per-source process/health-reason detail was not retained, so its exact cause is inferred. It is not recorded as a sustained receiver outage.

A later stopped-session health snapshot matched the recording's session and showed **94 bounded segments per source (279 total renewals), 758,874 input packets, 1,003 decoded reports and zero decode/rejection errors**. Panda/channel 6 accounted for the decoded reports. Techkey recorded **nine frames at 5745 MHz/channel 149**, establishing positive RF reception there for this run; none decoded to RID. BLE remained active/renewing during the recording but decoded no RID. All sources were stopped/inactive at review, with zero USB recoveries/attempts and no history-save error.

TAK stayed connected throughout all 717 recorded samples, with zero transport errors; the final idle output counter recorded 753 CoT writes. This is socket-write evidence, not an independent observation of each marker in TAK Aware. Memory rose approximately 3.6 MiB during the recorded window and settled near 105 MiB; file descriptors began/ended at 26 (range 23–27). There was no observed segment-by-segment FD accumulation or runaway memory growth in that window. Closed runtime-log retention held at 100 files, about 14 KB, with no maintenance errors. Detailed aggregate review remains private under the ignored reliability recording.

Assessment: positive endurance evidence for capture renewal, actual channel-6 RID decoding, ongoing TAK transport and bounded logging. The recorder stopped at two hours as designed; the remaining reported run duration has a final snapshot rather than a continuous resource series. Physical USB recovery, channel-149/BLE **RID** reception, multi-aircraft field separation, TAK stale/client reconnect and outside-network checks remain separate pending tests. A later monitoring refinement should distinguish expected renewal transitions from transient degraded snapshots without suppressing real capture exits.
