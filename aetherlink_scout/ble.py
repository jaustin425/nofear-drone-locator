"""Legacy BLE source with bounded unprivileged BlueZ discovery and shared UI."""
from contextlib import redirect_stderr, redirect_stdout
import io
import json
import math
import os
from pathlib import Path
import re
import subprocess
import tempfile

from dronecot.ble_parse import parse_odid_service_data
from dronecot.rid_normalize import bytes_to_rid_dict
from .capture import LiveCapture, CaptureError, CAPTURE_SEGMENT_SECONDS
from .receiver import Receiver, PcapStream, InputStopped
from .replay import report
from .decode_lock import DECODE_LOCK

MAX_LINE = 16384


def decode_advertisement(value):
    """BlueZ ServiceData excludes its 16-bit UUID; metadata is source-specific."""
    stamp = value.get('timestamp')
    address = value.get('address')
    raw = value.get('service_data')
    if (isinstance(stamp, bool) or not isinstance(stamp, (int, float)) or not math.isfinite(stamp)
            or not isinstance(address, str) or not re.fullmatch(r'(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}', address)
            or not isinstance(raw, str) or len(raw) > 8192):
        raise ValueError('Invalid BLE event')
    payload = parse_odid_service_data(bytes.fromhex('faff'+raw))
    if payload is None:
        return None
    rssi = value.get('rssi')
    if isinstance(rssi, bool) or not isinstance(rssi, (int, float)) or not math.isfinite(rssi) or not -150 <= rssi <= 0:
        rssi = None
    with DECODE_LOCK, redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
        data = bytes_to_rid_dict(payload, {'type': 'BLE legacy (BlueZ D-Bus)', 'MAC address': address, 'RSSI': rssi})
        return report(data, stamp) if data else None


class BleCapture(LiveCapture):
    def __init__(self, interface):
        if not re.fullmatch(r'hci[0-9]{1,3}', interface):
            raise CaptureError('Choose a Bluetooth hci adapter.')
        self.interface = interface
        self.process = self.log = None

    def start(self):
        if os.geteuid() == 0:
            raise CaptureError('Run Scout as your normal user.')
        root = Path(__file__).resolve().parent.parent
        folder = root/'local-data'
        folder.mkdir(exist_ok=True)
        self.log = tempfile.NamedTemporaryFile(prefix='scout-ble-', suffix='.log', dir=folder, delete=False)
        command = ['timeout', '--signal=INT', '--kill-after=3s', f'{CAPTURE_SEGMENT_SECONDS}s',
                   '/usr/bin/python3', '-u', str(root/'scripts/ble_bluez.py'), '--adapter', self.interface]
        try:
            self.process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=self.log)
        except OSError:
            self.log.close()
            raise CaptureError('Unable to start the bounded Bluetooth reader.') from None
        return self.process.stdout.fileno()


class BleReceiver(Receiver):
    def __init__(self, *, adapter='hci0', capture_factory=BleCapture, **kwargs):
        super().__init__(live_interface=adapter, capture_factory=capture_factory, **kwargs)
        self.message = 'Ready · START discovers legacy BLE Remote ID on the powered adapter'
        self.service_updates = self.rid_candidates = 0

    def metadata(self):
        value = super().metadata()
        value['source'] = 'Bluetooth legacy Remote ID · BlueZ D-Bus'
        return value

    def status(self):
        value = super().status()
        value.update(ble_enabled=True, ble_adapter=self.live_interface, ble_reader='bluez-dbus',
                     ble_coverage='Legacy BLE only · service updates may coalesce',
                     expected_channel=None, expected_frequency_mhz=None,
                     ble_service_updates=getattr(self, 'service_updates', 0),
                     ble_rid_candidates=getattr(self, 'rid_candidates', 0))
        return value

    def _read_pcap(self):
        # Reuse cancellable bounded pipe reads; this source is JSONL, not PCAP.
        with self.lock:
            if self.capture_segments == 1:
                self.service_updates = self.rid_candidates = 0
            service_offset, candidate_offset = self.service_updates, self.rid_candidates
        stream = PcapStream(self.input_fd, self.stop)
        while not self.stop.is_set():
            line = bytearray()
            while True:
                byte = stream.read(1, eof_ok=True)
                if byte is None:
                    if line:
                        raise CaptureError('Bluetooth stream ended in a partial event; detection stopped.')
                    return
                if byte == b'\n':
                    break
                line.extend(byte)
                if len(line) > MAX_LINE:
                    raise CaptureError('Bluetooth event exceeded the input limit; detection stopped.')
            try:
                value = json.loads(line)
                if not isinstance(value, dict):
                    raise ValueError()
                kind = value.get('kind')
                if kind == 'error':
                    # Only fixed source messages are allowed through, never raw exception/payload text.
                    raise CaptureError('Bluetooth reader failed; check powered adapter, BlueZ permissions and the private launch log. Detection stopped.')
                if kind == 'ready':
                    self.set_state('running', 'Bluetooth discovery running · listening for legacy Remote ID')
                elif kind == 'status':
                    with self.lock:
                        for key, attribute, offset in (('service_data_updates', 'service_updates', service_offset), ('rid_candidates', 'rid_candidates', candidate_offset)):
                            number = value.get(key)
                            if type(number) is int and 0 <= number <= 2**53:
                                setattr(self, attribute, offset+number)
                elif kind == 'advertisement':
                    with self.lock:
                        self.counts['packets'] += 1
                        self.last_packet = self.elapsed()
                    item = decode_advertisement(value)
                    with self.lock:
                        if item is None:
                            self.counts['rejected'] += 1
                        else:
                            self.counts['decoded'] += 1
                    if item is not None:
                        self.publish(item)
                else:
                    raise ValueError()
            except CaptureError:
                raise
            except Exception:
                with self.lock:
                    self.counts['errors'] += 1
        raise InputStopped()
