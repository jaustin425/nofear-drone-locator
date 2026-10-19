# Bounded Techkey capture test

Status: Techkey monitor reception is verified on channel 44, and the subsequent authorized Mavic test decoded 189 Remote ID reports on channel 6 with zero decode errors. Restoration was confirmed after both successful runs. Channel 149 and simultaneous dual-radio operation remain unverified. Read-only inspection found USB `0bda:b812`, `rtl88x2bu`, interface `wlx1cbfce038535`, with monitor support and channel 6 advertised. PHY indices can change.

The user approved this capture test after discussion of the connection impact. It disconnects **only Techkey** from Wi-Fi, switches it to monitor mode on channel 6/2437 MHz, records beacon/action frames for 60 seconds, confirms capture exit, then returns it to managed mode/NetworkManager and restores a known original connection. Intel `wlp3s0` remains the uplink; Panda is not reconfigured. No dashboard, channel hopping, TAK output, software installation or system permission changes are involved.

Run in your own terminal so sudo authentication stays private:

```sh
cd /home/jason/Projects/nofear-drone-locator
sudo -v
bash scripts/capture_techkey.sh
```

Review [the script](../scripts/capture_techkey.sh) for the exact preparation, capture and restoration commands. It checks the interface/driver, advertised monitor/channel support, starting managed/NetworkManager state, Intel connectivity and sudo authorization before changing anything. It verifies actual monitor mode/channel 6 before capturing. A failed channel change triggers restoration without capture. It does not try Panda's channel-1 workaround on Techkey.

The capture command is:

```sh
sudo -n timeout --signal=INT --kill-after=3s 60s \
  tcpdump -i wlx1cbfce038535 -nn -U -s 0 -w - -Z "$(id -un)" \
  '(wlan[0] & 0xfc = 0x80) or (wlan[0] & 0xfc = 0xd0)'
```

The script redirects packets and diagnostics to a new private directory in ignored `local-data/techkey-capture-*` (0700 directory, 0600 files). It uses normal-user shell/Python and tcpdump drops capture privileges to the launching user. GNU timeout status 124 is expected for the completed one-minute capture; other outcomes are reported as incomplete tests. It does not treat completion as proof of received frames or successful Remote ID decoding.

A Mavic broadcast is optional for the first test: ambient Wi-Fi frames can establish basic monitor reception. Aircraft reports require an authorized broadcasting aircraft and separate decoding verification. Do not upload raw PCAPs or paste packet contents, identifiers or locations. Share the final status and local artifact directory only; inspect aggregates locally afterward.

After completion, verify read-only:

```sh
iw dev wlx1cbfce038535 info
ip route show default
nmcli -g GENERAL.STATE device show wlx1cbfce038535
```

Confirm managed mode, restored connection when known, and the Intel default route. If restoration reports an error, stop and inspect the current state before retrying. The script will not restore the radio while capture exit is unconfirmed. A hard kill, power loss or USB removal can prevent cleanup; traps cannot guarantee restoration in those cases.

Four mock tests cover preflight failures without mutations, bounded capture/private artifacts, failed or stuck channels, and capture exit before restoration on interruption. Shell syntax and whitespace checks also pass. Keep real test results separate from these offline checks.

## First real test — October 18, 2026

The user ran the prepared script. Capture diagnostics reported IEEE802_11_RADIO/radiotap link type, 0 packets captured, 0 received by the filter and 0 kernel drops. The resulting PCAP was 24 bytes (header only); there were no frames to decode or verify observed frequency. Timeout exit status was the expected 124 and script exit status was 0. Test/cleanup timestamps were 23:54:20–23:55:36 UTC, including preparation/restoration around the 60-second bound.

Read-only follow-up confirmed Techkey in managed mode, connected on channel 44, and Intel retaining the preferred default route. New artifact directory/file modes were 0700/0600. Aggregate results are stored privately in ignored `local-data/techkey-capture-SaYDz9XU/summary.json`. No raw capture, identifiers or coordinates were uploaded or printed.

This verifies the preparation/capture-process/restoration lifecycle; it does **not** verify monitor reception or rule out a filter, channel, driver or reception issue. An absent Mavic would explain absent Remote ID, but does not by itself explain zero ambient Wi-Fi packets. The loaded external module is named `88x2bu` (driver name `rtl88x2bu`), version `v5.13.1-20-gbd7c7eb9d.20210702_COEX20210316-18317b7b`. Installed source advertises `CONFIG_WIFI_MONITOR = y`; that is not evidence of a functioning receive path.

A useful next diagnostic would be a separately approved short, unfiltered, truncated-frame capture on the known active channel 44. It would change only Techkey again and retain the same restoration safeguards. That can help separate a beacon/action filter or channel-6 issue from a broader monitor receive problem. The explicit diagnostic profile below was then prepared and tested with mock commands; its later hardware result is recorded below. Do not change driver modules, system parameters or install a replacement based solely on this empty capture. The [driver maintainer documentation](https://github.com/RinCat/RTL88x2BU-Linux-Driver/blob/master/README.md) distinguishes the external `88x2bu` module from newer in-kernel drivers; no driver change is part of this test.

## Approved follow-up: channel-44 receive diagnostic

The user approved this follow-up after the empty channel-6 result. It disconnects only Techkey again and uses the same monitor preparation, actual-channel verification and restoration safeguards. Intel and Panda are not reconfigured.

Run in your own terminal, entering sudo credentials privately:

```sh
cd /home/jason/Projects/nofear-drone-locator
sudo -v
bash scripts/capture_techkey.sh --diagnostic
```

This profile selects channel 44/5220 MHz, where Techkey's managed connection was observed working. It captures without a packet filter for at most 20 seconds or 10,000 frames, whichever comes first. Each frame is truncated to 96 bytes, bounding the raw PCAP to about 1.12 MB plus its global header. Truncation reduces retained data but is not anonymization; addresses and portions of packet contents can remain. Artifacts are private/ignored and must not be uploaded or dumped into chat. This is a basic receive-path diagnostic; the truncated data is not suitable for a full Remote ID decoding test.

The capture command selected by the script is:

```sh
sudo -n timeout --signal=INT --kill-after=3s 20s \
  tcpdump -i wlx1cbfce038535 -nn -U -s 96 -w - -Z "$(id -un)" -c 10000
```

Timeout status 124 is expected at the time limit; status 0 can indicate the packet-count limit was reached first. Other exits are reported as incomplete tests. Inspect the private capture/log offline to verify actual frame count, recorded link type, observed frequencies (when radiotap includes them), truncation and restored connectivity. A completed process by itself remains insufficient evidence of reception. The known-active channel and lack of filter help narrow the earlier empty result; the changed channel and filter mean this test alone cannot distinguish those two causes if it succeeds.

Seven mock tests cover both profiles, including diagnostic command bounds/truncation/no filter, restricted/stuck/failed channel selection, preflight protection, early packet-limit exit and restoration after capture interruption. No driver installation, module reload or system setting change is included.

## Channel-44 result — October 18 local time

The user ran the approved diagnostic. UTC preparation/cleanup timestamps were 2026-10-18 23:59:26 through 2026-10-19 00:00:03; capture itself was bounded to 20 seconds. Timeout status was 124 and script status 0. The private PCAP contains 7,537 frames over 19.462 seconds, 413,988 bytes, with radiotap link type 127. All 7,537 frames report 5220 MHz. Exactly 512 frames were truncated; none exceeded the configured 96-byte snapshot length and no parsing errors were observed.

Tcpdump reported 7,537 packets captured, 7,718 received by its filter and zero kernel-reported drops. These are distinct counters; zero kernel drops is not proof of no RF/driver losses. Offline application of the original beacon/action filter matched 405 frames: 375 beacons and 30 action frames. This establishes that the filter can match this driver's recorded channel-44 frame format; it does not establish why the earlier channel-6 capture was empty.

Read-only follow-up confirmed Techkey restored to managed mode, connected on channel 44, with Intel retaining the preferred default route. Artifact directory/files were 0700/0600. Aggregate results are private in ignored `local-data/techkey-capture-bsP5pGfS/summary.json`; no raw frame data, identifiers or coordinates were printed or uploaded.

This verifies basic Techkey monitor reception on channel 44 and successful restoration for this run. It does not verify Remote ID decoding, monitor reception on channel 6/149, continuous operation or a two-radio Scout configuration. Next, perform an explicitly approved full-frame Remote ID test with the authorized Mavic broadcasting; establish reception on the intended second-radio channel before implementing dual-radio operation. No driver replacement is indicated by this successful receive test alone.

## Mavic Remote ID result — October 18 local time

The user ran the full-frame, beacon/action-filtered channel-6 test during the authorized Mavic test. UTC preparation/cleanup timestamps were 2026-10-19 00:06:14–00:07:30, around a 60-second bounded capture. Timeout status was 124 and script status 0.

Offline inspection found 189 captured packets, all beacon frames with radiotap frequency 2437 MHz, over 59.203 seconds. All 189 decoded as Remote ID reports, forming one transmitter track, with zero decode errors, rejected reports or unidentified reports. Aircraft positions were present in 189 reports; reported operator positions in 137. These are field-presence counts, not independently surveyed accuracy or operator identity verification. The capture was 34,422 bytes, with private 0700/0600 directory/file modes. Tcpdump reported 191 packets received by its filter and zero kernel-reported drops; those counters do not establish absence of RF/driver losses.

Read-only follow-up confirmed Techkey restored to managed mode and connected on channel 44, with Intel retaining the preferred default route. Aggregates are saved privately in ignored `local-data/techkey-capture-wosfhOjW/summary.json`. No decoded identifiers, coordinates or raw packet contents were printed or uploaded; TAK remained unused.

This establishes that Techkey and the existing Scout decoder can receive/decode channel-6 Remote ID during this Mavic test. It does not establish simultaneous broadcasts on channels 6/149. A future approved comparison should keep a known-working receiver on 6 as a reference while testing the second receiver on 149 during the same broadcast interval. First verify the second receiver actually tunes and receives on its target channel; a missing report on 149 is an observation for that interval, not proof the aircraft never uses it. Simultaneous capture/cleanup and dual-input dashboard integration are separate, still unimplemented work.
