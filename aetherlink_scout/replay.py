"""Read-only PCAP decoding for the local dashboard. No CoT/network workers."""
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timezone
import hashlib
import io
import math
from pathlib import Path

from scapy.layers.dot11 import Dot11, RadioTap  # noqa: F401
from scapy.utils import PcapReader
from dronecot.rid_normalize import bytes_to_rid_dict
from dronecot.wifi_parse import extract_odid_from_scapy_packet

MAX_REPORTS = 100_000


def number(value, low=None, high=None, unknown=()):
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(value) or value in unknown:
        return None
    if low is not None and value < low or high is not None and value > high:
        return None
    return value


def text(value):
    return str(value).strip()[:200] if value is not None else None


def position(data, operator=False):
    prefix = "Operator" if operator else ""
    lat = number(data.get(prefix + "Latitude"), -90, 90)
    lon = number(data.get(prefix + "Longitude"), -180, 180)
    if lat is None or lon is None or lat == lon == 0:
        return None
    return {"lat": lat, "lon": lon,
            "altitude": number(data.get(prefix + "AltitudeGeo"), unknown=(-1000, 9999999))}


def report(data, timestamp):
    meta = data.get("data") or {}
    serial = text(data.get("BasicID"))
    mac = text(meta.get("MAC address"))
    identity = "mac:" + mac.lower() if mac else "id:" + serial if serial else None
    if identity is None:
        return None
    return {
        "timestamp": timestamp,
        "key": hashlib.sha256(identity.encode()).hexdigest()[:24],
        "serial": serial, "mac": mac,
        "operator_id": text(data.get("OperatorID")),
        "aircraft": position(data), "operator": position(data, True),
        "speed": number(data.get("SpeedHorizontal"), 0, 254.25),
        "heading": number(data.get("Direction"), 0, 359.999999),
        "height": number(data.get("Height"), unknown=(-1000, 9999999)),
        "height_type": text(data.get("HeightType")),
        "operator_location_type": text(data.get("OperatorLocationType")),
        "horizontal_accuracy_code": number(data.get("HorizAccuracy"), 0, 15),
        "vertical_accuracy_code": number(data.get("VertAccuracy"), 0, 15),
        "rssi": number(meta.get("RSSI"), -150, 0),
        "channel": number(meta.get("channel"), 1, 233),
        "radio": text(meta.get("type")) or "Wi-Fi Remote ID",
    }


def dataset(records, counts, source):
    if not records:
        raise ValueError("No usable Remote ID reports decoded from this capture.")
    records.sort(key=lambda record: record["timestamp"])
    start = records[0]["timestamp"]
    for record in records:
        record["t"] = record["timestamp"] - start
    return {"mode": "historical_replay", "source": source,
            "start_utc": datetime.fromtimestamp(start, timezone.utc).isoformat(),
            "start_timestamp": start, "duration": records[-1]["t"],
            "stale_seconds": 30, "tail_seconds": 35, "tak_enabled": False,
            "counts": counts, "records": records}


def load_capture(path):
    path = Path(path)
    if not path.is_file() or path.suffix.lower() not in {".pcap", ".pcapng", ".cap"}:
        raise ValueError("Provide an existing authorized .pcap, .pcapng or .cap file.")
    records = []
    counts = {"packets": 0, "decoded": 0, "rejected": 0, "errors": 0, "unidentified": 0}
    with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()), PcapReader(str(path)) as reader:
        for packet in reader:
            counts["packets"] += 1
            try:
                extracted = extract_odid_from_scapy_packet(packet)
                if not extracted:
                    continue
                data = bytes_to_rid_dict(*extracted)
                if not data:
                    counts["rejected"] += 1
                    continue
                counts["decoded"] += 1
                timestamp = number(packet.time)
                if timestamp is None:
                    counts["errors"] += 1
                    continue
                item = report(data, timestamp)
                if item is None:
                    counts["unidentified"] += 1
                    continue
                records.append(item)
            except Exception:
                counts["errors"] += 1
                continue
            if len(records) > MAX_REPORTS:
                raise ValueError("Capture exceeds the 100,000-report replay limit; use a shorter capture.")
    return dataset(records, counts, "Authorized local PCAP")


def demo_dataset():
    """Two fictitious transmitters; the first goes stale during playback."""
    records = []
    start = datetime(2020, 1, 1, tzinfo=timezone.utc).timestamp()
    for t in range(46):
        for i in range(2):
            if i == 0 and t > 12 or i == 1 and t < 5:
                continue
            data = {"BasicID": f"SYNTHETIC-DEMO-{i + 1}",
                    "Latitude": 35.01 + i * 0.002 + t * 0.000025,
                    "Longitude": -115.01 + i * 0.003 + t * 0.00002,
                    "OperatorLatitude": 35.008 + i * 0.002,
                    "OperatorLongitude": -115.012 + i * 0.003,
                    "AltitudeGeo": 120 + i * 10, "SpeedHorizontal": 4.5,
                    "Direction": 35, "OperatorLocationType": 1,
                    "data": {"MAC address": f"02:00:00:00:00:0{i + 1}", "RSSI": -45-i*10,
                             "channel": 6, "type": "Synthetic demo"}}
            records.append(report(data, start + t))
    result = dataset(records, {"packets": len(records), "decoded": len(records),
                              "rejected": 0, "errors": 0, "unidentified": 0}, "Synthetic demo — no real aircraft")
    result["mode"] = "synthetic_replay"
    return result
