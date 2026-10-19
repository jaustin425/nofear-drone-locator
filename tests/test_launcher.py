"""Launcher behavior with fake sudo/network commands in /tmp. No real radio changes."""
import os
from pathlib import Path
import shutil
import signal
import subprocess
import tempfile
import time
import unittest

MOCK = r'''#!/usr/bin/python3
import os, pathlib, signal, sys, time
root=pathlib.Path(os.environ['SCOUT_MOCK_ROOT'])
name=pathlib.Path(sys.argv[0]).name
args=sys.argv[1:]
def trace(value):
    with (root/'trace').open('a') as output: output.write(value+'\n')
trace(name+' '+' '.join(args))
if name=='sudo':
    if args and args[0]=='-n': args=args[1:]
    if args in (['true'],['-v']): sys.exit(1 if os.environ.get('MOCK_NO_AUTH') else 0)
    if args[0] not in ('nmcli','ip','iw','timeout'): sys.exit(99)
    os.execv(str(root/'mocks'/args[0]),args)
elif name=='readlink':
    print('/mock/'+('wrong-driver' if os.environ.get('MOCK_BAD_DRIVER') else 'mt76x0u'))
elif name=='nmcli':
    if 'GENERAL.STATE' in args: print('30' if os.environ.get('MOCK_NO_UPLINK') else '100')
    elif 'GENERAL.CON-UUID' in args:
        print('00000000-0000-0000-0000-000000000000' if (root/'connected').exists() else '')
    elif 'disconnect' in args: (root/'connected').unlink(missing_ok=True)
    elif 'connection' in args and 'up' in args: (root/'connected').touch()
elif name=='ip':
    if args[:3]==['route','show','default']:
        if not os.environ.get('MOCK_NO_UPLINK'): print('default via 192.0.2.1 dev wlp3s0 proto dhcp metric 600')
    elif args[-1]=='down': (root/'up').unlink(missing_ok=True)
    elif args[-1]=='up': (root/'up').touch()
elif name=='iw':
    if args[-1]=='info':
        print('Interface wlx9cefd5f67d4c\n\ttype '+(root/'mode').read_text())
        if (root/'up').exists():
            channel=(root/'channel').read_text()
            frequency={'1':'2412','6':'2437','44':'5220'}[channel]
            print('\tchannel '+channel+' ('+frequency+' MHz), width: 20 MHz')
    elif args[-2]=='type':
        if (root/'up').exists(): sys.exit(16)
        (root/'mode').write_text(args[-1])
    elif args[-2]=='channel':
        if not (root/'up').exists(): sys.exit(16)
        if os.environ.get('MOCK_CHANNEL_FAIL'): sys.exit(1)
        if not os.environ.get('MOCK_STUCK_CHANNEL'): (root/'channel').write_text(args[-1])
elif name=='python':
    if args==['-']:
        sys.stdin.read()
        if os.environ.get('MOCK_PORT_BUSY'): sys.exit(1)
    else:
        trace('app-start')
        if os.environ.get('MOCK_APP_HOLD'):
            def stop(*_):
                trace('app-stop')
                sys.exit(0)
            signal.signal(signal.SIGINT,stop)
            signal.signal(signal.SIGTERM,stop)
            while True: time.sleep(.05)
        trace('app-stop')
elif name=='timeout': sys.exit(124)
elif name=='tcpdump': sys.exit(99)
else: sys.exit(99)
'''


@unittest.skipIf(os.geteuid()==0,'Launcher intentionally refuses root')
class LauncherTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='scout-launcher-',dir='/tmp')
        self.root=Path(self.temp.name)
        (self.root/'scripts').mkdir()
        shutil.copyfile(Path(__file__).resolve().parent.parent/'scripts/capture_panda.sh',self.root/'scripts/capture_panda.sh')
        mocks=self.root/'mocks';mocks.mkdir()
        wrapper=mocks/'mock';wrapper.write_text(MOCK);wrapper.chmod(0o700)
        for name in ('sudo','nmcli','iw','ip','readlink','tcpdump','timeout'): (mocks/name).symlink_to(wrapper)
        python=self.root/'.venv/bin/python';python.parent.mkdir(parents=True);python.symlink_to(wrapper)
        (self.root/'up').touch();(self.root/'connected').touch()
        (self.root/'mode').write_text('managed');(self.root/'channel').write_text('44')
        self.env=dict(os.environ,PATH=str(mocks)+':'+os.environ['PATH'],SCOUT_MOCK_ROOT=str(self.root))

    def tearDown(self): self.temp.cleanup()

    def run_launcher(self, **flags):
        result=subprocess.run(['bash',str(self.root/'scripts/capture_panda.sh'),'--live'],env={**self.env,**flags},capture_output=True,text=True,timeout=10)
        return result,(self.root/'trace').read_text()

    def test_connected_panda_workaround_and_original_connection_restoration(self):
        result,trace=self.run_launcher()
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertIn('iw dev wlx9cefd5f67d4c set channel 1',trace)
        self.assertIn('iw dev wlx9cefd5f67d4c set channel 6',trace)
        self.assertIn('device disconnect wlx9cefd5f67d4c',trace)
        self.assertIn('connection up uuid',trace)
        self.assertEqual((self.root/'mode').read_text(),'managed')
        self.assertTrue((self.root/'connected').exists())
        self.assertNotIn('ip link set wlp3s0',trace)
        self.assertLess(trace.index('app-stop'),trace.rindex('set type managed'))

    def test_scan_option_is_explicit_and_app_stops_before_restoration(self):
        result=subprocess.run(['bash',str(self.root/'scripts/capture_panda.sh'),'--live-scan'],env=self.env,capture_output=True,text=True,timeout=10)
        self.assertEqual(result.returncode,0,result.stderr)
        trace=(self.root/'trace').read_text()
        self.assertIn('--live-interface wlx9cefd5f67d4c --scan',trace)
        self.assertLess(trace.index('app-stop'),trace.rindex('set type managed'))
        # Merely launching prepares channel 6; scanning starts only on START.
        self.assertNotIn('set channel 149',trace)

    def test_prepared_monitor_adoption_without_mode_cycle_or_retune(self):
        (self.root/'mode').write_text('monitor');(self.root/'channel').write_text('6')
        (self.root/'connected').unlink()
        result,trace=self.run_launcher()
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertNotIn('set type monitor',trace)
        self.assertNotIn('set channel',trace)
        self.assertNotIn('device disconnect',trace)
        self.assertNotIn('connection up uuid',trace)
        self.assertIn('managed yes',trace)
        self.assertEqual((self.root/'mode').read_text(),'managed')

    def test_disconnected_managed_panda_does_not_require_active_profile(self):
        (self.root/'connected').unlink()
        result,trace=self.run_launcher()
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertNotIn('device disconnect',trace)
        self.assertNotIn('connection up uuid',trace)
        self.assertIn('app-start',trace)

    def test_channel_failure_restores_panda_without_launching_app(self):
        for flag in ('MOCK_STUCK_CHANNEL','MOCK_CHANNEL_FAIL'):
            with self.subTest(flag=flag):
                (self.root/'mode').write_text('managed');(self.root/'channel').write_text('44')
                (self.root/'connected').touch();(self.root/'trace').unlink(missing_ok=True)
                result,trace=self.run_launcher(**{flag:'1'})
                self.assertNotEqual(result.returncode,0)
                self.assertNotIn('app-start',trace)
                self.assertIn('set type managed',trace)
                self.assertTrue((self.root/'up').exists())

    def test_preflight_failures_do_not_change_network_state(self):
        for flag in ('MOCK_NO_AUTH','MOCK_BAD_DRIVER','MOCK_NO_UPLINK','MOCK_PORT_BUSY'):
            with self.subTest(flag=flag):
                (self.root/'trace').unlink(missing_ok=True)
                result,trace=self.run_launcher(**{flag:'1'})
                self.assertNotEqual(result.returncode,0)
                self.assertNotIn('set type',trace)
                self.assertNotIn('set channel',trace)
                self.assertNotIn('device disconnect',trace)
                self.assertNotIn('app-start',trace)

    def test_terminal_signal_stops_app_before_radio_restoration(self):
        process=subprocess.Popen(['bash',str(self.root/'scripts/capture_panda.sh'),'--live'],env={**self.env,'MOCK_APP_HOLD':'1'},stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,start_new_session=True)
        try:
            until=time.monotonic()+5
            while not (self.root/'trace').exists() or 'app-start' not in (self.root/'trace').read_text():
                if time.monotonic()>until: self.fail('Mock app did not start')
                time.sleep(.02)
            process.send_signal(signal.SIGTERM)
            self.assertEqual(process.wait(timeout=10),143)
            trace=(self.root/'trace').read_text()
            self.assertLess(trace.index('app-stop'),trace.rindex('set type managed'))
            self.assertNotIn('ip link set wlp3s0',trace)
        finally:
            if process.poll() is None:
                os.killpg(process.pid,signal.SIGTERM);process.wait(timeout=10)


if __name__=='__main__': unittest.main()
