"""Network-free synthetic conversion check; no capture or TAK transmission."""
import xml.etree.ElementTree as ET

from dronecot.functions import rid_op_to_cot_xml, rid_uas_to_cot_xml
from dronecot.wifi_capture import parse_wifi_feed_url


def main():
    data = {
        "BasicID": "AETHERLINK-SCOUT-SYNTHETIC-TEST",
        "Latitude": 35.0,
        "Longitude": -115.0,
        "OperatorLatitude": 35.001,
        "OperatorLongitude": -115.001,
        "AltitudeGeo": 100.0,
        "data": {},
    }
    config = {"COT_HOST_ID": "AETHERLINK-SCOUT-LOCAL-TEST"}
    aircraft = rid_uas_to_cot_xml(data, config)
    operator = rid_op_to_cot_xml(data, config)
    assert aircraft is not None and operator is not None
    for event, lat, lon in [(aircraft, 35.0, -115.0), (operator, 35.001, -115.001)]:
        parsed = ET.fromstring(ET.tostring(event))
        assert parsed.tag == "event"
        point = parsed.find("point")
        assert point is not None
        assert float(point.attrib["lat"]) == lat
        assert float(point.attrib["lon"]) == lon
    assert aircraft.attrib["uid"] != operator.attrib["uid"]
    assert rid_uas_to_cot_xml({"BasicID": "NO-POSITION"}, config) is None
    assert rid_op_to_cot_xml({"BasicID": "NO-POSITION"}, config) is None
    assert parse_wifi_feed_url("wifi+pcap:///tmp/test.pcapng")["pcap_path"] == "/tmp/test.pcapng"
    print("PASS: synthetic aircraft/operator XML, separate IDs, missing-position handling, PCAP URL parsing")
    print("No radio capture, sockets, or TAK transmission. RF decoding and client rendering remain untested.")


if __name__ == "__main__":
    main()
