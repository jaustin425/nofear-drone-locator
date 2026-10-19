# Bounded Panda/Techkey comparison

Status: the first simultaneous bounded capture completed and restored both radios. Panda decoded 168 Remote ID reports on channel 6; Techkey captured no packets on 149. Channel-149 reception remains unverified. This is an offline recording test, separate from the dashboard's single-radio input.

The user approved preparing the comparison after reviewing its impact: both USB radios leave their Wi-Fi connections, Panda listens on channel 6/2437 MHz and Techkey on 149/5745 MHz, while Intel `wlp3s0` remains the preferred uplink. Both captures are bounded to 60 seconds. No channel hopping, production TAK traffic, software installation or system permission changes are involved.

When the authorized Mavic is broadcasting, run in your own terminal:

```sh
cd /home/jason/Projects/nofear-drone-locator
sudo -v
bash scripts/capture_dual.sh
```

Enter the password privately in the terminal. Stop other capture launchers first; both adapters must begin in managed mode under NetworkManager. Leave this terminal open until **both** restoration messages appear. Keep the Mavic broadcasting during the shared capture interval, following your normal authorized test procedure. Ctrl+C cancels both captures before restoration.

The launcher checks both known interfaces/drivers, distinct PHYs, advertised monitor support, enabled/unrestricted target channels, original connection UUIDs and Intel's preferred route before changing either radio. Both target channels are verified before either capture starts. Panda preparation uses the previously observed channel-1 then channel-6 sequence while up; Techkey receives only the requested channel-149 retune. The script does not alter regulatory settings or change Intel's interface.

Review [the launcher](../scripts/capture_dual.sh) for exact preparation and restoration commands. Each capture uses:

```sh
sudo -n timeout --signal=INT --kill-after=3s 60s \
  tcpdump -i INTERFACE -nn -U -s 0 -w - -Z USER \
  '(wlan[0] & 0xfc = 0x80) or (wlan[0] & 0xfc = 0xd0)'
```

Here INTERFACE is the inspected Panda or Techkey interface and USER is the launching normal user. Python is not run with privileges. The two capture processes launch concurrently, a short interval apart; they are not hardware-synchronized. The launcher records UTC launch timestamps on the same host clock. Analyse the common interval rather than treating the entire files as exactly simultaneous.

Private ignored `local-data/dual-capture-*` directories contain separate `panda.pcap`/`techkey.pcap`, diagnostics, prepared/final channel snapshots and a test log. New directories/files use modes 0700/0600. Share only final status and the directory path; do not paste packet contents, IDs, coordinates or credentials. Separate capture files preserve receiver provenance for offline analysis.

Only confirmed timeout status 124 is accepted as normal completion for each process. Any unexpected exit cancels its peer. Both process exits must be confirmed before either radio is restored. Preparation failures restore every radio already touched. The script restores managed mode/NetworkManager and reconnects each known original connection, reporting partial failures. Sudo expiry may require a private terminal refresh for restoration. An unconfirmed capture exit skips restoration and reports the remaining bound; hard kills, power loss or USB removal can prevent cleanup. Inspect state before manual recovery rather than blindly rerunning.

After capture, read-only checks are:

```sh
iw dev wlx9cefd5f67d4c info
iw dev wlx1cbfce038535 info
ip route show default
```

Offline review should verify usable packets and observed frequencies per radio, decode counts/errors, valid channel-6 reference reception during the shared window, matching transmitter/Remote ID identity across files without exposing identifiers, report timing and restoration. Compare payload identity and timing rather than assuming MAC addresses are identical across bands. No channel-149 reports means **not observed during that interval**; it does not prove that the aircraft never uses 149, nor independently establish a working channel-149 receive path when the capture is empty. General channel-149 traffic can provide additional receive-path evidence, but this beacon/action-filtered recording is not a comprehensive RF survey.

The Mavic's prior verified channel-6 reports do not promise simultaneous channel-149 transmission. This test does not implement dual-input dashboard tracking or alert deduplication. Remote ID positions and identities remain reported data, requiring independent corroboration for operational conclusions.

Five mock tests cover concurrent bounded processes/private files, all-radio preflight rejection, partial preparation failure, peer cancellation on capture failure and signal cleanup ordering. Shell syntax and whitespace checks passed. No real radio was changed during development. The first hardware result is recorded below; channel-149 reception remains unverified.

## First simultaneous result — October 18 local time

The user ran the approved launcher during the Mavic test. UTC preparation/cleanup timestamps were 2026-10-19 00:17:45–00:19:13. Shell launch timestamps were 00:17:47.473677287 for Panda and 00:17:47.475181193 for Techkey, approximately 1.504 ms apart. Each capture ended with the expected timeout status 124; script status was 0. The nominal shared window from launch timestamps and 60-second bounds is approximately 59.9985 seconds. Those timestamps do not prove hardware-synchronized listening boundaries.

| Receiver | Channel | Captured frames | Remote ID reports | Tracks | Kernel-reported drops |
| --- | --- | ---: | ---: | ---: | ---: |
| Panda | 6 / 2437 MHz | 5,677 | 168 | 1 | 0 |
| Techkey | 149 / 5745 MHz | 0 | 0 | 0 | 0 |

Panda's frames all reported 2437 MHz: 5,630 beacons and 47 action frames. It decoded 168 reports with zero decode errors, rejected reports or unidentified reports. All identified reports fell inside the nominal shared window. Aircraft positions appeared in 168 reports and reported operator positions in 161. Packet span was 59.393 seconds; Remote ID span was 59.196 seconds. Tcpdump reported 5,744 packets received by its filter; the PCAP was 2,071,073 bytes. Positions are reported data, not surveyed accuracy or identity evidence.

Techkey's PCAP was 24 bytes (header only), with no observed packet frequencies or decodable reports. Prepared and final snapshots confirmed monitor mode and the requested 149/5745 MHz setting, but no traffic independently established receive operation on 149. No matching Basic IDs across files were available to compare. This is **not observed on 149 during this interval**, not proof that the Mavic never transmits there or that Techkey is unable to receive there.

Read-only follow-up confirmed both adapters restored to managed mode and connected, with Intel still selected by the route lookup. Artifact directory/file modes were 0700/0600. Private aggregate results are saved in ignored `local-data/dual-capture-IeVY3yPt/summary.json`; no packet contents, identifiers or coordinates were printed or uploaded. No TAK traffic ran.

The coordinated capture/cleanup lifecycle is now observed on hardware. Keep channel 6 as the proven Mavic path. Before relying on a second receiver on 149, establish its reception using a known channel-149 source under a separately approved test; do not change the home router automatically to create one. Dual-input dashboard operation remains unimplemented and untested.
