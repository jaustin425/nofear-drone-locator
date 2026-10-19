# Channel coverage

Status: optional Panda scanning is implemented and checked offline; hardware scanning remains untested. Fixed channel 6 remains the default. No radio changes were made during development.

## Optional scan mode — October 18, 2026

After explicit approval for Panda preparation and retuning, use the terminal launcher with `--live-scan` instead of `--live`. It disconnects only Panda from its current Wi-Fi connection, prepares monitor mode on channel 6 and starts normal-user Scout. Intel remains the uplink. Opening the page does not scan. START selects channel 6 for three seconds, then channel 149 for one second, repeating until STOP. Dwell starts after each verified retune; command overhead and bounded capture renewal add reception gaps.

```sh
cd /home/jason/Projects/nofear-drone-locator
sudo -v
bash scripts/capture_panda.sh --live-scan
```

These commands change radio state: review the impact and obtain approval before running. The tested fixed-channel launch remains `bash scripts/capture_panda.sh --live`.

Each START/120-second renewal checks the Panda interface/mt76x0u driver, discovers its current PHY, checks both channel capabilities, monitor mode, Intel default route and terminal sudo authorization. Restricted/disabled channels fail closed. A single bounded tcpdump stays open during retunes; neither decoder state nor session IDs, counts, history or alert deduplication resets between channels. The receiver accepts radiotap frequencies 2437/5745 MHz in scan mode and stops on other reported frequencies. Missing radiotap frequency remains unverified, not proof of channel coverage.

Normal-user Python supervises a cancellable scan thread. Every retune uses this argument structure (only the known Panda interface and channels 6/149 are selected):

```sh
sudo -n timeout --signal=TERM --kill-after=1s 2s iw dev wlx9cefd5f67d4c set channel CHANNEL
iw dev wlx9cefd5f67d4c info
```

No privileged Python, system capability changes, installations, regulatory changes or unattended password prompts are used. A reported-channel mismatch, radio loss, route loss or command/authorization failure stops scanning and capture with a visible error; it cannot trigger automatic renewal. STOP cancels pending retunes, waits for an in-flight command and scan-thread exit, then confirms capture exit. Ctrl+C stops Scout before the launcher restores Panda to managed mode/NetworkManager and reconnects a known original connection. STOP alone leaves the prepared radio in monitor mode on its last channel.

The dashboard shows the selected scan/dwell plan, last verified radio channel with age and last observed packet frequency. A last verified channel is not a continuously measured current-channel claim, especially after STOP. Fixed mode still displays its expected channel separately.

Offline checks cover scheduling, cancellation during dwell and retune, failed/stuck retunes, capture expiry, capability restrictions, mixed-frequency independent tracks, unexpected frequencies and explicit launcher selection. Live retune reliability, reception on 149, STOP/terminal restoration and two-drone behavior still need field confirmation.

Checkpoint validation: all 55 Python unit tests passed, including seven scan tests, seven Techkey launcher tests and the explicit scan-launcher test. `node tests/live_state.mjs`, `node tests/location.mjs` and `node tests/replay_state.mjs` passed. `.venv/bin/python tests/browser_scan.py` verified the plan/last-verified display, explicit START and visible failure with a synthetic capture factory that refuses radio access; no external requests were made. JavaScript/shell syntax and `git diff --check` passed.

### Newly attached Techkey

Read-only inspection on October 18 found a Techkey AC1200 USB `0bda:b812` (RTL88x2bu), interface `wlx1cbfce038535`, driver `rtl88x2bu`, currently PHY 2 on kernel 6.8.0-142-generic. It advertises monitor mode and channels 6/2437 MHz and 149/5745 MHz without disabled/radar/no-IR flags. At that initial read-only inspection it was connected in managed mode; no interface changes or driver installations were made during inspection. The subsequent 60-second channel-6 capture completed and restored Techkey but contained zero packets (24-byte PCAP header only). The later unfiltered channel-44 diagnostic captured 7,537 frames, all reporting 5220 MHz, and confirmed restoration. Basic monitor reception on 44 is verified. A subsequent full-frame Mavic test decoded 189 Remote ID reports on channel 6, forming one track with zero decode errors; Techkey restoration was confirmed. A later simultaneous recording decoded 168 reports on Panda/6 and no frames on Techkey/149; both adapters restored successfully. Channel-149 reception and dual-input dashboard operation remain unverified. See [the simultaneous result](dual-radio-test.md#first-simultaneous-result--october-18-local-time). The original filter matched 405 frames offline. See [test results](techkey-capture.md#channel-44-result--october-18-local-time). PHY indices can change.

Panda was also present, connected in managed mode on channel 44, with Intel retaining the preferred default route. This supersedes an earlier observation before the USB adapters were reattached. The current launcher and scan profile deliberately address Panda only. Techkey is not configured or captured automatically.

The separate [combined detector](combined-detector.md) dedicates Panda to channel 6 and Techkey to 149, plus legacy BLE, with shared tracks/alerts, independent bounded capture and coordinated STOP/restoration. Positive reception on the intended second-radio channel still requires a suitable transmitter test. Hardware dashboard validation remains pending; the single-radio scan option is still separate.

The 6/149 plan is receiver coverage, not evidence that the Mavic broadcasts on both channels. Our Mavic captures establish channel-6 Remote ID reception only. DJI lists Wi-Fi Beacon as its Direct Remote ID transport in the [Mavic 4 Pro manual](https://dl.djicdn.com/downloads/mavic-4-pro/20250925/DJI_Mavic_4_Pro_User_Manual_v1.2_en.pdf); this is not a promise of simultaneous 6/149 broadcasts. A simultaneous comparison should preserve a working channel-6 reference and record any channel-149 result as an observation for that test interval.

The user elected to retain channel 149 in the receiver plan despite the empty Mavic comparison. Treat the monitor/channel configuration as operationally selected while preserving its unverified reception status. Add Bluetooth as a separate source rather than replacing either Wi-Fi channel; see [the inspected hardware and BLE integration plan](bluetooth-monitoring.md).

## Resumed checkpoint — October 5, 2026

Checkpoint `b12b11b` was clean on inspection. The laptop had rebooted. Two local history entries and the ignored location preset remained present; a fresh temporary Scout process successfully displayed saved historical details and markers without capture or external requests. The temporary process was stopped afterward.

Panda `wlx9cefd5f67d4c` uses `mt76x0u` and currently advertises monitor support. Its PHY index changed after reboot, so discover the PHY from the interface rather than hardcoding it. Panda was connected in managed mode on channel 44; Intel `wlp3s0` retained the default route. This observation does not establish whether the earlier launcher exit restored Panda before shutdown.

The inspected capability report enables 2.4 GHz channels 1–11, with 12–14 disabled. Channels 6/2437 MHz and 149/5745 MHz are enabled without radar/no-IR flags. The adapter supports one channel at a time. Advertised capability is not a successful live retune or Remote ID reception test on channel 149.

## Original implementation plan

- Preserve tested fixed-channel-6 mode as the default and provide an explicit optional 6/149 scan profile.
- Validate each planned channel against the current adapter capabilities. Do not change regulatory settings or include disabled channels.
- Keep privileged retuning in an explicitly approved, supervised terminal path. Opening the dashboard must not prepare or retune a radio; system Python must stay unprivileged.
- Verify actual reported channel after every retune. Earlier Panda channel requests sometimes succeeded without changing the reported channel; a channel-1 then channel-6 workaround worked in one test and is not proof of reliable hopping.
- Preserve track identity, counters, history and alert deduplication across planned channel changes. Update the current fixed-frequency guard deliberately; it must still detect unplanned frequency changes.
- Show the configured channel plan, dwell and observed packet frequency. STOP and terminal exit must cancel capture and any pending retunes before radio restoration.
- Test scheduling, cancellation, failed retunes, mixed-frequency inputs and two independent transmitter tracks offline before any live scan.

DroneCOT documents `WIFI_HOP_CHANNELS` and `WIFI_HOP_DWELL`, with a 6/149 example and 3/1-second dwell. These are upstream DroneCOT keys, not existing Scout settings. Start with that pair as a bounded field-test candidate; measure retune reliability, time to first detection and missed reports before selecting a dwell policy. With one radio, listening on one channel leaves reception gaps on the other. This profile does not establish coverage of every Wi-Fi channel or Bluetooth Remote ID.

Before a live test, explain that Panda will leave its Wi-Fi connection and alternate channels while Intel remains the uplink, then obtain approval. Record packet frequencies, retune outcomes, report timing, track separation, STOP and restoration. Do not transmit test/replayed reports to production TAK.

## Sources

- [Official DroneCOT configuration](https://github.com/snstac/dronecot/blob/main/docs/configuration.md): documented Wi-Fi hop keys and dwell example.
- [Official DroneCOT feeds](https://github.com/snstac/dronecot/blob/main/docs/feeds.md): beacon/NAN Wi-Fi inputs and the optional 6/149 example.
- [Linux Wireless iw documentation](https://wireless.docs.kernel.org/en/latest/en/users/documentation/iw.html): capability inspection and monitor/radiotap metadata.

Original sources and hardware capabilities inspected October 5, 2026; DroneCOT configuration and current USB/PHY capabilities rechecked October 18. Development inspections were read-only; the user subsequently ran the explicitly approved bounded Techkey tests documented above. No production server, firewall, router or driver changes were made.

Later endurance evidence (October 19, 2026): the matching stopped session recorded nine Techkey frames at 5745 MHz/channel 149 and zero channel-149 RID decodes. This establishes RF reception for that run, updating the earlier no-packet/unverified-RF result; it does not establish reception of a Remote ID transmitter on 149. See [the endurance review](laptop-reliability.md#observed-endurance-result--october-19-2026).
