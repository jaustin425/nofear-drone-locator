# Field-test checklist

Keep completed records and raw captures in ignored `local-data/`; share only sanitized summaries.

- [ ] Record UTC date/time, test location, observer, authorized aircraft/flight context, software versions, adapter chipset/driver, interface, input transport and channel.
- [ ] Confirm independent network connectivity or obtain approval after explaining Wi-Fi interruption/channel impact. Record the agreed restoration steps before capture.
- [ ] Verify local decode first; distinguish aircraft location, operator/system location and sensor location. Record absent/invalid fields, coordinate and altitude references, timestamp age, IDs and update intervals.
- [ ] For continuous mode, run beyond 120 seconds and verify capture renewal preserves the session, counts, track identity and one alert per newly observed track. Verify the four-second alternating-tone alert, immediate silence on mute/STOP/acknowledgement, manual STOP/repeated START, mute/re-arm, report updates, receiver/error status, stopped counts, retained/stale positions and silent refresh/reconnect. Verify a capture/authorization failure stops renewal visibly. Closing the browser does not stop detection; exit the launch terminal to stop the app before radio restoration.
- [ ] Verify Clear old tracks removes only pairs without a report for 30 seconds, preserves active pairs/My Location, and leaves detection/counters/history intact. Confirm saved snapshots survive server restart, History selection shows recorded times and historical markers, Return to live view restores current details, and external address/directions actions identify saved destinations as historical.
- [ ] Compare with known aircraft/flight observations; record mismatches, duplicate tracks, reception gaps and uncertainty. Failure to receive a broadcast does not establish absence of a drone.
- [ ] For the separately planned TLS test, confirm dedicated sensor certificate, CA trust, hostname verification and test identity. Keep credential contents out of logs and reports.
- [ ] Verify TLS connection and server delivery using the existing configuration, then verify aircraft/operator markers, labels, positions, timestamps, track separation and stale behavior in TAK Aware. Record client version and group visibility; do not assume transport success proves client rendering.
- [ ] Label replay/synthetic tests clearly and avoid production replay by default. Bound live test duration and record unexpected traffic/errors.
- [ ] Treat Remote ID as reported data that can be incomplete, erroneous or spoofed. Record independent corroboration and applicable context before drawing conclusions; a report alone is not proof of a violation or personal identity.
- [ ] Stop capture, confirm original connectivity/interface state is restored, and record outcome, limitations and next steps. Do not upload raw data or secrets, commit, or push.
