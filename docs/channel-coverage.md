# Proposed channel coverage

Status: planning only. Scout still captures on channel 6; no hopping is implemented or enabled by this document.

## Resumed checkpoint — October 5, 2026

Checkpoint `b12b11b` was clean on inspection. The laptop had rebooted. Two local history entries and the ignored location preset remained present; a fresh temporary Scout process successfully displayed saved historical details and markers without capture or external requests. The temporary process was stopped afterward.

Panda `wlx9cefd5f67d4c` uses `mt76x0u` and currently advertises monitor support. Its PHY index changed after reboot, so discover the PHY from the interface rather than hardcoding it. Panda was connected in managed mode on channel 44; Intel `wlp3s0` retained the default route. This observation does not establish whether the earlier launcher exit restored Panda before shutdown.

The inspected capability report enables 2.4 GHz channels 1–11, with 12–14 disabled. Channels 6/2437 MHz and 149/5745 MHz are enabled without radar/no-IR flags. The adapter supports one channel at a time. Advertised capability is not a successful live retune or Remote ID reception test on channel 149.

## First proposed implementation

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

Sources and hardware capabilities inspected October 5, 2026. No radio, production server, firewall or router changes were made.
