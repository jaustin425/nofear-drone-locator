"""Explicit capture on a prepared radio; optional, terminal-selected scanning."""
import os
from pathlib import Path
import pwd
import re
import signal
import subprocess
import tempfile
import threading
import time

FILTER = '(wlan[0] & 0xfc = 0x80) or (wlan[0] & 0xfc = 0xd0)'
EXPECTED_CHANNEL = 6
EXPECTED_FREQUENCY = 2437
CAPTURE_SEGMENT_SECONDS = 120
SCAN_PLAN = ((6, 2437, 3), (149, 5745, 1))


class CaptureError(Exception):
    pass


class LiveCapture:
    def __init__(self, interface):
        if not re.fullmatch(r'[a-zA-Z0-9_.-]{1,15}', interface) or interface == 'wlp3s0':
            raise CaptureError('Choose the dedicated capture radio; Intel uplink is protected.')
        self.interface = interface
        self.process = self.log = None

    def verify_channel(self, info):
        channel = re.search(r'^\s*channel (\d+) \(([\d.]+) MHz\)', info, re.M)
        if not channel:
            raise CaptureError('Cannot verify Panda channel; capture was not started.')
        if int(channel[1]) != EXPECTED_CHANNEL or float(channel[2]) != EXPECTED_FREQUENCY:
            raise CaptureError(f'Panda is on channel {channel[1]} ({channel[2]} MHz); expected channel 6 (2437 MHz). Retune only Panda in the launch terminal before retrying.')

    def prepare_capture(self):
        pass

    def start(self):
        if os.geteuid() == 0:
            raise CaptureError('Run the dashboard as your normal user.')
        try:
            info = subprocess.run(['iw', 'dev', self.interface, 'info'], capture_output=True, text=True, timeout=5, check=True)
            if not re.search(r'^\s*type monitor\s*$', info.stdout, re.M):
                raise CaptureError('Panda must be prepared in monitor mode in your terminal first.')
            self.verify_channel(info.stdout)
            routes = subprocess.run(['ip', 'route', 'show', 'default'], capture_output=True, text=True, timeout=5, check=True)
            if not re.search(r'\bdev wlp3s0\b', routes.stdout):
                raise CaptureError('Intel default route is missing; capture was not started.')
            subprocess.run(['sudo', '-n', 'true'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5, check=True)
        except (OSError, subprocess.SubprocessError):
            raise CaptureError('Capture prerequisites unavailable. Run sudo -v privately in the launch terminal; no interface changes made.') from None
        self.prepare_capture()
        folder = Path(__file__).resolve().parent.parent/'local-data'
        folder.mkdir(exist_ok=True)
        self.log = tempfile.NamedTemporaryFile(prefix='scout-capture-', suffix='.log', dir=folder, delete=False)
        user = pwd.getpwuid(os.getuid()).pw_name
        # Sudo forwards SIGINT to its command. timeout is a second bound if an
        # explicit stop cannot complete. tcpdump drops capture privileges to user.
        command = ['sudo', '-n', 'timeout', '--signal=INT', '--kill-after=3s', f'{CAPTURE_SEGMENT_SECONDS}s',
                   'tcpdump', '-i', self.interface, '-nn', '-U', '-s', '0', '-w', '-', '-Z', user, FILTER]
        try:
            self.process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=self.log)
        except OSError:
            self.log.close()
            raise CaptureError('Unable to start capture; see private terminal prerequisites.') from None
        return self.process.stdout.fileno()

    def stop(self):
        if self.process is None:
            return
        try:
            if self.process.poll() is None:
                self.process.send_signal(signal.SIGINT)
                self.process.wait(timeout=5)
        except (OSError, subprocess.TimeoutExpired):
            raise CaptureError('Capture stop was not confirmed; the 120-second capture limit remains in effect. Check the launch terminal.') from None
        finally:
            if self.process.poll() is not None:
                self.process.stdout.close()
                self.log.close()

    def outcome(self):
        if self.process is None:
            return None
        # Pipe EOF can arrive just before timeout/sudo has reaped its child.
        # Wait for a confirmed exit code before deciding whether to renew.
        try:
            return self.process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            raise CaptureError('Capture output ended before process exit could be confirmed; detection was not renewed.') from None


class ScanCapture(LiveCapture):
    """One continuous tcpdump, with cancellable, verified Panda-only retunes.

    Python remains unprivileged. Only explicitly selected scan mode uses sudo iw;
    each command and each capture has an independent time bound.
    """
    def __init__(self, interface):
        super().__init__(interface)
        if interface != 'wlx9cefd5f67d4c':
            raise CaptureError('The scan profile is restricted to the inspected Panda interface.')
        self.cancel = threading.Event()
        self.worker = None
        self.failure = None
        self.verified = None

    def verify_channel(self, info):
        if Path(f'/sys/class/net/{self.interface}/device/driver').resolve().name != 'mt76x0u':
            raise CaptureError('Scan requires the inspected Panda mt76x0u driver.')
        phy = re.search(r'^\s*wiphy (\d+)\s*$', info, re.M)
        if not phy:
            raise CaptureError('Cannot discover Panda PHY; scanning was not started.')
        capabilities = subprocess.run(['iw', 'phy', f'phy{phy[1]}', 'info'], capture_output=True, text=True, check=True, timeout=5).stdout
        for channel, frequency, _ in SCAN_PLAN:
            line = re.search(rf'^\s*\* {frequency}(?:\.0+)? MHz \[{channel}\]([^\n]*)$', capabilities, re.M)
            if not line or re.search(r'disabled|radar|no[ -]IR', line[1], re.I):
                raise CaptureError(f'Channel {channel} is unavailable or restricted; scanning was not started.')

    def retune(self, channel, frequency):
        try:
            routes = subprocess.run(['ip', 'route', 'show', 'default'], capture_output=True, text=True, check=True, timeout=2).stdout
            if not re.search(r'\bdev wlp3s0\b', routes):
                raise CaptureError('Intel default route is missing; scanning stopped.')
            if self.cancel.is_set():
                return
            subprocess.run(['sudo', '-n', 'timeout', '--signal=TERM', '--kill-after=1s', '2s',
                            'iw', 'dev', self.interface, 'set', 'channel', str(channel)],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True, timeout=4)
            info = subprocess.run(['iw', 'dev', self.interface, 'info'], capture_output=True, text=True, check=True, timeout=2).stdout
            if (not re.search(r'^\s*type monitor\s*$', info, re.M)
                    or not re.search(rf'^\s*channel {channel} \({frequency}(?:\.0+)? MHz\)', info, re.M)):
                raise CaptureError(f'Panda did not confirm channel {channel}; scanning stopped. Check the launch terminal.')
            self.verified = (channel, frequency, time.monotonic())
        except (OSError, subprocess.SubprocessError):
            raise CaptureError('Scan retune failed or authorization expired; detection stopped. Check Panda in the launch terminal.') from None

    def prepare_capture(self):
        self.retune(*SCAN_PLAN[0][:2])

    def start(self):
        fd = super().start()
        # Keep the app alive if a retune has not exited: the launcher must not
        # restore managed mode while a command could still change the channel.
        self.worker = threading.Thread(target=self.scan, name='scout-radio-scan', daemon=False)
        self.worker.start()
        return fd

    def scan(self):
        deadline = time.monotonic() + CAPTURE_SEGMENT_SECONDS
        index = 0
        try:
            while not self.cancel.is_set():
                until = time.monotonic() + SCAN_PLAN[index][2]
                while time.monotonic() < until:
                    if self.cancel.wait(min(.1, max(0, until-time.monotonic()))):
                        return
                    if self.process.poll() is not None or time.monotonic() >= deadline:
                        return
                if self.cancel.is_set() or self.process.poll() is not None or time.monotonic() >= deadline:
                    return
                index = (index+1) % len(SCAN_PLAN)
                self.retune(*SCAN_PLAN[index][:2])
        except Exception as error:
            self.failure = str(error) if isinstance(error, CaptureError) else 'Radio scanning failed; detection stopped.'
            # EOF wakes the receiver, which surfaces failure rather than renewing.
            try:
                if self.process.poll() is None:
                    self.process.send_signal(signal.SIGINT)
            except OSError:
                pass

    def cancel_scan(self):
        self.cancel.set()
        if self.worker:
            self.worker.join(timeout=9)
            if self.worker.is_alive():
                raise CaptureError('Radio scan shutdown is pending; retry STOP before restoring Panda.')

    def stop(self):
        try:
            self.cancel_scan()
        finally:
            super().stop()

    def outcome(self):
        result = super().outcome()
        self.cancel_scan()
        if self.failure:
            raise CaptureError(self.failure)
        return result
