"""Real ODID wire decoding plus synthetic lifecycle and BlueZ session tests."""
import importlib.util
import io
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import threading
import time
import unittest
from unittest.mock import Mock, patch

from aetherlink_scout.ble import BleReceiver, BleCapture, decode_advertisement, MAX_LINE
from aetherlink_scout.capture import CaptureError
from test_receiver import wait_for

spec=importlib.util.spec_from_file_location('scout_bluez',Path(__file__).resolve().parent.parent/'scripts/ble_bluez.py')
bluez=importlib.util.module_from_spec(spec);spec.loader.exec_module(bluez)
ADDRESS='02:00:00:00:00:01'


def advertisement(message, counter=0):
    return {'kind':'advertisement','timestamp':123.5,'address':ADDRESS,'rssi':-50,'service_data':(bytes([0x0d,counter])+message).hex()}


def basic():
    return bytes([2,18])+b'AetherLinkScoutBLE'.ljust(20,b'\0')+bytes(3)


def location():
    payload=bytearray(25);payload[0]=0x12
    struct.pack_into('<ii',payload,5,350123456,-1150123456)
    struct.pack_into('<HHH',payload,13,2240,2240,2020)
    return bytes(payload)


def system():
    payload=bytearray(25);payload[0]=0x42
    struct.pack_into('<ii',payload,2,350087654,-1150098765)
    return bytes(payload)


class BleTests(unittest.TestCase):
    def test_real_basic_location_system_messages_and_stable_track(self):
        first=decode_advertisement(advertisement(basic()))
        aircraft=decode_advertisement(advertisement(location(),1))
        operator=decode_advertisement(advertisement(system(),2))
        self.assertEqual(first['serial'],'AetherLinkScoutBLE')
        self.assertIsNone(first['aircraft']);self.assertIsNone(first['operator'])
        self.assertEqual(first['key'],aircraft['key']);self.assertEqual(first['key'],operator['key'])
        self.assertAlmostEqual(aircraft['aircraft']['lat'],35.0123456)
        self.assertAlmostEqual(operator['operator']['lon'],-115.0098765)
        self.assertEqual(first['radio'],'BLE legacy (BlueZ D-Bus)')
        self.assertIsNone(first['channel'])
        self.assertEqual(first['timestamp'],123.5)

    def test_invalid_or_unrelated_input_never_creates_drone(self):
        self.assertIsNone(decode_advertisement({**advertisement(basic()),'service_data':'0100'+basic().hex()}))
        self.assertIsNone(decode_advertisement({**advertisement(basic()),'service_data':'0d00'}))
        for change in ({'timestamp':float('nan')},{'timestamp':True},{'address':'invalid'},{'service_data':'x'*9000},{'service_data':'zz'}):
            with self.subTest(change=next(iter(change))):
                with self.assertRaises((ValueError,TypeError)):
                    decode_advertisement({**advertisement(basic()),**change})

    def test_stream_decodes_partial_fields_and_stop_keeps_history(self):
        read_fd,write_fd=os.pipe()
        class Source:
            def __init__(self,interface):pass
            def start(self):return read_fd
            def stop(self):pass
            def outcome(self):return 0
        receiver=BleReceiver(capture_factory=Source)
        try:
            receiver.start_detection()
            events=[{'kind':'ready'},advertisement(basic()),advertisement(location(),1),advertisement(system(),2),{'kind':'status','service_data_updates':9,'rid_candidates':3}]
            os.write(write_fd,(''.join(json.dumps(e)+'\n' for e in events)).encode())
            wait_for(lambda:receiver.counts['decoded']==3 and receiver.status()['ble_service_updates']==9)
            track=receiver.snapshot()['tracks'][0]
            self.assertIsNotNone(track['fields']['operator'])
            self.assertIsNotNone(track['fields']['aircraft'])
            self.assertEqual(receiver.status()['expected_channel'],None)
            self.assertTrue(receiver.status()['ble_enabled'])
            receiver.stop_detection()
            self.assertFalse(receiver.status()['detection_active'])
            self.assertEqual(len(receiver.tracks),1)
            self.assertEqual(receiver.history.list()['total'],1)
        finally:
            receiver.close();os.close(read_fd);os.close(write_fd)

    def test_errors_are_sanitized_and_oversized_stream_stops(self):
        for data in (json.dumps({'kind':'error','message':'PRIVATE-PAYLOAD'}).encode()+b'\n',b'x'*(MAX_LINE+1)):
            with self.subTest(length=len(data)):
                read_fd,write_fd=os.pipe()
                class Source:
                    def __init__(self,interface):pass
                    def start(self):return read_fd
                    def stop(self):pass
                    def outcome(self):return 0
                receiver=BleReceiver(capture_factory=Source)
                writer=threading.Thread(target=lambda:os.write(write_fd,data),daemon=True)
                try:
                    receiver.start_detection();writer.start()
                    wait_for(lambda:receiver.state=='error')
                    self.assertNotIn('PRIVATE-PAYLOAD',receiver.message)
                    self.assertEqual(receiver.counts['decoded'],0)
                finally:
                    receiver.close();os.close(read_fd);writer.join(2);os.close(write_fd)

    def test_confirmed_renewal_preserves_ble_counts_tracks_and_session(self):
        captures=[]
        class Segment(BleCapture):
            def __init__(self,interface):
                super().__init__(interface);self.index=len(captures);captures.append(self)
            def start(self):
                events=[{'kind':'ready'},advertisement(basic()),{'kind':'status','service_data_updates':7,'rid_candidates':1}]
                raw=(''.join(json.dumps(e)+'\n' for e in events)).encode()
                code=124 if self.index<2 else 0;pause=.1 if self.index<2 else 60
                self.log=io.BytesIO()
                self.process=subprocess.Popen([sys.executable,'-c',f'import os,time;os.write(1,{raw!r});time.sleep({pause});raise SystemExit({code})'],stdout=subprocess.PIPE,stderr=subprocess.DEVNULL)
                return self.process.stdout.fileno()
        receiver=BleReceiver(capture_factory=Segment)
        try:
            first=receiver.start_detection()
            wait_for(lambda:receiver.counts['decoded']==3 and receiver.status()['ble_service_updates']==21)
            self.assertEqual(receiver.status()['session_id'],first['session_id'])
            self.assertEqual(receiver.capture_segments,3)
            self.assertEqual(receiver.status()['ble_rid_candidates'],3)
            self.assertEqual(len(receiver.tracks),1)
            receiver.stop_detection()
            self.assertTrue(all(c.process.poll() is not None for c in captures))
        finally:receiver.close()

    def test_bluez_reader_ignores_cached_rssi_and_other_services_and_releases_own_session(self):
        props=Mock();props.Get.return_value=True
        adapter=Mock()
        def interface(obj,name):
            return props if name=='org.freedesktop.DBus.Properties' else adapter
        dbus=Mock();dbus.Interface.side_effect=interface;dbus.Dictionary.side_effect=lambda data,**_:data;dbus.Boolean.side_effect=bool
        bus=Mock();loop=Mock();reader=bluez.Discovery(bus,dbus,loop,'hci0')
        values=[]
        with patch.object(bluez,'emit',side_effect=values.append):
            reader.ingest('/org/bluez/hci0/dev_test',{'ServiceData':{bluez.UUID:bytes([13,0])+basic()},'Address':ADDRESS})
            reader.start()
            reader.changed('org.bluez.Device1',{'RSSI':-40},[],path='/org/bluez/hci0/dev_test')
            reader.ingest('/org/bluez/hci1/dev_test',{'ServiceData':{bluez.UUID:bytes([13,0])+basic()},'Address':ADDRESS})
            reader.ingest('/org/bluez/hci0/dev_test',{'ServiceData':{'unrelated':b'private-name'},'Address':ADDRESS})
            reader.ingest('/org/bluez/hci0/dev_test',{'ServiceData':{bluez.UUID:bytes([13,0])+basic()},'Address':ADDRESS})
            reader.close()
        advertisements=[v for v in values if v['kind']=='advertisement']
        self.assertEqual(len(advertisements),1)
        self.assertNotIn('private-name',json.dumps(values))
        self.assertEqual(adapter.SetDiscoveryFilter.call_args.args[0],{'Transport':'le','DuplicateData':True})
        adapter.StartDiscovery.assert_called_once();adapter.StopDiscovery.assert_called_once()
        self.assertFalse(any(call[0].endswith('.Set') for call in props.mock_calls))

    def test_power_off_and_discovery_loss_fail_without_power_mutation(self):
        props=Mock();props.Get.return_value=False
        dbus=Mock();dbus.Interface.return_value=props
        reader=bluez.Discovery(Mock(),dbus,Mock(),'hci0')
        with patch.object(bluez,'emit'):
            with self.assertRaises(RuntimeError):reader.start()
            reader.close()
            self.assertFalse(reader.session)
            self.assertFalse(reader.heartbeat())
            self.assertEqual(reader.code,1)
        props.Set.assert_not_called()

    def test_helper_expiry_is_renewable_only_after_cleanup_succeeds(self):
        reader=bluez.Discovery(Mock(),Mock(),Mock(),'hci0')
        reader.adapter=Mock();reader.session=reader.running=True
        with patch.object(bluez,'emit'):
            reader.expire();reader.close()
            self.assertEqual(reader.code,124)
            reader.adapter.StopDiscovery.assert_called_once()
            reader.adapter.StopDiscovery.side_effect=RuntimeError('synthetic failure')
            reader.session=True
            reader.close()
            self.assertEqual(reader.code,1)
        reader.loop.quit.assert_called_once()

    def test_cli_rejects_invalid_adapter_and_combined_modes_before_scanning(self):
        root=Path(__file__).resolve().parent.parent
        for args in (['--live-bluetooth','invalid'],['--demo','--live-bluetooth','hci0'],['--live-bluetooth','hci0','--scan']):
            with self.subTest(args=args):
                result=subprocess.run([sys.executable,'-m','aetherlink_scout',*args],cwd=root,capture_output=True,text=True,timeout=10)
                self.assertEqual(result.returncode,2)


if __name__=='__main__':unittest.main()
