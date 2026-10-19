"""Techkey preparation/restoration with fake commands; no actual radio access."""
import os
from pathlib import Path
import shutil
import signal
import subprocess
import time
import unittest

import test_launcher as launcher_fixture


@unittest.skipIf(os.geteuid()==0, 'Launcher intentionally refuses root')
class TechkeyTests(unittest.TestCase):
    def setUp(self):
        launcher_fixture.LauncherTests.setUp(self)
        source=Path(__file__).resolve().parent.parent/'scripts/capture_techkey.sh'
        shutil.copyfile(source,self.root/'scripts/capture_techkey.sh')
        mock=launcher_fixture.MOCK.replace('mt76x0u','rtl88x2bu').replace('wlx9cefd5f67d4c','wlx1cbfce038535')
        mock=mock.replace("if 'GENERAL.STATE' in args:", "if 'GENERAL.NM-MANAGED' in args: print('yes')\n    elif 'GENERAL.STATE' in args:")
        mock=mock.replace("if args[-1]=='info':", "if args[:1]==['phy']:\n        restricted=' (disabled)' if os.environ.get('MOCK_RESTRICTED') else ''\n        print(' * monitor\\n * 2437.0 MHz [6] (16 dBm)'+restricted+'\\n * 5220.0 MHz [44] (15 dBm)'+restricted)\n    elif args[-1]=='info':")
        mock=mock.replace("print('Interface wlx1cbfce038535\\n\\ttype '","print('Interface wlx1cbfce038535\\n\\twiphy 2\\n\\ttype '")
        (self.root/'mocks/mock').write_text(mock)

    tearDown=launcher_fixture.LauncherTests.tearDown

    def run_test(self, mode=None, **flags):
        command=['bash',str(self.root/'scripts/capture_techkey.sh')]+([mode] if mode else [])
        result=subprocess.run(command,env={**self.env,**flags},capture_output=True,text=True,timeout=10)
        return result,(self.root/'trace').read_text()

    def test_capture_only_techkey_and_restore_original_connection(self):
        result,trace=self.run_test()
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertIn('set type monitor',trace)
        self.assertIn('set channel 6',trace)
        self.assertIn('timeout --signal=INT --kill-after=3s 60s tcpdump -i wlx1cbfce038535',trace)
        self.assertIn('connection up uuid',trace)
        self.assertEqual((self.root/'mode').read_text(),'managed')
        self.assertTrue((self.root/'connected').exists())
        self.assertNotIn('wlx9cefd5f67d4c',trace)
        self.assertNotIn('ip link set wlp3s0',trace)
        self.assertNotIn('set channel 149',trace)
        folder=next((self.root/'local-data').iterdir())
        self.assertEqual(folder.stat().st_mode & 0o777,0o700)
        self.assertEqual((folder/'capture.pcap').stat().st_mode & 0o777,0o600)

    def test_stuck_or_failed_channel_restores_without_capture(self):
        for flag in ('MOCK_STUCK_CHANNEL','MOCK_CHANNEL_FAIL'):
            with self.subTest(flag=flag):
                (self.root/'mode').write_text('managed');(self.root/'channel').write_text('44')
                (self.root/'trace').unlink(missing_ok=True)
                result,trace=self.run_test(**{flag:'1'})
                self.assertNotEqual(result.returncode,0)
                self.assertNotIn('60s tcpdump',trace)
                self.assertIn('set type managed',trace)
                self.assertTrue((self.root/'connected').exists())

    def test_diagnostic_is_unfiltered_truncated_and_bounded_on_channel_44(self):
        result,trace=self.run_test('--diagnostic')
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertIn('set channel 44',trace)
        capture=next(line for line in trace.splitlines() if line.startswith('sudo -n timeout'))
        self.assertIn('--kill-after=3s 20s tcpdump',capture)
        self.assertIn('-s 96',capture)
        self.assertIn('-c 10000',capture)
        self.assertNotIn('wlan[',capture)
        self.assertNotIn('wlx9cefd5f67d4c',trace)
        self.assertNotIn('ip link set wlp3s0',trace)
        self.assertEqual((self.root/'mode').read_text(),'managed')
        self.assertTrue((self.root/'connected').exists())

    def test_diagnostic_restriction_or_failed_retune_does_not_capture(self):
        for flag in ('MOCK_RESTRICTED','MOCK_CHANNEL_FAIL','MOCK_STUCK_CHANNEL'):
            with self.subTest(flag=flag):
                (self.root/'mode').write_text('managed');(self.root/'channel').write_text('6')
                (self.root/'trace').unlink(missing_ok=True)
                result,trace=self.run_test('--diagnostic',**{flag:'1'})
                self.assertNotEqual(result.returncode,0)
                self.assertNotIn('20s tcpdump',trace)
                self.assertEqual((self.root/'mode').read_text(),'managed')

    def test_packet_limit_exit_is_success_only_in_diagnostic_mode(self):
        wrapper=self.root/'mocks/mock'
        wrapper.write_text(wrapper.read_text().replace("elif name=='timeout': sys.exit(124)", "elif name=='timeout': sys.exit(0)"))
        result,_=self.run_test('--diagnostic')
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertIn('packet limit reached',result.stdout)
        result,_=self.run_test()
        self.assertNotEqual(result.returncode,0)

    def test_preflight_failures_leave_network_unchanged(self):
        for flag in ('MOCK_NO_AUTH','MOCK_BAD_DRIVER','MOCK_NO_UPLINK','MOCK_RESTRICTED'):
            with self.subTest(flag=flag):
                (self.root/'trace').unlink(missing_ok=True)
                result,trace=self.run_test(**{flag:'1'})
                self.assertNotEqual(result.returncode,0)
                self.assertNotIn('device disconnect',trace)
                self.assertNotIn('set type',trace)
                self.assertNotIn('set channel',trace)

    def test_interruption_confirms_capture_exit_before_restoring_radio(self):
        wrapper=self.root/'mocks/mock'
        wrapper.write_text(wrapper.read_text().replace("elif name=='timeout': sys.exit(124)", """elif name=='timeout':
    def stopped(*_):
        trace('capture-exit')
        sys.exit(143)
    signal.signal(signal.SIGTERM,stopped)
    trace('capture-ready')
    while True: time.sleep(.05)
"""))
        process=subprocess.Popen(['bash',str(self.root/'scripts/capture_techkey.sh')],env=self.env,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,start_new_session=True)
        try:
            deadline=time.monotonic()+5
            while not (self.root/'trace').exists() or 'capture-ready' not in (self.root/'trace').read_text():
                if time.monotonic()>deadline:
                    self.fail('Mock capture did not start')
                time.sleep(.02)
            process.send_signal(signal.SIGTERM)
            self.assertEqual(process.wait(timeout=10),143)
            trace=(self.root/'trace').read_text()
            self.assertLess(trace.index('capture-exit'),trace.rindex('set type managed'))
            self.assertTrue((self.root/'connected').exists())
        finally:
            if process.poll() is None:
                os.killpg(process.pid,signal.SIGTERM)
                process.wait(timeout=10)


if __name__=='__main__': unittest.main()
