import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient
from scapy.layers.dot11 import RadioTap, Dot11, Dot11Beacon, Dot11Elt
from scapy.utils import wrpcap

from aetherlink_scout.app import create_app
from aetherlink_scout.replay import demo_dataset, load_capture, number, report


class DashboardTests(unittest.TestCase):
    def test_preserves_precision_and_flags_unknown(self):
        data = {"BasicID": "TEST", "Latitude": 35.1234567, "Longitude": -115.7654321,
                "AltitudeGeo": -1000, "SpeedHorizontal": 255, "Direction": 361, "data": {}}
        item = report(data, 1)
        self.assertEqual(item["aircraft"]["lat"], data["Latitude"])
        self.assertIsNone(item["aircraft"]["altitude"])
        self.assertIsNone(item["speed"])
        self.assertIsNone(item["heading"])
        self.assertIsNone(item["operator"])
        self.assertIsNone(number(float("nan")))

    def test_stable_key_before_serial_and_zero_pair(self):
        base = {"Latitude": 0, "Longitude": 0, "data": {"MAC address": "02:00:00:00:00:01"}}
        before = report(base, 1)
        after = report({**base, "BasicID": "TEST"}, 2)
        self.assertEqual(before["key"], after["key"])
        self.assertIsNone(before["aircraft"])

    def test_actual_radiotap_decode(self):
        basic = bytes([2, 18]) + b"TEST-ID".ljust(20, b"\0") + bytes(3)
        frame = (RadioTap()/Dot11(type=0, subtype=8, addr1="ff:ff:ff:ff:ff:ff",
                                 addr2="02:00:00:00:00:01", addr3="02:00:00:00:00:01")/
                 Dot11Beacon()/Dot11Elt(ID=221, info=bytes.fromhex("fa0bbc0d00f21901") + basic))
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/"test.pcap"
            wrpcap(str(path), [frame])
            replay = load_capture(path)
        self.assertEqual(replay["counts"]["decoded"], 1)
        self.assertEqual(replay["records"][0]["serial"], "TEST-ID")
        self.assertIsNone(replay["records"][0]["aircraft"])

    def test_loopback_api_and_no_live_controls(self):
        client = TestClient(create_app(demo_dataset()), base_url="http://127.0.0.2")
        self.assertEqual(client.get("/").status_code, 200)
        response = client.get("/api/replay")
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()["tak_enabled"])
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertEqual(client.post("/api/replay").status_code, 405)
        self.assertEqual(client.get("/api/replay", headers={"Host": "outside.example"}).status_code, 400)
        self.assertEqual(client.get("/api/capture").status_code, 404)
        self.assertEqual(client.get("/static/../requirements.txt").status_code, 404)


if __name__ == "__main__":
    unittest.main()
