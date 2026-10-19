"""USB-generation fault injection; mock sysfs/commands and normal-user producers only."""
import copy
import io
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from aetherlink_scout.app import create_app
from aetherlink_scout.capture import CaptureError
from aetherlink_scout.combined import CombinedReceiver, Source
from aetherlink_scout.receiver import Receiver
from aetherlink_scout.recovery import RadioRecovery, RadioAbsent, RecoveryCancelled
from test_combined import synthetic_sources, wifi_bytes
from test_receiver import wait_for


class SyntheticRecovery:
    """Controllable physical generation with real producer processes."""
    def __init__(self,raw):
        self.raw=raw;self.index=1;self.present=True
        self.captures=[];self.prepared=[];self.block=None;self.fail=False
    def disconnected(self,capture):return not self.present or capture.recovery_generation!=self.index
    def check_capture(self,capture):
        if self.disconnected(capture):raise RadioAbsent('Synthetic USB loss')
    def prepare(self,stop):
        self.prepared.append(self.index)
        if self.block:
            self.block[0].set();self.block[1].wait(4)
        if stop.is_set():raise RecoveryCancelled('Synthetic cancellation')
        if not self.present:raise RadioAbsent('Synthetic radio absent')
        if self.fail:raise CaptureError('Synthetic authorization/driver failure')
    def capture(self,interface):
        if not self.present:raise RadioAbsent('Synthetic radio absent')
        recovery=self
        class Capture:
            def __init__(self):self.recovery_generation=recovery.index;self.process=None
            def start(self):
                self.process=subprocess.Popen([sys.executable,'-c',f'import os,time;os.write(1,{recovery.raw!r});time.sleep(60)'],stdout=subprocess.PIPE,stderr=subprocess.DEVNULL)
                return self.process.stdout.fileno()
            def stop(self):
                if self.process:
                    if self.process.poll() is None:self.process.terminate()
                    self.process.wait(timeout=3);self.process.stdout.close()
            def outcome(self):return self.process.wait(timeout=3)
        capture=Capture();self.captures.append(capture);return capture


def recovery_sources():
    sources,captures=synthetic_sources()
    recovery=SyntheticRecovery(wifi_bytes())
    sources[0].receiver=Receiver(live_interface='test-wifi6',capture_factory=recovery.capture,recovery=recovery)
    return sources,recovery,captures


class RecoveryTests(unittest.TestCase):
    def setUp(self):self.receiver=None
    def tearDown(self):
        if self.receiver:self.receiver.close()

    def started(self):
        sources,recovery,captures=recovery_sources()
        self.receiver=CombinedReceiver(sources=sources)
        self.receiver.start_detection();wait_for(lambda:self.receiver.status()['counts']['decoded']==9)
        return sources,recovery,captures

    def test_disconnect_reconnect_preserves_shared_session_tracks_history_and_healthy_feeds(self):
        sources,recovery,captures=self.started()
        before=self.receiver.snapshot();child_session=sources[0].receiver.session_id
        first_capture=recovery.captures[0]
        recovery.present=False
        wait_for(lambda:self.receiver.state=='recovering')
        self.assertTrue(all(s.receiver.status()['detection_active'] for s in sources))
        self.assertTrue(all(c.process.poll() is None for c in captures))
        self.assertIsNotNone(first_capture.process.poll())
        # A second radio's new report still reaches the shared map during outage.
        item=next(iter(self.receiver.tracks.values()))['fields']
        sources[1].receiver.publish({**item,'key':'synthetic-new-aircraft','serial':'AETHERLINK-SCOUT-SECOND','timestamp':time.time(),'aircraft':{'lat':35.02,'lon':-115.03,'altitude':None},'operator':None})
        self.assertEqual(len(self.receiver.tracks),2)
        recovery.index=2;recovery.present=True
        wait_for(lambda:sources[0].receiver.capture_recoveries==1)
        wait_for(lambda:self.receiver.status()['counts']['decoded']==12)
        value=self.receiver.snapshot()
        self.assertEqual(value['status']['session_id'],before['status']['session_id'])
        self.assertEqual(sources[0].receiver.session_id,child_session)
        self.assertEqual(value['status']['capture_renewals'],0)
        self.assertEqual(value['status']['capture_recoveries'],1)
        self.assertEqual(self.receiver.history.list()['total'],2)
        self.assertIn(before['tracks'][0]['key'],self.receiver.tracks)
        self.receiver.stop_detection()
        self.assertTrue(all(c.process.poll() is not None for c in recovery.captures))

    def test_stop_while_waiting_never_reprepares_or_restarts_returning_radio(self):
        _,recovery,_=self.started();recovery.present=False
        wait_for(lambda:self.receiver.state=='recovering')
        self.receiver.stop_detection();calls=len(recovery.prepared)
        recovery.present=True;recovery.index=2;time.sleep(1.1)
        self.assertEqual(len(recovery.prepared),calls)
        self.assertEqual(len(recovery.captures),1)
        self.assertFalse(self.receiver.status()['detection_active'])

    def test_stop_during_prepare_waits_and_does_not_launch_replacement(self):
        _,recovery,_=self.started();entered,release=threading.Event(),threading.Event()
        recovery.block=(entered,release);recovery.index=2
        self.assertTrue(entered.wait(4))
        stopper=threading.Thread(target=self.receiver.stop_detection);stopper.start()
        time.sleep(.1);self.assertTrue(stopper.is_alive())
        release.set();stopper.join(5)
        self.assertFalse(stopper.is_alive())
        self.assertEqual(len(recovery.captures),1)
        self.assertFalse(self.receiver.status()['detection_active'])

    def test_authorization_or_preparation_error_stops_group_without_loop(self):
        _,recovery,captures=self.started();recovery.fail=True;recovery.index=2
        wait_for(lambda:not self.receiver.thread.is_alive())
        self.assertEqual(self.receiver.state,'error')
        self.assertEqual(len(recovery.prepared),1)
        self.assertTrue(all(c.process.poll() is not None for c in captures))

    def test_unexpected_capture_failure_without_usb_loss_is_not_recovered(self):
        _,recovery,_=self.started();recovery.captures[0].process.terminate()
        wait_for(lambda:not self.receiver.thread.is_alive())
        self.assertEqual(self.receiver.state,'error')
        self.assertEqual(recovery.prepared,[])

    def test_explicit_autostart_once_and_stop_survives_new_browser_connection(self):
        sources,recovery,_=recovery_sources();self.receiver=CombinedReceiver(sources=sources)
        with TestClient(create_app(receiver=self.receiver,autostart=True),base_url='http://127.0.0.2') as client:
            wait_for(lambda:self.receiver.status()['counts']['decoded']==9)
            session=self.receiver.session_id
            client.get('/api/health');self.assertEqual(self.receiver.session_id,session)
            client.post('/api/detection/stop',json={})
            client.get('/api/receiver');time.sleep(.1)
            self.assertFalse(self.receiver.status()['detection_active'])
            self.assertEqual(len(recovery.captures),1)
        with self.assertRaises(ValueError):create_app(receiver=Receiver(),autostart=True)

    def test_unconfirmed_old_capture_cleanup_never_reprepares_radio(self):
        _,recovery,_=self.started()
        original=recovery.captures[0].stop
        blocked=threading.Event()
        def stop():
            if not blocked.is_set():raise CaptureError('Synthetic unconfirmed capture exit')
            original()
        recovery.captures[0].stop=stop;recovery.index=2
        try:
            wait_for(lambda:self.receiver.stop_unconfirmed)
            self.assertTrue(self.receiver.thread.is_alive())
            self.assertEqual(recovery.prepared,[])
        finally:blocked.set()
        wait_for(lambda:not self.receiver.thread.is_alive())
        self.assertEqual(recovery.prepared,[])

    def test_cli_recovery_autostart_guards_for_replay_and_scan(self):
        for args in (['--demo','--recover-radios'],['--simulate-demo','--autostart'],['--combined','--autostart']):
            result=subprocess.run([sys.executable,'-m','aetherlink_scout',*args],capture_output=True,text=True)
            self.assertEqual(result.returncode,2)


class RadioProfileTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='scout-radio-profile-',dir='/tmp');self.root=Path(self.temp.name)
        self.name='wlx9cefd5f67d4c';self.commands=[];self.stop=threading.Event()
        self.device=self.root/'usb/1-2:1.0';self.device.mkdir(parents=True)
        driver=self.root/'drivers/mt76x0u';driver.mkdir(parents=True)
        (self.device/'driver').symlink_to(driver)
        self.radio=self.root/self.name;self.radio.mkdir()
        (self.radio/'device').symlink_to(self.device)
        (self.radio/'address').write_text('9c:ef:d5:f6:7d:4c\n');(self.radio/'ifindex').write_text('5\n')
        self.mode='managed';self.channel=44;self.bad_route=False;self.extra=False;self.restricted=False
        self.cancel_command=None;self.authorization=True
        self.recovery=RadioRecovery(self.name,'9c:ef:d5:f6:7d:4c','mt76x0u',6,2437,sysnet=self.root,run=self.run_command)
    def tearDown(self):self.temp.cleanup()
    def run_command(self,command,**kwargs):
        self.commands.append(command)
        args=command[6:] if command[:2]==['sudo','-n'] else command
        if args==['true'] and not self.authorization:raise subprocess.CalledProcessError(1,command)
        if args==['ip','route','show','default']:
            output='default dev '+(self.name if self.bad_route else 'wlp3s0')+'\n'
        elif args==['iw','dev',self.name,'info']:
            output=f' type {self.mode}\n wiphy 1\n channel {self.channel} ({2437 if self.channel==6 else 5220} MHz)\n'
        elif args==['iw','dev']:
            output='phy#1\n Interface '+self.name+'\n'+(' Interface extra0\n' if self.extra else '')
        elif args==['iw','phy','phy1','info']:
            output=' * monitor\n * 2412 MHz [1] (10 dBm)\n * 2437 MHz [6] (10 dBm)'+(' (no IR)' if self.restricted else '')+'\n'
        else:
            output=''
            if args[-2:]==['type','monitor']:self.mode='monitor'
            if len(args)>2 and args[-2]=='channel':self.channel=int(args[-1])
        if self.cancel_command and args==self.cancel_command:self.stop.set()
        return subprocess.CompletedProcess(command,0,stdout=output)
    def mutations(self):return [c for c in self.commands if c[:2]==['sudo','-n'] and c[-1]!='true']

    def test_pinned_adapter_restored_to_fixed_channel_using_bounded_commands(self):
        self.recovery.prepare(self.stop)
        self.assertEqual(self.mode,'monitor');self.assertEqual(self.channel,6)
        commands=self.mutations()
        self.assertEqual(len(commands),6)
        self.assertTrue(all(c[:6]==['sudo','-n','timeout','--signal=TERM','--kill-after=1s','4s'] for c in commands))
        self.assertEqual(commands[-2][-2:],['channel','1'])
        self.assertEqual(commands[-1][-2:],['channel','6'])

    def test_absent_wrong_identity_uplink_sibling_restriction_and_authorization_guard(self):
        cases=('absent','identity','route','sibling','restricted','auth')
        for case in cases:
            with self.subTest(case=case):
                self.commands.clear();self.bad_route=case=='route';self.extra=case=='sibling';self.restricted=case=='restricted';self.authorization=case!='auth'
                (self.radio/'address').write_text('00:00:00:00:00:00' if case=='identity' else '9c:ef:d5:f6:7d:4c')
                if case=='absent':(self.radio/'ifindex').unlink()
                else:(self.radio/'ifindex').write_text('5')
                with self.assertRaises(CaptureError):self.recovery.prepare(self.stop)
                self.assertEqual(self.mutations(),[])

    def test_same_name_new_usb_generation_is_detected_and_driver_mismatch_is_fatal(self):
        capture=self.recovery.capture(self.name)
        self.recovery.check_capture(capture)
        (self.radio/'ifindex').write_text('14')
        self.assertTrue(self.recovery.disconnected(capture))
        self.recovery.last_check=0
        with self.assertRaises(RadioAbsent):self.recovery.check_capture(capture)
        (self.radio/'address').write_text('00:00:00:00:00:00')
        with self.assertRaises(CaptureError):self.recovery.disconnected(capture)
        self.assertEqual(self.mutations(),[])

    def test_cancel_between_commands_prevents_later_retune(self):
        self.cancel_command=['ip','link','set',self.name,'down']
        with self.assertRaises(RecoveryCancelled):self.recovery.prepare(self.stop)
        self.assertFalse(any(c[-2:]==['type','monitor'] or c[-2:]==['channel','6'] for c in self.commands))


if __name__=='__main__':unittest.main()
