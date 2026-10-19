"""Two independent mock radios; no real sudo, network changes or capture."""
import os
from pathlib import Path
import shutil
import signal
import subprocess
import tempfile
import time
import unittest

MOCK=r'''#!/usr/bin/python3
import os,pathlib,signal,sys,time
root=pathlib.Path(os.environ['SCOUT_DUAL_MOCK'])
name=pathlib.Path(sys.argv[0]).name
args=sys.argv[1:]
def trace(s):
    with (root/'trace').open('a') as f:f.write(s+'\n')
trace(name+' '+' '.join(args))
radios=('wlx9cefd5f67d4c','wlx1cbfce038535')
radio=next((r for r in radios if any(r in a for a in args)),None)
index=radios.index(radio) if radio else None
state=root/str(index) if radio else None
if name=='sudo':
    args=args[1:] if args[0]=='-n' else args
    if args in (['true'],['-v']):sys.exit(1 if os.environ.get('NO_AUTH') else 0)
    assert args[0] in ('iw','ip','nmcli','timeout')
    os.execv(str(root/'mocks'/args[0]),args)
elif name=='readlink':
    print('/driver/'+('mt76x0u' if index==0 else 'wrong' if os.environ.get('BAD_DRIVER') else 'rtl88x2bu'))
elif name=='nmcli':
    if 'GENERAL.STATE' in args:print('30' if os.environ.get('NO_UPLINK') and radio is None else '100')
    elif 'GENERAL.NM-MANAGED' in args:print('yes')
    elif 'GENERAL.CON-UUID' in args:print('00000000-0000-0000-0000-00000000000'+str(index))
    elif 'disconnect' in args:(state/'connected').unlink(missing_ok=True)
    elif 'connection' in args and 'up' in args:(state/'connected').touch()
elif name=='ip':
    if args[:2]==['route','get']:
        print('1.1.1.1 via 192.0.2.1 dev '+('wlx9cefd5f67d4c' if os.environ.get('NO_UPLINK') else 'wlp3s0')+' src 192.0.2.2')
    elif args[-1]=='down':(state/'up').unlink(missing_ok=True)
    elif args[-1]=='up':(state/'up').touch()
elif name=='iw':
    if args[0]=='phy':
        restricted=' (no IR)' if os.environ.get('RESTRICTED') else ''
        print(' * monitor\n * 2437.0 MHz [6] (7 dBm)\n * 5745.0 MHz [149] (15 dBm)'+restricted)
    elif args[-1]=='info':
        channel=(state/'channel').read_text()
        frequency={'1':2412,'6':2437,'44':5220,'149':5745}[channel]
        phy='1' if index==0 or os.environ.get('SAME_PHY') else '2'
        print('Interface '+radio+'\n type '+(state/'mode').read_text()+'\n wiphy '+phy+'\n channel '+channel+' ('+str(frequency)+' MHz), width: 20 MHz')
    elif args[-2]=='type':
        if (state/'up').exists():sys.exit(16)
        (state/'mode').write_text(args[-1])
    elif args[-2]=='channel':
        if os.environ.get('RETUNE_FAIL') and index==1:sys.exit(1)
        if not (os.environ.get('STUCK') and index==1):(state/'channel').write_text(args[-1])
elif name=='python':
    if args==['-']:
        code=sys.stdin.read()
        if ('import socket' in code and os.environ.get('PORT_BUSY')) or ('import dbus' in code and os.environ.get('NO_BLUETOOTH')):sys.exit(1)
    elif args[:3]==['-m','aetherlink_scout.tak','--validate-config']:
        sys.exit(1 if os.environ.get('BAD_TAK_CONFIG') else 0)
    else:
        trace('app-start')
        def app_end(*_):
            trace('app-stop')
            sys.exit(0)
        signal.signal(signal.SIGINT,app_end);signal.signal(signal.SIGTERM,app_end)
        if os.environ.get('APP_HOLD'):
            while True:time.sleep(.02)
        trace('app-stop')
        sys.exit(1 if os.environ.get('APP_FAIL') else 0)
elif name=='timeout':
    trace('capture-start '+radio)
    def end(*_):
        trace('capture-exit '+radio)
        sys.exit(143)
    signal.signal(signal.SIGTERM,end)
    if os.environ.get('CAPTURE_FAIL') and index==0:
        time.sleep(.1);trace('capture-exit '+radio);sys.exit(1)
    if os.environ.get('HOLD') or os.environ.get('CAPTURE_FAIL'):
        while True:time.sleep(.02)
    time.sleep(.2)
    trace('capture-exit '+radio)
    sys.exit(124)
else:sys.exit(99)
'''


@unittest.skipIf(os.geteuid()==0,'Launcher intentionally refuses root')
class DualLauncherTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='scout-dual-',dir='/tmp')
        self.root=Path(self.temp.name)
        (self.root/'scripts').mkdir()
        shutil.copyfile(Path(__file__).resolve().parent.parent/'scripts/capture_dual.sh',self.root/'scripts/capture_dual.sh')
        script=self.root/'scripts/capture_dual.sh'
        # Both Python dependency preflights use fake interpreters in this test
        # copy. Neither the real system D-Bus nor a radio is ever accessed.
        script.write_text(script.read_text().replace('/usr/bin/python3 -I -', '"$project_root/.venv/bin/python" -'))
        mocks=self.root/'mocks';mocks.mkdir()
        wrapper=mocks/'mock';wrapper.write_text(MOCK);wrapper.chmod(0o700)
        for name in ('sudo','nmcli','iw','ip','readlink','timeout','tcpdump'):(mocks/name).symlink_to(wrapper)
        python=self.root/'.venv/bin/python';python.parent.mkdir(parents=True);python.symlink_to(wrapper)
        for i in (0,1):
            state=self.root/str(i);state.mkdir()
            (state/'up').touch();(state/'connected').touch()
            (state/'mode').write_text('managed');(state/'channel').write_text('44')
        self.env=dict(os.environ,PATH=str(mocks)+':'+os.environ['PATH'],SCOUT_DUAL_MOCK=str(self.root))

    def tearDown(self):self.temp.cleanup()

    def run_launcher(self,mode=None,**flags):
        result=subprocess.run(['bash',str(self.root/'scripts/capture_dual.sh')]+([mode] if mode else []),env={**self.env,**flags},capture_output=True,text=True,timeout=12)
        trace=(self.root/'trace').read_text()
        return result,trace

    def assert_restored(self):
        for i in (0,1):
            self.assertEqual((self.root/str(i)/'mode').read_text(),'managed')
            self.assertTrue((self.root/str(i)/'connected').exists())

    def test_concurrent_bounded_captures_restore_both_after_all_exits(self):
        result,trace=self.run_launcher()
        self.assertEqual(result.returncode,0,result.stderr)
        self.assert_restored()
        self.assertEqual(trace.count('capture-start '),2)
        self.assertLess(trace.rindex('capture-start '),trace.index('capture-exit '))
        self.assertLess(trace.rindex('capture-exit '),trace.index('set type managed'))
        self.assertIn('set channel 6',trace);self.assertIn('set channel 149',trace)
        self.assertEqual(trace.count('sudo -n timeout --signal=INT --kill-after=3s 60s tcpdump'),2)
        self.assertNotIn('ip link set wlp3s0',trace)
        folder=next(p for p in (self.root/'local-data').iterdir() if p.is_dir())
        self.assertEqual(folder.stat().st_mode&0o777,0o700)
        for name in ('panda.pcap','techkey.pcap'):
            self.assertEqual((folder/name).stat().st_mode&0o777,0o600)
        self.assertEqual((folder/'test.txt').read_text().count('capture exit: 124'),2)
        for label in ('panda','techkey'):
            self.assertTrue((folder/f'{label}-prepared-radio.txt').exists())
            self.assertTrue((folder/f'{label}-final-radio.txt').exists())

    def test_preflight_rejects_before_any_radio_changes(self):
        for flag in ('BAD_DRIVER','NO_UPLINK','NO_AUTH','RESTRICTED','SAME_PHY'):
            with self.subTest(flag=flag):
                (self.root/'trace').unlink(missing_ok=True)
                result,trace=self.run_launcher(**{flag:'1'})
                self.assertNotEqual(result.returncode,0)
                self.assertNotIn('device disconnect',trace)
                self.assertNotIn('set type',trace)
                self.assertNotIn('set channel',trace)

    def test_second_radio_preparation_failure_restores_both_without_capture(self):
        for flag in ('RETUNE_FAIL','STUCK'):
            with self.subTest(flag=flag):
                result,trace=self.run_launcher(**{flag:'1'})
                self.assertNotEqual(result.returncode,0)
                self.assertNotIn('capture-start',trace)
                self.assert_restored()
                (self.root/'trace').unlink(missing_ok=True)

    def test_failed_capture_cancels_peer_before_any_restoration(self):
        result,trace=self.run_launcher(CAPTURE_FAIL='1')
        self.assertNotEqual(result.returncode,0)
        self.assertEqual(trace.count('capture-exit '),2)
        self.assertLess(trace.rindex('capture-exit '),trace.index('set type managed'))
        self.assert_restored()

    def test_live_preflight_and_app_before_radio_restoration(self):
        result,trace=self.run_launcher('--live')
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertIn('-m aetherlink_scout --combined',trace)
        self.assertNotIn('capture-start',trace)
        self.assertLess(trace.index('app-stop'),trace.index('set type managed'))
        self.assert_restored()

    def test_live_missing_bluetooth_or_busy_port_leaves_both_radios_unchanged(self):
        for flag in ('NO_BLUETOOTH','PORT_BUSY'):
            with self.subTest(flag=flag):
                (self.root/'trace').unlink(missing_ok=True)
                result,trace=self.run_launcher('--live',**{flag:'1'})
                self.assertNotEqual(result.returncode,0)
                self.assertNotIn('device disconnect',trace)
                self.assertNotIn('set type',trace)

    def test_abnormal_app_exit_does_not_restore_during_possible_capture(self):
        result,trace=self.run_launcher('--live',APP_FAIL='1')
        self.assertNotEqual(result.returncode,0)
        self.assertIn('Neither radio was restored',result.stderr)
        self.assertNotIn('set type managed',trace)

    def test_optional_tak_profile_validates_before_radio_changes(self):
        result=subprocess.run(['bash',str(self.root/'scripts/capture_dual.sh'),'--live-tak'],env={**self.env,'BAD_TAK_CONFIG':'1'},capture_output=True,text=True)
        self.assertNotEqual(result.returncode,0)
        trace=(self.root/'trace').read_text()
        self.assertIn('aetherlink_scout.tak --validate-config',trace)
        self.assertNotIn('device disconnect',trace)
        self.assertNotIn('set type monitor',trace)

    def test_optional_tak_profile_preserves_fixed_sources_and_restoration(self):
        result=subprocess.run(['bash',str(self.root/'scripts/capture_dual.sh'),'--live-tak'],env=self.env,capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)
        trace=(self.root/'trace').read_text()
        self.assertIn('--combined --tak-config',trace)
        self.assertIn('set channel 6',trace)
        self.assertIn('set channel 149',trace)
        self.assertLess(trace.index('app-stop'),trace.index('set type managed'))
        self.assert_restored()

    def test_explicit_recovery_and_autostart_flags_reach_combined_app(self):
        result=subprocess.run(['bash',str(self.root/'scripts/capture_dual.sh'),'--live-tak','--recover','--autostart'],env=self.env,capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)
        trace=(self.root/'trace').read_text()
        self.assertIn('--recover-radios --autostart',trace)
        self.assertLess(trace.index('app-stop'),trace.index('set type managed'))
        self.assert_restored()

    def test_autostart_without_recovery_is_rejected_before_network_changes(self):
        result=subprocess.run(['bash',str(self.root/'scripts/capture_dual.sh'),'--live','--autostart'],env=self.env,capture_output=True,text=True)
        self.assertNotEqual(result.returncode,0)
        self.assertIn('Autostart requires --recover',result.stderr)
        self.assertFalse((self.root/'trace').exists())

    def test_live_signal_waits_for_app_before_restoring_both_radios(self):
        process=subprocess.Popen(['bash',str(self.root/'scripts/capture_dual.sh'),'--live'],env={**self.env,'APP_HOLD':'1'},stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,start_new_session=True)
        try:
            deadline=time.monotonic()+5
            while not (self.root/'trace').exists() or 'app-start' not in (self.root/'trace').read_text():
                if time.monotonic()>deadline:self.fail('Mock app did not start')
                time.sleep(.02)
            process.send_signal(signal.SIGTERM)
            self.assertEqual(process.wait(timeout=10),143)
            trace=(self.root/'trace').read_text()
            self.assertLess(trace.index('app-stop'),trace.index('set type managed'))
            self.assert_restored()
        finally:
            if process.poll() is None:
                os.killpg(process.pid,signal.SIGTERM);process.wait(timeout=10)

    def test_signal_cancels_both_captures_before_restoration(self):
        process=subprocess.Popen(['bash',str(self.root/'scripts/capture_dual.sh')],env={**self.env,'HOLD':'1'},stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,start_new_session=True)
        try:
            deadline=time.monotonic()+5
            while not (self.root/'trace').exists() or (self.root/'trace').read_text().count('capture-start ')<2:
                if time.monotonic()>deadline:self.fail('Captures did not start')
                time.sleep(.02)
            process.send_signal(signal.SIGTERM)
            self.assertEqual(process.wait(timeout=10),143)
            trace=(self.root/'trace').read_text()
            self.assertEqual(trace.count('capture-exit '),2)
            self.assertLess(trace.rindex('capture-exit '),trace.index('set type managed'))
            self.assert_restored()
        finally:
            if process.poll() is None:
                os.killpg(process.pid,signal.SIGTERM);process.wait(timeout=10)


if __name__=='__main__':unittest.main()
