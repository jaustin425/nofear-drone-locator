"""Scan scheduling and cancellation with fake commands/processes only."""
import os
import io
from pathlib import Path
import subprocess
import sys
import threading
import time
import unittest
from unittest.mock import Mock, patch

from aetherlink_scout.capture import CaptureError, ScanCapture
from aetherlink_scout.receiver import Receiver
from test_receiver import frame_bytes, wait_for

INTERFACE = 'wlx9cefd5f67d4c'


class ScanTests(unittest.TestCase):
    def setUp(self):
        self.capture = ScanCapture(INTERFACE)
        self.capture.process = Mock()
        self.capture.process.poll.return_value = None
        self.commands = []
        self.channel = 6
        self.stuck = self.denied = self.restricted = False

    def command(self, args, **kwargs):
        self.commands.append(args)
        text = ''
        if args[:3] == ['ip', 'route', 'show']:
            text = 'default via 192.0.2.1 dev wlp3s0'
        elif args[:2] == ['iw', 'phy']:
            text = '* 2437 MHz [6] (7.0 dBm)\n* 5745 MHz [149] (10.0 dBm)' + (' (disabled)' if self.restricted else '')
        elif args[0] == 'sudo':
            if self.denied:
                raise subprocess.CalledProcessError(1, args)
            if not self.stuck:
                self.channel = int(args[-1])
        elif args == ['iw', 'dev', INTERFACE, 'info']:
            frequency = {6: 2437, 149: 5745}[self.channel]
            text = f'Interface {INTERFACE}\n type monitor\n wiphy 9\n channel {self.channel} ({frequency} MHz), width: 20 MHz'
        return subprocess.CompletedProcess(args, 0, stdout=text)

    def run_worker(self):
        self.capture.worker = threading.Thread(target=self.capture.scan)
        self.capture.worker.start()

    def test_schedule_verified_channels_and_stop_during_dwell(self):
        with patch('aetherlink_scout.capture.subprocess.run', side_effect=self.command), patch('aetherlink_scout.capture.SCAN_PLAN', ((6,2437,.03),(149,5745,.02))):
            self.run_worker()
            wait_for(lambda: sum(cmd[0]=='sudo' for cmd in self.commands) >= 3)
            self.capture.cancel_scan()
            count = len(self.commands)
            time.sleep(.08)
            self.assertEqual(len(self.commands), count)
        requested = [int(cmd[-1]) for cmd in self.commands if cmd[0]=='sudo']
        self.assertEqual(requested[:3], [149,6,149])
        self.assertFalse(self.capture.worker.is_alive())
        self.assertEqual(self.capture.verified[:2], (149,5745))
        self.assertTrue(all('wlp3s0' not in cmd for cmd in self.commands))

    def test_failed_or_stuck_retune_stops_capture_and_prevents_renewal(self):
        for failure in ('stuck','denied'):
            with self.subTest(failure=failure):
                self.setUp()
                setattr(self, failure, True)
                with patch('aetherlink_scout.capture.subprocess.run', side_effect=self.command), patch('aetherlink_scout.capture.SCAN_PLAN', ((6,2437,.01),(149,5745,.01))):
                    self.run_worker()
                    self.capture.worker.join(2)
                    self.assertFalse(self.capture.worker.is_alive())
                    self.capture.process.send_signal.assert_called_once()
                    self.capture.process.wait.return_value = 124
                    with self.assertRaises(CaptureError):
                        self.capture.outcome()
                self.assertEqual(sum(cmd[0]=='sudo' for cmd in self.commands), 1)

    def test_stop_waits_for_inflight_retune_then_no_more_commands(self):
        entered, release = threading.Event(), threading.Event()
        def blocked(args, **kwargs):
            if args[0]=='sudo':
                entered.set()
                release.wait(2)
            return self.command(args, **kwargs)
        with patch('aetherlink_scout.capture.subprocess.run', side_effect=blocked), patch('aetherlink_scout.capture.SCAN_PLAN', ((6,2437,.01),(149,5745,.01))):
            self.run_worker()
            self.assertTrue(entered.wait(2))
            stopper = threading.Thread(target=self.capture.stop)
            stopper.start()
            time.sleep(.03)
            self.assertTrue(stopper.is_alive())
            release.set()
            stopper.join(2)
            self.assertFalse(stopper.is_alive())
            self.assertFalse(self.capture.worker.is_alive())
            count = len(self.commands)
            time.sleep(.05)
            self.assertEqual(len(self.commands), count)

    def test_process_exit_or_bound_prevents_later_retunes(self):
        with patch('aetherlink_scout.capture.subprocess.run', side_effect=self.command), patch('aetherlink_scout.capture.CAPTURE_SEGMENT_SECONDS', .02):
            self.run_worker()
            self.capture.worker.join(1)
            self.assertFalse(self.capture.worker.is_alive())
            self.assertEqual(self.commands, [])
        self.setUp()
        self.capture.process.poll.return_value = 124
        self.run_worker()
        self.capture.worker.join(1)
        self.assertFalse(self.capture.worker.is_alive())
        self.assertEqual(self.commands, [])

    def test_capabilities_discovered_and_restricted_plan_rejected_before_mutation(self):
        info = ' type monitor\n wiphy 9\n channel 6 (2437 MHz)'
        with patch('aetherlink_scout.capture.Path.resolve', return_value=Path('/mock/mt76x0u')), patch('aetherlink_scout.capture.subprocess.run', side_effect=self.command):
            self.capture.verify_channel(info)
            self.assertIn(['iw','phy','phy9','info'], self.commands)
            self.restricted = True
            with self.assertRaisesRegex(CaptureError, '149'):
                self.capture.verify_channel(info)
            self.assertFalse(any(cmd[0]=='sudo' for cmd in self.commands))
        with self.assertRaises(CaptureError):
            ScanCapture('wlp3s0')
        with self.assertRaises(ValueError):
            Receiver(scan=True)

    def test_mixed_frequency_tracks_and_unplanned_frequency_stops(self):
        read_fd, write_fd = os.pipe()
        class Capture:
            def __init__(self, interface): pass
            def start(self): return read_fd
            def stop(self): pass
            def outcome(self): return 0
        receiver = Receiver(live_interface=INTERFACE, scan=True, capture_factory=Capture)
        try:
            receiver.start_detection()
            first = frame_bytes(2437)
            second = frame_bytes(5745).replace(b'SYNTHETIC-PIPE', b'AETHER-SCOUT-2').replace(bytes.fromhex('020000000001'), bytes.fromhex('020000000002'))
            os.write(write_fd, first+second[24:])
            wait_for(lambda: receiver.counts['decoded']==2)
            status = receiver.status()
            self.assertEqual(len(receiver.tracks), 2)
            self.assertEqual(status['received_frequencies_mhz'], [2437,5745])
            self.assertTrue(status['scan_enabled'])
            self.assertIsNone(status['expected_channel'])
            os.write(write_fd, frame_bytes(5220)[24:])
            wait_for(lambda: not receiver.thread.is_alive())
            self.assertEqual(receiver.state, 'error')
            self.assertIn('scan channels', receiver.message)
            self.assertEqual(len(receiver.tracks), 2)
        finally:
            receiver.close()
            os.close(write_fd)
            os.close(read_fd)

    def test_bounded_scan_renewal_keeps_session_and_stops_all_workers(self):
        captures = []
        class Segment(ScanCapture):
            def __init__(self, interface):
                super().__init__(interface)
                self.index = len(captures)
                captures.append(self)
            def start(self):
                self.prepare_capture()
                raw = frame_bytes(2437 if self.index % 2 == 0 else 5745)
                code = 124 if self.index < 2 else 0
                pause = .12 if self.index < 2 else 60
                self.log = io.BytesIO()
                self.process = subprocess.Popen([sys.executable, '-c', f'import os,time;os.write(1,{raw!r});time.sleep({pause});raise SystemExit({code})'], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
                self.worker = threading.Thread(target=self.scan)
                self.worker.start()
                return self.process.stdout.fileno()
        receiver = Receiver(live_interface=INTERFACE, scan=True, capture_factory=Segment)
        with patch('aetherlink_scout.capture.subprocess.run', side_effect=self.command), patch('aetherlink_scout.capture.SCAN_PLAN', ((6,2437,.03),(149,5745,.02))):
            try:
                first = receiver.start_detection()
                wait_for(lambda: receiver.counts['decoded'] == 3)
                status = receiver.status()
                self.assertEqual(status['session_id'], first['session_id'])
                self.assertEqual(status['capture_segments'], 3)
                self.assertEqual(status['counts']['errors'], 0)
                self.assertEqual(len(receiver.tracks), 1)
                self.assertEqual(status['received_frequencies_mhz'], [2437,5745])
                self.assertIsNotNone(status['last_verified_radio'])
                receiver.stop_detection()
                self.assertFalse(receiver.status()['detection_active'])
                self.assertTrue(all(not c.worker.is_alive() and c.process.poll() is not None for c in captures))
                count = len(self.commands)
                time.sleep(.08)
                self.assertEqual(len(self.commands), count)
            finally:
                receiver.close()


if __name__ == '__main__':
    unittest.main()
