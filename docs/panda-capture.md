# Bounded Panda capture

The user approved disconnecting only Panda and switching it to monitor mode. The first completed test recording decoded 185 reports; afterward Panda was observed connected in managed mode and Intel retained the default route. Internal Intel `wlp3s0` is the uplink; the inspected Panda is `wlx9cefd5f67d4c`, driver `mt76x0u`. Recheck hardware and connectivity before each test.

When the authorized aircraft is broadcasting, run from the repository in your own terminal:

```sh
cd /home/jason/Projects/nofear-drone-locator
sudo -v
bash scripts/capture_panda.sh
```

Enter the sudo password only in that terminal. The script uses cached sudo authentication and stops before interface changes if it is unavailable. It checks Intel connection/default route, Panda driver, managed/monitor state and an original Panda connection UUID when available. No sudo installation, capability grant or system Python permission change is needed.

The privileged preparation operations in `scripts/capture_panda.sh` are shown below. Disconnection runs only for a known active managed connection. An existing monitor radio skips disconnection and mode switching; retuning runs only if channel 6/2437 MHz is not already confirmed:

```sh
sudo -n nmcli --wait 10 device disconnect wlx9cefd5f67d4c
sudo -n nmcli device set wlx9cefd5f67d4c managed no
sudo -n ip link set wlx9cefd5f67d4c down
sudo -n iw dev wlx9cefd5f67d4c set type monitor
sudo -n ip link set wlx9cefd5f67d4c up
sudo -n iw dev wlx9cefd5f67d4c set channel 1
sudo -n iw dev wlx9cefd5f67d4c set channel 6
sudo -n timeout --signal=INT --kill-after=5s 60s \
  tcpdump -i wlx9cefd5f67d4c -nn -U -s 0 -w - \
  '(wlan[0] & 0xfc = 0x80) or (wlan[0] & 0xfc = 0xd0)'
```

The script redirects binary capture and diagnostics into a new ignored `local-data/panda-capture-*` directory, never the terminal. The filter selects beacon/action management frames, not just Remote ID, so keep captures local. Channel 6 is the first test based on the earlier successful capture; no reception on that channel does not establish absence of Remote ID. No packets are injected and no TAK connection is opened.

An exit/interrupt handler attempts to restore Panda to managed mode, return it to NetworkManager and reactivate its original connection UUID when known. Restoration failures are reported; Intel is never changed by the script. Avoid unplugging Panda or closing the terminal mid-test. After completion confirm:

```sh
iw dev
ip route show default
nmcli -f DEVICE,TYPE,STATE,CONNECTION device status
```

Decode the resulting file locally (replace the generated directory name):

```sh
.venv/bin/python scripts/replay_summary.py local-data/panda-capture-XXXXXXXX/capture.pcap
```

Confirm reports decode and compare aircraft/operator positions and timestamps against the known test. Stop the existing replay server with Ctrl+C in its launch terminal, then display this new recording:

```sh
.venv/bin/python -m aetherlink_scout --capture local-data/panda-capture-XXXXXXXX/capture.pcap
```

Open **http://127.0.0.2:8765** and select **Play replay**. This procedure produces a historical replay after capture. For continuous reception, use the separate START/STOP procedure below. GPS, geocoding and TAK are optional. If no reports decode, retain the local capture and check channel/format/broadcast conditions before repeating; do not infer absence of Remote ID.

Preparation checks passed: Bash syntax and offline filter selection of synthetic beacon/action frames while excluding data frames. The first local capture decoded successfully, with zero reported kernel-dropped packets; restored managed mode/connectivity was verified afterward. Interrupted/error-path restoration is not yet verified. Record each test's conditions using the field checklist. A Remote ID report alone is not proof of a violation.

## Continuous START/STOP test

The new live controls have been tested using synthetic and saved input. The first live hardware attempt received Wi-Fi frames but no decoded Remote ID. After the successful manual channel-1 then channel-6 retune, the user reports live Mavic reception worked; read-only receiver counters corroborate it. The original channel mismatch mechanism remains under investigation. Before running, review that Panda disconnects from home Wi-Fi, is set to channel 6, and remains in monitor mode until the launch terminal exits; verify its channel before START. Intel retains connectivity. Stop the existing local dashboard process first so port 8765 is free.

```sh
cd /home/jason/Projects/nofear-drone-locator
sudo -v
bash scripts/capture_panda.sh --live
```

The launcher supports connected or disconnected managed Panda and adopts a prepared monitor Panda. It checks port availability, driver, Intel connectivity and cached sudo before changes. It prints the impact: only Panda is prepared, retunes request channel 1 then 6 while up when needed, and exit returns Panda to managed mode/NetworkManager with its original connection restored when known. Channel 6/2437 MHz is verified before launching normal-user Python with `--live-interface wlx9cefd5f67d4c`. The browser never changes radio state. Opening the page does not start capture. In **http://127.0.0.2:8765**, **START** checks the monitor radio and Intel default route, then starts this exact bounded capture command (the current normal username is used for `-Z`):

START also verifies channel **6 / 2437 MHz**. If the radio is elsewhere, it fails before sudo/capture, without automatically retuning. During capture, received radiotap frequency is displayed and a frequency mismatch stops capture with an explicit error. Unknown/missing frequency metadata is labeled unverified. [Linux Wireless documentation](https://wireless.docs.kernel.org/en/latest/en/users/documentation/iw.html#modifying-monitor-interface-flags) describes radiotap metadata for monitor input.

```sh
sudo -n timeout --signal=INT --kill-after=3s 120s \
  tcpdump -i wlx9cefd5f67d4c -nn -U -s 0 -w - -Z jason \
  '(wlan[0] & 0xfc = 0x80) or (wlan[0] & 0xfc = 0xd0)'
```

Binary packets go directly to the local decoder, never the terminal/browser or TAK. `-Z` drops tcpdump privileges to the normal user after opening capture. The app itself remains unprivileged and grants no capabilities. START stays active across confirmed 120-second segment expiries: only exit code 124 is renewed, with radio/route/authorization rechecked before each successor. Session ID, tracks, field ages, cumulative counters and alerts are preserved. Renewal can create a brief reception gap; unexpected EOF, other exit codes or prerequisites failures stop detection visibly. See [GNU timeout exit-status documentation](https://www.gnu.org/software/coreutils/manual/html_node/timeout-invocation.html). Capture stdout/stderr are separated; diagnostics go to ignored `local-data/scout-capture-*.log`. This mode does not save raw PCAPs. Reading is bounded and cancellable, including when no packets arrive. Unsupported/truncated streams show a generic error without payload contents. The stream supports classic Wi-Fi PCAP (radiotap or 802.11); replay/simulation additionally support PCAPNG.

**STOP** signals the capture command, confirms its exit, and leaves retained markers aging. Panda remains prepared for another START. A fresh START resets the detection session. If stop cannot be confirmed, the UI reports it, retains STOP for retry, and the independent 120-second process limit remains. **Ctrl+C in the launcher terminal** signals Scout, waits for its shutdown, and then restores Panda to managed mode/NetworkManager; a known original connection UUID is reactivated. If app shutdown cannot be confirmed, radio reconfiguration is skipped and the failure is reported. Prepared-monitor adoption does not assume an original active Wi-Fi profile. Closing/refreshing the browser does not stop detection or restore Wi-Fi.

The live launcher keeps this terminal's existing sudo authorization refreshed using `sudo -n -v` every 45 seconds while open, stopping that helper on exit. It never stores a password or modifies sudo configuration. Initial authorization remains `sudo -v` entered privately. If noninteractive refresh or capture authorization fails, the terminal/dashboard reports it; renewal does not spin or request a password in the browser. If restoration needs authentication after authorization loss, only the launcher terminal may ask privately; noninteractive restoration instead reports failure. Do not paste passwords or grant passwordless sudo/system Python privileges.

For the next test, verify START produces current reports and a single audible tone for the track, position updates preserve operator-first details, STOP halts report counts, markers become stale after 30 seconds, a new START works, and terminal exit restores managed mode/connectivity. Run for at least three minutes to verify renewal after the first 120-second segment: the renewal counter should rise while packet/report totals and track identity remain continuous. Then verify manual STOP and another START. Check the same `iw`, route and NetworkManager outputs shown above after exit. Do not infer receiver coverage from this single-channel test.

### Channel-mismatch retry

The first live UI test decoded no reports despite receiving management frames; Panda was subsequently found on channel 44. The earlier successful capture contains Remote ID exclusively at 2437 MHz. The failed live mode did not archive packets/frequency, so the precise cause/timing of a channel change is not established. A subsequent manual channel-6 command returned success while `iw` still reported channel 44. Panda was unmanaged/disconnected and Intel retained its connection. The launcher now verifies channel 6/2437 MHz before launching capture or the dashboard; failure invokes its Panda restoration handler. No system network services are stopped as a remedy.

Exit the current launcher with Ctrl+C to restore Panda, then relaunch the updated code as above. Just before START, verify:

```sh
iw dev wlx9cefd5f67d4c info
```

If it no longer shows channel 6, retuning affects only the already prepared Panda monitor radio; Intel must remain connected. Review that impact before running this manually in a separate terminal, entering sudo authentication only there:

```sh
sudo iw dev wlx9cefd5f67d4c set channel 6
iw dev wlx9cefd5f67d4c info
```

Verify channel 6/2437 MHz and click START promptly. Check that the dashboard's packet frequency stays at 2437 MHz. If it changes again, stop the test and preserve aggregate diagnostics; investigate the radio/controller before changing services, drivers or broader network settings. Remote ID absence on the wrong channel is not a decoder/display result.

If the command succeeds but the reported channel remains 44, do not keep repeating START. A down-state retune was attempted locally and returned `Device or resource busy (-16)`. The `&&` command chain then skipped bringing Panda up; a read-only check confirmed Panda was down while Intel remained connected. Do not repeat that sequence. Linux 6.8 [requires at least one running monitor interface](https://github.com/torvalds/linux/blob/v6.8/net/wireless/core.h) for the monitor-channel setter; [the setter returns EBUSY](https://github.com/torvalds/linux/blob/v6.8/net/wireless/chan.c) if that condition is not met. This explains the down-state error and does not establish the cause of the original channel-44 mismatch.

With STOP selected and the launch terminal left open for restoration, bring only Panda back up and inspect its channel. This restores the already approved monitor interface; it leaves Intel untouched and does not start capture:

```sh
sudo ip link set wlx9cefd5f67d4c up
iw dev wlx9cefd5f67d4c info
ip -brief link show wlx9cefd5f67d4c
```

If bringing it up fails, stop and report the error. Inspect the reported channel before choosing another retune or live test; the effect of this down/up cycle remains unverified. If channel 44 remains, preserve the output for further driver/controller diagnosis before another flight. Do not restart NetworkManager, unload drivers, or enable active monitor mode as an unreviewed workaround.

The user brought Panda back up successfully; it still reported channel 44. The down/up cycle did not resolve the mismatch, and the dashboard confirmed detection inactive. Both channels 1 and 6 are permitted by the current Panda channel listing. The user then requested channel 1, followed by channel 6, while keeping the monitor interface up. Both requests worked: `iw` showed 1/2412 MHz, then 6/2437 MHz. A subsequent read-only inspection independently confirmed Panda in monitor mode on 6/2437 MHz and Intel retaining the default route. Linux 6.8 [returns success immediately for a matching cached monitor-channel request](https://github.com/torvalds/linux/blob/v6.8/net/mac80211/cfg.c#L831-L861); the successful intermediate request is consistent with that hypothesis, but does not prove the cause of the original mismatch.

The successful manual sequence is recorded below. Before repeating it, review and approve the impact: only Panda is briefly retuned, Intel remains the uplink, and detection stays stopped. Keep the launch terminal open. The updated launcher now applies this tested intermediate retune only during explicit preparation when channel 6 is not already confirmed:

```sh
sudo iw dev wlx9cefd5f67d4c set channel 1
iw dev wlx9cefd5f67d4c info
sudo iw dev wlx9cefd5f67d4c set channel 6
iw dev wlx9cefd5f67d4c info
```

Record both outputs and any errors. Do not start detection until the final output confirms channel 6/2437 MHz. If both requests leave channel 44 unchanged, do not automate this workaround; investigate further before another flight.

With channel 6 now confirmed, the next step is a 30–60-second live UI reception test with the user's Mavic broadcasting Remote ID. The existing dashboard may retain the previous channel-44 error until START; START rechecks the radio and starts a fresh session. Keep the launch terminal open. Verify packet frequency remains 2437 MHz, packet and decoded-report counters increase, operator/aircraft details update, and the first new track produces one audible alert. Press STOP and confirm both counters stop increasing while retained markers continue aging. Record aggregate counts, frequency and any error without publishing identifiers or coordinates. The user subsequently confirmed that this live Mavic test worked. Read-only `/api/health` inspection corroborated 408 decoded reports from 4,397 packets, zero errors/rejected/unidentified reports, received frequencies `[2437]`, and stopped/inactive capture with stable counts across two reads. The sanitized, timestamped aggregate record is saved in ignored `local-data/live-test-diagnostics/`. Individual marker observations, audible tone, manual STOP versus automatic capture completion, exact test duration, repeated START and radio restoration remain to be recorded. That successful test used the earlier bounded implementation via direct `--live-interface`; stopping a direct server does not restore the radio. The updated launcher/continuous renewal now needs a separate hardware check.


## Clear old tracks and saved history

Clear old tracks saves the latest snapshot, removes only tracks with no report for 30 seconds, and preserves active tracks, My Location, detection and counters. It does not delete history; a history-saving failure leaves the map unchanged. Current-view changes synchronize to other dashboard tabs. A fresh report can make the transmitter reappear without a duplicate alert in the retained session.

Live history persists in ignored `local-data/scout-history.sqlite3` (0600 on creation), retaining up to 1,000 latest track/session snapshots with bounded trails. Updates coalesce to five seconds per track and flush on STOP, completion, clearing and eviction. These are reported snapshots rather than a full packet archive. Simulation history stays separate in memory. One existing retained Mavic snapshot was migrated locally before restarting the new implementation; identifiers and coordinates were not printed or uploaded.

Use History to select a saved aircraft/operator pair. The right panel says SAVED and shows recorded times; dashed markers are historical. Return to live view removes the saved overlay. Historical driving/address actions require explicit confirmation. In the field, verify history survives server restart and never confuse a saved operator point with a current controller fix.

Local verification passed receiver renewal/cancellation tests using unprivileged synthetic processes, mock launcher paths, history persistence/retention/origin checks, and isolated browser checks for stale-only clearing, saved-map review, location preset and existing controls. Real Panda renewal was observed in the subsequent test below; revised launcher exit cleanup remains unverified.


## Continuous Mavic test — October 4, 2026

The user completed a live Mavic flight. Read-only local receiver inspection at 2026-10-05 02:01:47 UTC (October 4 locally) observed 804 decoded reports from 24,756 Wi-Fi packets, zero decode/rejected/unidentified errors, one retained transmitter track and no history-saving error. The session had been listening for 535 seconds and reached five capture segments: four renewals beyond the original 120-second limit. All observed radiotap frequencies were 2437 MHz. Four completed segment logs reported zero kernel-dropped packets; the fifth segment was still active. The local history API reported two saved track/session snapshots, including the earlier preserved live snapshot. This confirms continued capture beyond the earlier two-minute limit; uninterrupted reception during subprocess transitions has not been measured.

Panda remained in monitor mode on channel 6, with Intel connected and retaining the default route. Detection was still active after the aircraft test; the last Remote ID report was already stale. A later read showed the same 804 report count and one retained track while Wi-Fi packet totals continued increasing. Flight completion does not stop Scout: use STOP explicitly. No capture, channel, route or production TAK setting was changed by the inspection. TAK output remained disabled.

Aggregate results were saved privately in ignored `local-data/live-test-diagnostics/continuous-live-20261005T020147Z.json`; no identifiers, coordinates or packet payloads were printed or included. The user subsequently confirmed hearing the detection tone and seeing the expected positions and information, and explicitly confirmed STOP had not been pressed. This is a user-observed display check, not a surveyed position-accuracy measurement. The user later pressed STOP and confirmed counters stopped increasing. A read-only check corroborated stopped/inactive detection, 27,202 packets, 804 decoded reports, zero decode/rejected/unidentified errors and six completed/started capture segments with no history error. The user subsequently confirmed old tracks clear and History works as intended. Clearing and saved-report review are confirmed by user observation; retention across a server restart was not explicitly reported. After the October 5 reboot, two saved entries remained on disk; a fresh temporary Scout process displayed saved details and historical markers with no capture or external requests. Repeated START and terminal-exit restoration remain pending field confirmation. Panda was observed connected in managed mode after reboot, which does not independently prove the launcher restoration path ran before shutdown. Verify STOP makes both packet/report counts stable, then clear the stale pair and confirm its saved History entry remains available. Do not mistake a historical operator point for a current fix.


### More noticeable audible alert

The original short tone was heard during the live test. The updated alert uses eight alternating low/high pulses over four seconds, once for newly observed tracks; repeated reports remain silent. A new detection replaces an active sequence so sounds do not stack. Mute, STOP or acknowledgement cancels it immediately; replay pause/seek/restart also cancels it. The Enable sound button is directly below START/STOP in streaming mode. Refresh the page to load the sound change, then START or Enable sound to arm browser audio. Offline rendered-waveform checks verify multiple distinct pitches, four-second duration, immediate cancellation, clean completion and no stacked gain; the user subsequently reported the updated alert working well.
