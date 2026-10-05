"""Export and validate historical CoT locally; no networking or live workers."""
import argparse
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timedelta, timezone
import io
import json
import math
from pathlib import Path
import xml.etree.ElementTree as ET

from scapy.layers.dot11 import Dot11, RadioTap  # noqa: F401
from scapy.utils import PcapReader
from dronecot.functions import rid_op_to_cot_xml, rid_uas_to_cot_xml
from dronecot.rid_normalize import bytes_to_rid_dict
from dronecot.wifi_parse import extract_odid_from_scapy_packet
from replay_summary import position_state

ROOT = Path(__file__).resolve().parents[1]
STALE_SECONDS = 120


def iso(value):
    return value.isoformat(timespec="microseconds").replace("+00:00", "Z")


def historical_event(data, kind, captured):
    lat_key, lon_key, alt_key, converter = (
        ("Latitude", "Longitude", "AltitudeGeo", rid_uas_to_cot_xml)
        if kind == "aircraft" else
        ("OperatorLatitude", "OperatorLongitude", "OperatorAltitudeGeo", rid_op_to_cot_xml)
    )
    if position_state(data, lat_key, lon_key) != "in_range":
        return None, "position_review"
    normalized = dict(data)
    altitude = data.get(alt_key)
    altitude_unknown = altitude is None
    if altitude is not None:
        altitude_unknown = not math.isfinite(float(altitude)) or float(altitude) in {-1000.0, 9999999.0}
    if altitude_unknown:
        normalized[alt_key] = 9999999.0
    config = {"COT_HOST_ID": "AETHERLINK-SCOUT-HISTORICAL-REPLAY", "COT_STALE": str(STALE_SECONDS)}
    event = converter(normalized, config)
    if event is None:
        return None, "conversion_missing"
    event.set("uid", "HISTORICAL-REPLAY." + event.attrib["uid"])
    event.set("time", iso(captured))
    event.set("start", iso(captured))
    event.set("stale", iso(captured + timedelta(seconds=STALE_SECONDS)))
    detail = event.find("detail")
    contact = detail.find("contact")
    if contact is not None:
        contact.set("callsign", "HISTORICAL-REPLAY " + contact.get("callsign", kind))
    for node in detail.findall("remarks"):
        detail.remove(node)
    ET.SubElement(detail, "remarks").text = (
        f"HISTORICAL REPLAY — PCAP observation time {iso(captured)}; "
        "not a live report. Capture clock and reported position/altitude are unverified."
    )
    ET.SubElement(detail, "aetherlink_scout_replay", {
        "capture_time": iso(captured), "kind": kind,
        "altitude": "unknown" if altitude_unknown else "reported_geodetic_unverified",
        "transmitted": "false",
    })
    for node in detail.iter():
        if node.tag == "_flow-tags_":
            for key in node.attrib:
                node.set(key, iso(captured))
        if "lastUpdateTime" in node.attrib:
            node.set("lastUpdateTime", str(int(captured.timestamp() * 1000)))
        if "timestamp" in node.attrib:
            node.set("timestamp", str(int(captured.timestamp() * 1000)))
        if node.tag in {"link", "creator"}:
            node.set("uid", "HISTORICAL-REPLAY." + node.get("uid", ""))
    # Verify serialized XML and numerical/time preservation, not just construction.
    event = ET.fromstring(ET.tostring(event))
    point = event.find("point")
    # PyTAK 7.6.1 truncates coordinates to four decimal places by default.
    assert abs(float(point.get("lat")) - float(data[lat_key])) <= 0.0001000001
    assert abs(float(point.get("lon")) - float(data[lon_key])) <= 0.0001000001
    assert float(point.get("hae")) == float(normalized[alt_key])
    assert all(math.isfinite(float(point.get(key))) for key in ("hae", "ce", "le"))
    assert event.get("time") == event.get("start") == iso(captured)
    assert event.get("stale") == iso(captured + timedelta(seconds=STALE_SECONDS))
    return event, "altitude_unknown" if altitude_unknown else "altitude_reported"


def export(capture, output):
    counts = {"packets": 0, "decoded_reports": 0, "decode_errors": 0,
              "rejected_payloads": 0, "validation_errors": 0}
    ids = {"aircraft": set(), "operator": set()}
    states = {kind: {"events": 0, "position_review": 0, "conversion_missing": 0,
                     "altitude_unknown": 0, "altitude_reported": 0} for kind in ids}
    now = datetime.now(timezone.utc)
    first = last = None
    current_or_future = 0
    root = ET.Element("aetherlink_scout_historical_archive", {"transmitted": "false", "stale_seconds": str(STALE_SECONDS)})
    with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()), PcapReader(str(capture)) as reader:
        for packet in reader:
            counts["packets"] += 1
            try:
                extracted = extract_odid_from_scapy_packet(packet)
                if not extracted:
                    continue
                pack, meta = extracted
                captured = datetime.fromtimestamp(float(packet.time), timezone.utc)
                meta["timestamp"] = int(captured.timestamp() * 1000)
                data = bytes_to_rid_dict(pack, meta)
                if not data:
                    counts["rejected_payloads"] += 1
                    continue
            except Exception:
                counts["decode_errors"] += 1
                continue
            counts["decoded_reports"] += 1
            first = captured if first is None else min(first, captured)
            last = captured if last is None else max(last, captured)
            current_or_future += captured + timedelta(seconds=STALE_SECONDS) >= now
            events = []
            try:
                for kind in ids:
                    event, state = historical_event(data, kind, captured)
                    states[kind][state] += 1
                    if event is not None:
                        ids[kind].add(event.get("uid"))
                        states[kind]["events"] += 1
                        events.append(event)
                if len(events) == 2:
                    assert events[0].get("uid") != events[1].get("uid")
                root.extend(events)
            except Exception:
                counts["validation_errors"] += 1
    overlap = len(ids["aircraft"] & ids["operator"])
    result = {"counts": counts, "events": states,
              "unique_track_ids": {k: len(v) for k, v in ids.items()},
              "cross_kind_uid_collisions": overlap,
              "capture_first_utc": iso(first) if first else None,
              "capture_last_utc": iso(last) if last else None,
              "reports_not_expired_at_validation": current_or_future,
              "stale_seconds": STALE_SECONDS,
              "scope": "Local historical archive; capture-clock timestamps, no aggregation, no transmission. IDs are not verified aircraft/person identities."}
    ET.ElementTree(root).write(output / "historical-cot.xml", encoding="utf-8", xml_declaration=True)
    (output / "summary.json").write_text(json.dumps(result, indent=2) + "\n")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("capture", type=Path)
    args = parser.parse_args()
    if not args.capture.is_file() or args.capture.suffix.lower() not in {".pcap", ".pcapng", ".cap"}:
        parser.error("provide an existing authorized PCAP/PCAPNG file")
    output = ROOT / "local-data" / "cot-validation" / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    output.mkdir(parents=True, exist_ok=False)
    try:
        result = export(args.capture, output)
    except Exception:
        parser.exit(2, "Capture/export failed; no payload contents printed.\n")
    print(json.dumps(result, indent=2))
    print("Local artifacts:", output.relative_to(ROOT))
    return 0 if result["counts"]["decoded_reports"] and not result["counts"]["validation_errors"] and not result["cross_kind_uid_collisions"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
