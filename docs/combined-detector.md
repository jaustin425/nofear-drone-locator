# Combined AetherLink Scout detector

Status: implemented and tested with synthetic processes and mock radio commands. A simultaneous three-source hardware dashboard test remains pending. Earlier separate tests verified Mavic Wi-Fi decoding on channel 6, the two-radio recording/restore lifecycle, and legacy Bluetooth discovery; no packets were observed on 149 in the Mavic comparison and no real BLE RID transmitter has yet been decoded.

## Run

Stop the old Scout server with Ctrl+C in its own terminal first. Both USB Wi-Fi adapters must start in managed mode under NetworkManager, Intel must be the preferred connected uplink, and Bluetooth hci0 must already be powered on.

```sh
cd /home/jason/Projects/nofear-drone-locator
sudo -v
bash scripts/capture_dual.sh --live
```

Enter sudo credentials privately. This explicit launcher disconnects only Panda and Techkey, prepares Panda on channel 6 and Techkey on 149, verifies the actual reported channels and starts normal-user Scout at **http://127.0.0.2:8765**. Intel remains the uplink. Bluetooth power is unchanged. No production TAK traffic, installations, sudo policy changes or Python capabilities are involved.

Opening/refreshing the page does not start any capture/discovery. Press **START** once for all three sources:

| Source | Input | On-screen status |
| --- | --- | --- |
| Panda | Fixed Wi-Fi 6, 2437 MHz | packets, decoded/error totals, last packet frequency, state and renewals |
| Techkey | Fixed Wi-Fi 149, 5745 MHz | same; quiet input remains listening, with packet frequency unverified until observed |
| Internal Bluetooth | BlueZ legacy BLE on hci0 | service-data updates, RID candidates, decoded/errors, state and renewals; legacy-only coverage |

The total decoded count is observations across sources, not a unique aircraft count. Correlated aircraft share a track, details, alerts and history; raw per-source observations still contribute to counters. Ordinary Bluetooth updates do not become drone detections. The Bluetooth 4.2 controller does not add Bluetooth 5 extended/Coded PHY coverage, and BlueZ D-Bus service updates may coalesce.

**STOP** cancels renewal and stops every source, confirms source process/thread exit, and keeps map positions/history with continuing freshness ages. STOP leaves both Wi-Fi radios prepared and Bluetooth power unchanged. **Ctrl+C in the launch terminal** stops Scout and waits for its combined supervisor before restoring both USB radios to managed mode/NetworkManager and reconnecting known original profiles. Cached sudo is refreshed noninteractively every 45 seconds while the launcher stays open. Never enter a password in chat.

The direct CLI `.venv/bin/python -m aetherlink_scout --combined` is for separately prepared radios only; it never prepares interfaces or changes channels. Use the launcher for normal field testing so restoration has the original connection context. The older standalone Wi-Fi, optional hopping, Bluetooth-only, simulation and historical replay modes remain available and mutually exclusive.

## Capture and failure behavior

Each source has an independent bounded 120-second process. Wi-Fi uses root tcpdump via terminal-authorized sudo/timeout, with tcpdump dropping capture privileges to the launching user. Bluetooth uses existing Ubuntu dbus/GLib bindings as an unprivileged helper; it never powers, pairs or connects devices. Only confirmed duration expiry renews a source; other errors do not restart automatically. Source renewal preserves the combined session, tracks, counters and alert state. A serialized decoder lock protects process-global output suppression from competing input threads.

All three configured sources are required. Startup failure, wrong-channel packets, driver/radio loss, authorization failure, Bluetooth power loss or unexpected process exit produces a visible error and stops the group. Already-started peers are stopped even if a later source fails. Source status/counters remain visible for diagnosis; a quiet channel alone is not an error. Optional TAK stays disabled in `--live`. The separately selected `--live-tak` output remains independent: TAK errors never stop detection. See [TAK forwarding](tak-forwarding.md).

STOP cancels before waiting, including in-flight startup/renewal. Independent stops are coordinated concurrently. If source exit is unconfirmed, the combined supervisor remains alive as a non-daemon thread, shows an error and retries cleanup; START remains blocked. The launcher skips radio restoration if Scout cannot exit within its grace period or exits abnormally. Individual captures retain their timeout bounds. An abnormal app kill, USB loss or power failure can require manual inspection/restoration; do not blindly rerun a launcher over unconfirmed captures.

The launcher checks the dashboard port and powered Bluetooth before touching either Wi-Fi radio. Both radios' current drivers, separate PHYs, advertised monitor capability, target-channel restrictions, current managed/NetworkManager state, Intel routing and cached sudo are checked read-only first. Panda uses its previously observed channel-1 then channel-6 preparation sequence; no hopping occurs during combined detection. The existing no-argument dual launcher still performs the separate 60-second recording comparison.

## Track correlation and provenance

Reports retain their receiver/transport provenance. A matching **claimed Basic ID and ID type** can join observations across different radio MACs. Unknown identities stay source-specific until there is evidence to associate them; they are not joined just because coordinates are nearby. Late identity can reconcile provisional tracks into one marker pair/history entry, preserving the earlier track key, latest individual field times and provenance. Browser selection and alert references follow the merge without an additional alert. Unrelated ID types, changed identities on a transmitter and grossly disagreeing fresh positions are kept separate and flagged for review. A claimed ID match is not authentication or conclusive personal identification.

A source can initially be unidentified, so multiple provisional tracks/initial alerts are possible before shared identity becomes available. Already-heard tones cannot be undone; reconciliation removes duplicate visual alert references and does not issue another tone. No arbitrary Bluetooth device is labeled a drone merely to make a test positive. Cleared tracks can reappear without another alert under the retained session identity; association memory is bounded along with the existing tracks/history.

Source snapshots share one private history store, with the existing latest-1,000 retention limit. Successful late merging saves the surviving track before removing provisional history rows from that session. History errors remain visible and map clearing retains its existing save-failure guard. Saved reports remain explicitly historical; reported operator addresses/identity are never assumed verified.

Live START-transition snapshots are distinguished from initial/reconnected stream snapshots, so immediate observations can alert once without making refresh/reconnect noisy. Independent source renewal does not suppress alerts from other active receivers. The audible sequence retains the existing four-second alternating tones, mute/STOP/acknowledgement cancellation and no stacking.

## Offline verification

The full Python suite passed 82 tests, including concurrent synthetic input processes, source renewal/failure, correlation/conflicts, cleanup confirmation and mocked launcher restoration. The combined browser check verified three inputs sharing one track and alert, independent renewals, STOP and silent refresh. Existing live Wi-Fi, Bluetooth and scanning browser regressions passed. JavaScript state/location/replay checks and shell syntax checks also passed. These tests changed no physical radio state and establish no additional RF coverage.

## Field verification

- Confirm both radios at 6/149 and hci0 powered before START; keep a known channel-6 Mavic reference and use a known legacy-BLE RID source for positive Bluetooth verification.
- Check all three source rows become running/listening. Record zero/absent reports accurately; absence on 149 is not evidence that the Mavic or another make never uses it.
- Verify operator-first details, receiver provenance, one correlated track/alert for matching reported IDs, separation of different aircraft and reviewable conflict flags.
- Run past 120 seconds and verify independent renewal preserves shared tracks/session/counters. Confirm the alarm still fires for a new track while another input is renewing.
- STOP must freeze all source counters after confirmation while retained positions age. Refresh stays silent; history survives restart and remains labeled saved.
- Exit the launcher and verify both USB adapters restored/connected, Intel preferred routing and Bluetooth power unchanged with Scout discovery released. Record source errors and any cleanup limitation privately.

Hardware results for this combined mode are pending. Development changed no radio state. Never commit raw captures, logs, local locations, certificates or credentials. Do not commit/push this feature without a new explicit checkpoint authorization.

For approved server forwarding, use the optional [TAK output procedure](tak-forwarding.md); capture setup is identical, and historical replay/simulation cannot transmit.
