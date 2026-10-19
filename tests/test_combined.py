"""Shared detector tracks/lifecycle with real unprivileged synthetic producers."""
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from scapy.layers.dot11 import RadioTap,Dot11,Dot11Beacon,Dot11Elt
from scapy.utils import wrpcap
from aetherlink_scout.ble import BleReceiver,BleCapture
from aetherlink_scout.capture import LiveCapture,CaptureError
from aetherlink_scout.combined import CombinedReceiver,Source
from aetherlink_scout.receiver import Receiver
from aetherlink_scout.replay import demo_dataset
from test_ble import advertisement,basic,location,system
from test_receiver import wait_for


def wifi_bytes(frequency=2437,address='02:00:00:00:00:01'):
    frames=[]
    for message in (basic(),location(),system()):
        frames.append(RadioTap(present='Channel',ChannelFrequency=frequency,ChannelFlags=0x0080)/
                      Dot11(type=0,subtype=8,addr1='ff:ff:ff:ff:ff:ff',addr2=address,addr3=address)/
                      Dot11Beacon()/Dot11Elt(ID=221,info=bytes.fromhex('fa0bbc0d00f21901')+message))
    with tempfile.TemporaryDirectory() as temp:
        p=Path(temp)/'synthetic.pcap';wrpcap(str(p),frames);return p.read_bytes()


def synthetic_sources(*,renew=False,fail=None,pending=None):
    captures=[];sources=[]
    for name,frequency,address in (('wifi6',2437,'02:00:00:00:00:01'),('wifi149',5745,'02:00:00:00:00:02'),('ble',None,'02:00:00:00:00:03')):
        if frequency:
            raw=wifi_bytes(frequency,address)
        else:
            events=[{'kind':'ready'}]+[{**advertisement(message), 'address':address} for message in (basic(),location(),system())]+[{'kind':'status','service_data_updates':10,'rid_candidates':3}]
            raw=(''.join(json.dumps(event)+'\n' for event in events)).encode()
        def factory(interface, name=name, raw=raw, frequency=frequency):
            class Capture(LiveCapture if frequency else BleCapture):
                def __init__(self):
                    super().__init__(interface)
                    self.source_id=name
                    self.index=sum(c.source_id==name for c in captures)
                    captures.append(self)
                def start(self):
                    if pending and name=='wifi149' and self.index:
                        pending[0].set();pending[1].wait(3)
                    if fail==name:
                        raise CaptureError('Synthetic source unavailable')
                    code=124 if renew and self.index<1 else 0
                    pause=.2 if code==124 else 60
                    self.log=io.BytesIO()
                    self.process=subprocess.Popen([sys.executable,'-c',f'import os,time;os.write(1,{raw!r});time.sleep({pause});raise SystemExit({code})'],stdout=subprocess.PIPE,stderr=subprocess.DEVNULL)
                    return self.process.stdout.fileno()
            return Capture()
        child=Receiver(live_interface='test-'+name,capture_factory=factory,channel=6 if frequency==2437 else 149,frequency=frequency) if frequency else BleReceiver(capture_factory=factory)
        sources.append(Source(name,name,child))
    return sources,captures


class CombinedTests(unittest.TestCase):
    def setUp(self):self.receiver=None
    def tearDown(self):
        if self.receiver:self.receiver.close()

    def manual(self):
        self.receiver=CombinedReceiver(sources=[Source('wifi6','Panda',Receiver()),Source('wifi149','Techkey',Receiver()),Source('ble','BLE',Receiver())])
        self.receiver.accepting=True
        return self.receiver

    def test_three_sources_share_track_history_and_session_through_renewal(self):
        sources,captures=synthetic_sources(renew=True)
        self.receiver=CombinedReceiver(sources=sources)
        first=self.receiver.start_detection()
        wait_for(lambda:self.receiver.status()['counts']['decoded']==18)
        value=self.receiver.snapshot()
        self.assertEqual(len(value['tracks']),1)
        self.assertEqual(value['tracks'][0]['fields']['sources'],['ble','wifi149','wifi6'])
        self.assertEqual(value['status']['session_id'],first['session_id'])
        self.assertEqual(value['status']['capture_renewals'],3)
        self.assertEqual(value['status']['counts']['errors'],0)
        self.assertEqual([s['received_frequencies_mhz'] for s in value['status']['sources'][:2]],[[2437],[5745]])
        self.receiver.stop_detection()
        self.assertTrue(all(c.process is None or c.process.poll() is not None for c in captures))
        self.assertFalse(self.receiver.status()['detection_active'])
        self.assertEqual(self.receiver.history.list()['total'],1)
        frozen=self.receiver.status()['counts'];time.sleep(.1)
        self.assertEqual(self.receiver.status()['counts'],frozen)

    def test_startup_failure_stops_already_started_peer_and_never_starts_later_source(self):
        sources,captures=synthetic_sources(fail='wifi149')
        self.receiver=CombinedReceiver(sources=sources)
        self.receiver.start_detection()
        wait_for(lambda:not self.receiver.thread.is_alive())
        self.assertEqual(self.receiver.state,'error')
        self.assertNotIn('ble',[c.source_id for c in captures])
        self.assertTrue(all(c.process is None or c.process.poll() is not None for c in captures))
        self.assertFalse(self.receiver.status()['detection_active'])

    def test_stop_during_renewal_start_confirms_new_process_exit(self):
        entered,release=threading.Event(),threading.Event()
        sources,captures=synthetic_sources(renew=True,pending=(entered,release))
        self.receiver=CombinedReceiver(sources=sources)
        self.receiver.start_detection()
        self.assertTrue(entered.wait(3))
        stopper=threading.Thread(target=self.receiver.stop_detection)
        stopper.start();time.sleep(.03);self.assertTrue(stopper.is_alive())
        release.set();stopper.join(5)
        self.assertFalse(stopper.is_alive())
        self.assertTrue(all(c.process is None or c.process.poll() is not None for c in captures))
        self.assertFalse(self.receiver.status()['detection_active'])

    def test_source_runtime_error_stops_entire_group_and_retains_track(self):
        sources,captures=synthetic_sources()
        self.receiver=CombinedReceiver(sources=sources)
        self.receiver.start_detection()
        wait_for(lambda:self.receiver.status()['counts']['decoded']==9)
        captures[1].process.terminate()
        wait_for(lambda:not self.receiver.thread.is_alive())
        self.assertEqual(self.receiver.state,'error')
        self.assertEqual(len(self.receiver.tracks),1)
        self.assertTrue(all(c.process.poll() is not None for c in captures))

    def test_late_identity_merges_preserve_fresh_fields_and_one_history_snapshot(self):
        r=self.manual();base=demo_dataset()['records'][0]
        # Two initially unidentified transmitters on independent transports.
        r.ingest('wifi6',{**base,'key':'a','serial':None})
        r.ingest('ble',{**base,'key':'b','serial':None,'aircraft':None,'operator':{'lat':35.0081,'lon':-115.0121,'altitude':None}})
        self.assertEqual(len(r.tracks),2)
        r.ingest('wifi6',{**base,'key':'a','aircraft':None,'operator':None})
        r.ingest('ble',{**base,'key':'b','aircraft':None,'operator':None})
        self.assertEqual(len(r.tracks),1)
        track=next(iter(r.tracks.values()))
        self.assertEqual(track['fields']['operator']['lat'],35.0081)
        self.assertEqual(track['fields']['sources'],['ble','wifi6'])
        self.assertEqual(r.history.list()['total'],1)
        controls=[e for e in r.events if 'merged_track' in e]
        self.assertEqual(len(controls),1)
        self.assertEqual(len(r.merge_aliases),1)
        r.clear_map(only_stale=False)
        r.ingest('ble',{**base,'key':'b'})
        self.assertEqual(next(iter(r.tracks)),track['key'])

    def test_conflicting_fresh_positions_and_changed_claim_remain_reviewable(self):
        r=self.manual();base=demo_dataset()['records'][0]
        r.ingest('wifi6',{**base,'key':'a'})
        far={**base,'key':'b','aircraft':{'lat':40.,'lon':-110.,'altitude':120}}
        r.ingest('ble',far)
        self.assertEqual(len(r.tracks),2)
        self.assertTrue(all(t['fields'].get('identity_conflict') for t in r.tracks.values()))
        r.ingest('ble',{**far,'serial':'AetherLinkScoutOther'})
        self.assertEqual(len(r.tracks),3)

    def test_fixed_149_guard_rejects_unplanned_channel_before_privileged_capture(self):
        from aetherlink_scout.capture import LiveCapture
        info=subprocess.CompletedProcess([],0,stdout=' type monitor\n channel 6 (2437 MHz)\n')
        with patch('aetherlink_scout.capture.subprocess.run',return_value=info),patch('aetherlink_scout.capture.subprocess.Popen') as popen:
            with self.assertRaisesRegex(CaptureError,'149'):
                LiveCapture('test149',channel=149,frequency=5745).start()
            popen.assert_not_called()

    def test_unconfirmed_stop_keeps_supervisor_alive_until_cleanup_succeeds(self):
        sources,captures=synthetic_sources()
        original=sources[1].receiver.stop_detection
        entered,release=threading.Event(),threading.Event()
        def delayed_confirmation():
            if not release.is_set():
                entered.set()
                raise CaptureError('Synthetic stop not confirmed')
            return original()
        sources[1].receiver.stop_detection=delayed_confirmation
        self.receiver=CombinedReceiver(sources=sources)
        self.receiver.start_detection()
        wait_for(lambda:self.receiver.status()['counts']['decoded']==9)
        stopper=threading.Thread(target=self.receiver.stop_detection)
        stopper.start()
        try:
            self.assertTrue(entered.wait(2))
            wait_for(lambda:self.receiver.stop_unconfirmed)
            self.assertTrue(self.receiver.thread.is_alive())
            self.assertFalse(self.receiver.thread.daemon)
            self.assertTrue(stopper.is_alive())
        finally:
            release.set();stopper.join(5)
        self.assertFalse(self.receiver.status()['detection_active'])

    def test_different_id_types_do_not_merge_and_late_position_conflict_is_preserved(self):
        r=self.manual();base=demo_dataset()['records'][0]
        r.ingest('wifi6',{**base,'key':'a','id_type':1})
        r.ingest('ble',{**base,'key':'b','id_type':2})
        self.assertEqual(len(r.tracks),2)
        r=self.manual()
        r.ingest('wifi6',{**base,'key':'a','serial':None})
        r.ingest('ble',{**base,'key':'b','serial':None,'aircraft':{'lat':40.,'lon':-110.,'altitude':120}})
        r.ingest('wifi6',{**base,'key':'a','aircraft':None,'operator':None})
        r.ingest('ble',{**base,'key':'b','aircraft':None,'operator':None})
        self.assertEqual(len(r.tracks),2)
        self.assertTrue(all(t['fields'].get('identity_conflict') for t in r.tracks.values()))


if __name__=='__main__':unittest.main()
