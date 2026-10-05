"""Explicit capture on an already prepared radio. Never changes interface state."""
import os
from pathlib import Path
import pwd
import re
import signal
import subprocess
import tempfile

FILTER = '(wlan[0] & 0xfc = 0x80) or (wlan[0] & 0xfc = 0xd0)'
EXPECTED_CHANNEL = 6
EXPECTED_FREQUENCY = 2437
CAPTURE_SEGMENT_SECONDS = 120


class CaptureError(Exception):
    pass


class LiveCapture:
    def __init__(self, interface):
        if not re.fullmatch(r'[a-zA-Z0-9_.-]{1,15}', interface) or interface == 'wlp3s0':
            raise CaptureError('Choose the dedicated capture radio; Intel uplink is protected.')
        self.interface = interface
        self.process = self.log = None

    def start(self):
        if os.geteuid() == 0:
            raise CaptureError('Run the dashboard as your normal user.')
        try:
            info = subprocess.run(['iw', 'dev', self.interface, 'info'], capture_output=True, text=True, timeout=5, check=True)
            if not re.search(r'^\s*type monitor\s*$', info.stdout, re.M):
                raise CaptureError('Panda must be prepared in monitor mode in your terminal first.')
            channel = re.search(r'^\s*channel (\d+) \(([\d.]+) MHz\)', info.stdout, re.M)
            if not channel:
                raise CaptureError('Cannot verify Panda channel; capture was not started.')
            if int(channel[1]) != EXPECTED_CHANNEL or float(channel[2]) != EXPECTED_FREQUENCY:
                raise CaptureError(f'Panda is on channel {channel[1]} ({channel[2]} MHz); expected channel 6 (2437 MHz). Retune only Panda in the launch terminal before retrying.')
            routes = subprocess.run(['ip', 'route', 'show', 'default'], capture_output=True, text=True, timeout=5, check=True)
            if not re.search(r'\bdev wlp3s0\b', routes.stdout):
                raise CaptureError('Intel default route is missing; capture was not started.')
            subprocess.run(['sudo', '-n', 'true'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5, check=True)
        except (OSError, subprocess.SubprocessError):
            raise CaptureError('Capture prerequisites unavailable. Run sudo -v privately in the launch terminal; no interface changes made.') from None
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
