"""Offline Wi-Fi ODID decode summary. No capture workers or network output."""
import argparse
from contextlib import redirect_stderr, redirect_stdout
import io
import json
import math
from pathlib import Path

from scapy.utils import PcapReader
# Register radiotap/802.11 link types before PcapReader opens a real capture.
from scapy.layers.dot11 import Dot11, RadioTap  # noqa: F401
from dronecot.rid_normalize import bytes_to_rid_dict
from dronecot.wifi_parse import extract_odid_from_scapy_packet


def position_state(data, lat_key, lon_key):
    if data.get(lat_key) is None or data.get(lon_key) is None:
        return "missing"
    try:
        lat, lon = float(data[lat_key]), float(data[lon_key])
    except (TypeError, ValueError):
        return "invalid"
    if not (math.isfinite(lat) and math.isfinite(lon) and -90 <= lat <= 90 and -180 <= lon <= 180):
        return "invalid"
    return "zero_pair_review" if lat == lon == 0 else "in_range"


def summarize(path):
    counts = {"packets": 0, "odid_payloads": 0, "decoded_reports": 0,
              "rejected_payloads": 0, "decode_errors": 0,
              "reports_with_basic_id": 0}
    positions = {name: {state: 0 for state in ("missing", "invalid", "zero_pair_review", "in_range")}
                 for name in ("aircraft", "operator")}
    # Suppress upstream diagnostics so payload contents never appear by accident.
    with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()), PcapReader(str(path)) as reader:
        for packet in reader:
            counts["packets"] += 1
            try:
                result = extract_odid_from_scapy_packet(packet)
                if not result:
                    continue
                counts["odid_payloads"] += 1
                pack, meta = result
                data = bytes_to_rid_dict(pack, meta)
                if not data:
                    counts["rejected_payloads"] += 1
                    continue
                counts["decoded_reports"] += 1
                counts["reports_with_basic_id"] += bool(data.get("BasicID"))
                for name, lat, lon in [("aircraft", "Latitude", "Longitude"),
                                       ("operator", "OperatorLatitude", "OperatorLongitude")]:
                    positions[name][position_state(data, lat, lon)] += 1
            except Exception:
                counts["decode_errors"] += 1
    return {"counts": counts, "positions": positions,
            "scope": "Per-packet offline decode; no track aggregation, CoT transmission or authenticity verification. In-range coordinates are not validated locations."}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("capture", type=Path, help="Explicit authorized PCAP/PCAPNG file")
    args = parser.parse_args()
    if not args.capture.is_file():
        parser.error("capture must be an existing file")
    if args.capture.suffix.lower() not in {".pcap", ".pcapng", ".cap"}:
        parser.error("expected .pcap, .pcapng or .cap")
    try:
        result = summarize(args.capture)
    except Exception:
        parser.exit(2, "Could not read capture; check format and read permissions. No payload details printed.\n")
    print(json.dumps(result, indent=2))
    return 0 if result["counts"]["decoded_reports"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
