"""Terminal-selected recovery of the two pinned dedicated USB Wi-Fi radios."""
from pathlib import Path
import re
import subprocess
import threading
import time
from .capture import CaptureError, LiveCapture


class RadioAbsent(CaptureError):
    """Expected USB device is absent or has been replaced by a new generation."""


class RecoveryCancelled(CaptureError):
    pass


class RadioRecovery:
    def __init__(self, interface, address, driver, channel, frequency, *, sysnet=Path('/sys/class/net'), run=subprocess.run):
        if (interface not in ('wlx9cefd5f67d4c','wlx1cbfce038535')
                or not re.fullmatch(r'(?:[0-9a-f]{2}:){5}[0-9a-f]{2}',address)):
            raise CaptureError('Recovery requires an inspected dedicated radio profile.')
        self.interface,self.address,self.driver=interface,address,driver
        self.channel,self.frequency=channel,frequency
        self.sysnet,self.run=Path(sysnet),run
        self.lock=threading.Lock()
        self.last_check=0

    def generation(self):
        root=self.sysnet/self.interface
        try:
            address=(root/'address').read_text().strip().lower()
            driver=(root/'device/driver').resolve(strict=True).name
            index=int((root/'ifindex').read_text())
        except (FileNotFoundError,NotADirectoryError):
            raise RadioAbsent('Dedicated USB radio absent; waiting for the same adapter.') from None
        except (OSError,ValueError):
            raise CaptureError('Cannot verify dedicated USB radio identity; recovery stopped.') from None
        if address!=self.address or driver!=self.driver:
            raise CaptureError('USB radio identity/driver differs from the approved profile; recovery stopped.')
        if not (root/'device').resolve().name.endswith(':1.0'):
            raise CaptureError('Recovery requires the inspected USB device interface.')
        return index

    def capture(self, interface):
        if interface!=self.interface:
            raise CaptureError('Recovery interface differs from its pinned profile.')
        capture=LiveCapture(interface,channel=self.channel,frequency=self.frequency,driver=self.driver)
        capture.recovery_generation=self.generation()
        return capture

    def check_capture(self,capture):
        # At most one sysfs read per second during idle packet polling.
        if time.monotonic()-self.last_check<1:return
        self.last_check=time.monotonic()
        if self.generation()!=capture.recovery_generation:
            raise RadioAbsent('Dedicated USB radio reappeared; restoring its approved capture profile.')

    def disconnected(self,capture):
        try:
            return self.generation()!=capture.recovery_generation
        except RadioAbsent:
            return True

    def command(self,args,stop,*,privileged=False):
        if stop.is_set():raise RecoveryCancelled('USB recovery cancelled.')
        command=(['sudo','-n','timeout','--signal=TERM','--kill-after=1s','4s'] if privileged else [])+args
        try:
            return self.run(command,capture_output=True,text=True,check=True,timeout=7 if privileged else 3).stdout
        except (OSError,subprocess.SubprocessError):
            if stop.is_set():raise RecoveryCancelled('USB recovery cancelled.') from None
            # A second physical unplug is retryable; an ordinary command error is not.
            self.generation()
            raise CaptureError('USB recovery command/authorization failed; inspect the launch terminal. No automatic retry of this error.') from None

    def prepare(self,stop):
        """Each mutation is bounded and cancelled between commands; no uplink changes."""
        with self.lock:
            generation=self.generation()
            def same_device():
                if stop.is_set():raise RecoveryCancelled('USB recovery cancelled.')
                if self.generation()!=generation:raise RadioAbsent('USB radio changed during recovery; retrying verification.')
            routes=self.command(['ip','route','show','default'],stop)
            if not re.search(r'\bdev wlp3s0\b',routes) or re.search(rf'\bdev {re.escape(self.interface)}\b',routes):
                raise CaptureError('Recovery requires Intel uplink and a dedicated radio carrying no default route.')
            info=self.command(['iw','dev',self.interface,'info'],stop)
            phy=re.search(r'^\s*wiphy (\d+)\s*$',info,re.M)
            if not phy:raise CaptureError('Cannot verify recovery PHY.')
            siblings=self.command(['iw','dev'],stop)
            block=re.search(rf'^phy#{phy[1]}\n(.*?)(?=^phy#|\Z)',siblings,re.M|re.S)
            if not block or re.findall(r'^\s*Interface (\S+)',block[1],re.M)!=[self.interface]:
                raise CaptureError('Recovery PHY has another interface; automatic reconfiguration refused.')
            capabilities=self.command(['iw','phy','phy'+phy[1],'info'],stop)
            if not re.search(r'^\s*\* monitor\s*$',capabilities,re.M):raise CaptureError('Monitor capability unavailable; recovery stopped.')
            targets=[(self.channel,self.frequency)]+([(1,2412)] if self.channel==6 else [])
            for channel,frequency in targets:
                line=re.search(rf'^\s*\* {frequency}(?:\.0+)? MHz \[{channel}\]([^\n]*)$',capabilities,re.M)
                if not line or re.search(r'disabled|radar|no[ -]IR',line[1],re.I):raise CaptureError('Recovery channel is unavailable/restricted.')
            # Require cached authorization. Never prompt from the browser or grant Python privileges.
            self.command(['true'],stop,privileged=True)
            commands=[['nmcli','device','set',self.interface,'managed','no'],
                      ['ip','link','set',self.interface,'down'],
                      ['iw','dev',self.interface,'set','type','monitor'],
                      ['ip','link','set',self.interface,'up']]
            if self.channel==6:commands.append(['iw','dev',self.interface,'set','channel','1'])
            commands.append(['iw','dev',self.interface,'set','channel',str(self.channel)])
            for command in commands:
                same_device();self.command(command,stop,privileged=True)
            same_device()
            info=self.command(['iw','dev',self.interface,'info'],stop)
            if not re.search(r'^\s*type monitor\s*$',info,re.M) or not re.search(rf'^\s*channel {self.channel} \({self.frequency}(?:\.0+)? MHz\)',info,re.M):
                raise CaptureError('Returned radio did not confirm its assigned monitor channel; recovery stopped.')
