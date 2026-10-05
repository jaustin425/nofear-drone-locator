"""Continuous input cancellation, partial frames, bounded state and control guards."""
import io
import os
import struct
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from pathlib import Path

from fastapi.testclient import TestClient
from scapy.layers.dot11 import RadioTap, Dot11, Dot11Beacon, Dot11Elt
from scapy.utils import wrpcap
from aetherlink_scout.app import create_app
from aetherlink_scout.capture import CaptureError, LiveCapture
from aetherlink_scout.receiver import Receiver, MAX_EVENTS, MAX_TRACKS, event
from aetherlink_scout.replay import demo_dataset


def wait_for(predicate):
    until=time.monotonic()+3
    while not predicate():
        if time.monotonic()>until:
            raise AssertionError('Receiver condition timed out')
        time.sleep(.01)


def frame_bytes(frequency=None):
    basic=bytes([2,18])+b'SYNTHETIC-PIPE'.ljust(20,b'\0')+bytes(3)
    radio=RadioTap() if frequency is None else RadioTap(present='Channel',ChannelFrequency=frequency,ChannelFlags=0x0080)
    frame=(radio/Dot11(type=0,subtype=8,addr1='ff:ff:ff:ff:ff:ff',addr2='02:00:00:00:00:01',addr3='02:00:00:00:00:01')/
           Dot11Beacon()/Dot11Elt(ID=221,info=bytes.fromhex('fa0bbc0d00f21901')+basic))
    with tempfile.TemporaryDirectory() as tmp:
        path=Path(tmp)/'synthetic.pcap'
        wrpcap(str(path),[frame])
        return path.read_bytes()


class ReceiverTests(unittest.TestCase):
    def setUp(self):
        self.read_fd,self.write_fd=os.pipe()
        self.receiver=Receiver(input_fd=self.read_fd)

    def tearDown(self):
        self.receiver.close()
        os.close(self.read_fd)
        if self.write_fd is not None:
            os.close(self.write_fd)

    def end_input(self):
        os.close(self.write_fd)
        self.write_fd=None

    def test_fragmented_input_and_idle_running_distinction(self):
        raw=frame_bytes()
        self.assertEqual(self.receiver.status()['state'],'stopped')
        self.receiver.start_detection()
        for byte in raw[:24]:
            os.write(self.write_fd,bytes([byte]))
        wait_for(lambda:self.receiver.status()['state']=='running')
        self.assertIsNone(self.receiver.status()['last_report_age'])
        for offset in range(24,len(raw),3):
            os.write(self.write_fd,raw[offset:offset+3])
        wait_for(lambda:self.receiver.counts['decoded']==1)
        self.end_input()
        wait_for(lambda:self.receiver.state=='stopped')
        self.assertEqual(self.receiver.counts['errors'],0)
        self.assertEqual(len(self.receiver.snapshot()['tracks']),1)
        self.assertFalse(self.receiver.status()['tak_enabled'])

    def test_truncated_and_oversized_input_fail_without_payload_details(self):
        raw=frame_bytes()
        self.receiver.start_detection()
        os.write(self.write_fd,raw[:-3])
        self.end_input()
        wait_for(lambda:self.receiver.state=='error')
        self.assertNotIn('SYNTHETIC-PIPE',self.receiver.message)
        self.assertEqual(self.receiver.counts['decoded'],0)

    def test_oversized_record_is_rejected_before_payload_read(self):
        self.receiver.start_detection()
        os.write(self.write_fd,frame_bytes()[:24]+struct.pack('<IIII',1,0,0xffffffff,0xffffffff))
        wait_for(lambda:self.receiver.state=='error')
        self.assertEqual(self.receiver.counts['packets'],0)

    def test_bad_radio_frame_does_not_stop_following_valid_reports(self):
        raw=frame_bytes()
        malformed=struct.pack('<IIII',1,0,1,1)+b'\x00'
        self.receiver.start_detection()
        os.write(self.write_fd,raw[:24]+malformed+raw[24:])
        self.end_input()
        wait_for(lambda:self.receiver.state=='stopped')
        self.assertEqual(self.receiver.counts['packets'],2)
        self.assertEqual(self.receiver.counts['decoded'],1)
        self.assertEqual(self.receiver.counts['errors'],1)

    def test_stop_interrupts_idle_input_without_eof(self):
        self.receiver.start_detection()
        os.write(self.write_fd,frame_bytes()[:24])
        wait_for(lambda:self.receiver.state=='running')
        before=time.monotonic()
        self.receiver.stop_detection()
        self.assertLess(time.monotonic()-before,1)
        self.assertFalse(self.receiver.thread.is_alive())
        self.assertEqual(self.receiver.state,'stopped')

    def test_channel_mismatch_is_rejected_before_sudo_or_capture(self):
        info=subprocess.CompletedProcess([],0,stdout='Interface test0\n\ttype monitor\n\tchannel 44 (5220 MHz), width: 20 MHz\n')
        with patch('aetherlink_scout.capture.os.geteuid',return_value=1000), patch('aetherlink_scout.capture.subprocess.run',return_value=info) as run, patch('aetherlink_scout.capture.subprocess.Popen') as popen:
            with self.assertRaisesRegex(CaptureError,'channel 44'):
                LiveCapture('test0').start()
            self.assertEqual(run.call_count,1)
            self.assertEqual(run.call_args.args[0],['iw','dev','test0','info'])
            popen.assert_not_called()

    def test_packet_frequency_drift_stops_prepared_capture(self):
        read_fd=self.read_fd
        class PreparedCapture:
            stopped=False
            def __init__(self,interface): pass
            def start(self): return read_fd
            def stop(self): self.stopped=True
            def outcome(self): return 0
        self.receiver=Receiver(live_interface='test0',capture_factory=PreparedCapture)
        self.receiver.start_detection()
        os.write(self.write_fd,frame_bytes(5220))
        wait_for(lambda:not self.receiver.thread.is_alive())
        status=self.receiver.status()
        self.assertEqual(status['state'],'error')
        self.assertEqual(status['last_frequency_mhz'],5220)
        self.assertEqual(status['received_frequencies_mhz'],[5220])
        self.assertEqual(status['expected_channel'],6)
        self.assertEqual(status['counts']['decoded'],0)
        self.assertTrue(self.receiver.capture.stopped)

    def test_expected_packet_frequency_decodes_and_is_recorded(self):
        self.receiver.start_detection()
        os.write(self.write_fd,frame_bytes(2437))
        self.end_input()
        wait_for(lambda:self.receiver.state=='stopped')
        self.assertEqual(self.receiver.status()['last_frequency_mhz'],2437)
        self.assertEqual(self.receiver.status()['counts']['decoded'],1)

    def test_snapshot_keeps_partial_field_age_and_resynchronizes_bounded_events(self):
        clock=[100.]
        receiver=Receiver(clock=lambda:clock[0])
        item=demo_dataset()['records'][0]
        receiver.publish(item)
        clock[0]+=10
        receiver.publish({**item,'aircraft':None,'operator':None})
        track=receiver.snapshot()['tracks'][0]
        self.assertEqual(track['fieldTimes']['operator'],0)
        self.assertEqual(track['lastSeen'],10)
        for _ in range(MAX_EVENTS):
            receiver.publish(item)
        self.assertEqual(len(receiver.events),MAX_EVENTS)
        self.assertTrue(receiver.update(0)['resynchronized'])
        for i in range(MAX_TRACKS+1):
            receiver.publish({**item,'key':str(i)})
        self.assertEqual(len(receiver.tracks),MAX_TRACKS)
        self.assertGreater(receiver.evicted_tracks,0)
        self.assertIn('event: receiver\n',event(receiver.update(receiver.sequence)))

    def test_start_stop_api_is_guarded_and_replay_cannot_start_capture(self):
        receiver=Receiver(simulation=demo_dataset())
        with TestClient(create_app(receiver=receiver),base_url='http://127.0.0.2') as client:
            self.assertFalse(client.get('/api/health').json()['live_capture'])
            self.assertEqual(client.get('/api/replay').status_code,409)
            self.assertEqual(client.post('/api/detection/start',json={},headers={'origin':'https://outside.example'}).status_code,403)
            self.assertEqual(client.post('/api/detection/start').status_code,415)
            started=client.post('/api/detection/start',json={}).json()
            repeated=client.post('/api/detection/start',json={}).json()
            self.assertEqual(started['status']['session_id'],repeated['status']['session_id'])
            self.assertEqual(client.get('/api/receiver',headers={'sec-fetch-site':'cross-site'}).status_code,403)
            stopped=client.post('/api/detection/stop',json={}).json()
            self.assertEqual(stopped['status']['state'],'stopped')
            restarted=client.post('/api/detection/start',json={}).json()
            self.assertNotEqual(restarted['status']['session_id'],started['status']['session_id'])
        with TestClient(create_app(demo_dataset()),base_url='http://127.0.0.2') as client:
            self.assertEqual(client.post('/api/detection/start',json={}).status_code,409)
            self.assertEqual(client.get('/api/events').status_code,404)

    def test_live_capture_failure_is_visible_and_uplink_is_protected(self):
        class UnavailableCapture:
            def __init__(self,interface): pass
            def start(self): raise CaptureError('Prepared monitor radio unavailable')
            def stop(self): pass
        receiver=Receiver(live_interface='test0',capture_factory=UnavailableCapture)
        with TestClient(create_app(receiver=receiver),base_url='http://127.0.0.2') as client:
            response=client.post('/api/detection/start',json={})
            self.assertEqual(response.status_code,503)
            self.assertEqual(client.get('/api/receiver').json()['status']['state'],'error')
        with self.assertRaises(CaptureError):
            LiveCapture('wlp3s0')
        with self.assertRaises(CaptureError):
            LiveCapture('bad;command')

    def test_stop_terminates_a_real_unprivileged_producer(self):
        raw=frame_bytes()
        class SyntheticCapture(LiveCapture):
            def start(self):
                self.log=io.BytesIO()
                self.process=subprocess.Popen([sys.executable,'-c',f'import os,time;os.write(1,{raw[:24]!r});time.sleep(60)'],stdout=subprocess.PIPE,stderr=subprocess.DEVNULL)
                return self.process.stdout.fileno()
        receiver=Receiver(live_interface='synthetic0',capture_factory=SyntheticCapture)
        try:
            receiver.start_detection()
            wait_for(lambda:receiver.state=='running')
            receiver.stop_detection()
            self.assertIsNotNone(receiver.capture.process.poll())
            self.assertFalse(receiver.status()['detection_active'])
            self.assertFalse(receiver.thread.is_alive())
        finally:
            receiver.close()

    def synthetic_segments(self, *, exits=(124,124,None), renewal_error=False, pending_start=None):
        raw=frame_bytes(2437)
        captures=[]
        class SyntheticSegment(LiveCapture):
            def __init__(self,interface):
                super().__init__(interface)
                self.index=len(captures)
                captures.append(self)
            def start(self):
                if self.index and renewal_error:
                    raise CaptureError('Cached capture authorization unavailable')
                if self.index and pending_start:
                    pending_start[0].set()
                    pending_start[1].wait(timeout=3)
                code=exits[min(self.index,len(exits)-1)]
                self.log=io.BytesIO()
                script=f'import os,time,sys;os.write(1,{raw!r});time.sleep({.1 if code is not None else 60});sys.exit({code or 0})'
                self.process=subprocess.Popen([sys.executable,'-c',script],stdout=subprocess.PIPE,stderr=subprocess.DEVNULL)
                return self.process.stdout.fileno()
        self.receiver=Receiver(live_interface='synthetic0',capture_factory=SyntheticSegment)
        return captures

    def test_renewal_preserves_session_tracks_and_cumulative_counters(self):
        captures=self.synthetic_segments()
        first=self.receiver.start_detection()
        wait_for(lambda:self.receiver.counts['decoded']==3)
        status=self.receiver.status()
        self.assertEqual(status['session_id'],first['session_id'])
        self.assertEqual(status['capture_segments'],3)
        self.assertTrue(status['continuous_capture'])
        self.assertEqual(status['capture_segment_seconds'],120)
        self.assertEqual(len(self.receiver.tracks),1)
        self.assertEqual(self.receiver.sequence,3)
        self.assertEqual(status['counts']['errors'],0)
        self.assertEqual(status['received_frequencies_mhz'],[2437])
        self.receiver.stop_detection()
        self.assertTrue(all(c.process.poll() is not None for c in captures))
        self.assertFalse(self.receiver.status()['detection_active'])
        self.assertEqual(self.receiver.counts['decoded'],3)

    def test_stop_during_renewal_gap_never_starts_a_replacement(self):
        captures=self.synthetic_segments(exits=(124,None))
        self.receiver.start_detection()
        wait_for(lambda:self.receiver.state=='renewing')
        self.receiver.stop_detection()
        time.sleep(.3)
        self.assertEqual(len(captures),1)
        self.assertEqual(self.receiver.state,'stopped')
        self.assertFalse(self.receiver.thread.is_alive())

    def test_stop_during_in_flight_segment_start_stops_the_replacement(self):
        entered,release=threading.Event(),threading.Event()
        captures=self.synthetic_segments(exits=(124,None),pending_start=(entered,release))
        self.receiver.start_detection()
        self.assertTrue(entered.wait(timeout=3))
        failures=[]
        def stop():
            try: self.receiver.stop_detection()
            except Exception as error: failures.append(error)
        stopper=threading.Thread(target=stop)
        stopper.start()
        self.assertTrue(self.receiver.stop.wait(timeout=1))
        release.set()
        stopper.join(timeout=3)
        self.assertFalse(stopper.is_alive())
        self.assertEqual(failures,[])
        self.assertEqual(len(captures),2)
        self.assertTrue(all(c.process.poll() is not None for c in captures))
        self.assertFalse(self.receiver.status()['detection_active'])

    def test_unexpected_exit_does_not_loop_or_discard_reports(self):
        for outcome in (0,1,137):
            with self.subTest(outcome=outcome):
                self.receiver.close()
                captures=self.synthetic_segments(exits=(outcome,))
                self.receiver.start_detection()
                wait_for(lambda:not self.receiver.thread.is_alive())
                self.assertEqual(len(captures),1)
                self.assertEqual(self.receiver.state,'error')
                self.assertEqual(self.receiver.counts['decoded'],1)
                self.assertIn('unexpectedly',self.receiver.message)

    def test_renewal_authorization_failure_retains_the_session_for_review(self):
        captures=self.synthetic_segments(exits=(124,),renewal_error=True)
        session=self.receiver.start_detection()['session_id']
        wait_for(lambda:not self.receiver.thread.is_alive())
        self.assertEqual(self.receiver.state,'error')
        self.assertEqual(self.receiver.status()['session_id'],session)
        self.assertEqual(self.receiver.counts['decoded'],1)
        self.assertEqual(len(self.receiver.tracks),1)
        self.assertEqual(len(captures),2)
        self.assertFalse(self.receiver.status()['detection_active'])


if __name__=='__main__':
    unittest.main()
