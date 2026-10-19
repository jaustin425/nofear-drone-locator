"""Bounded local state and cancellable input, with explicit live capture renewal."""
import copy
import json
import os
import select
import struct
import sqlite3
import threading
import time
import uuid
from collections import deque
from contextlib import redirect_stderr, redirect_stdout
import io

from scapy.layers.dot11 import Dot11, RadioTap
from dronecot.rid_normalize import bytes_to_rid_dict
from dronecot.wifi_parse import extract_odid_from_scapy_packet
from .replay import report
from .capture import CaptureError, LiveCapture, ScanCapture, SCAN_PLAN, EXPECTED_CHANNEL, EXPECTED_FREQUENCY, CAPTURE_SEGMENT_SECONDS
from .history import HistoryStore
from .decode_lock import DECODE_LOCK
from .health import receiver_health

MAX_TRACKS = 512
MAX_EVENTS = 2048
MAX_PACKET = 262144


class InputStopped(Exception):
    pass


class PacketDecodeError(Exception):
    pass


class PcapStream:
    """Classic PCAP only, bounded reads, including partial pipe writes and EOF.

    PCAPNG remains supported by ordinary replay/simulation. Live tcpdump uses
    classic PCAP. An idle pipe is polled so shutdown never waits for a packet.
    """
    def __init__(self, fd, stop, health_check=None):
        self.fd, self.stop = fd, stop
        self.health_check = health_check

    def read(self, count, eof_ok=False):
        data = bytearray()
        while len(data) < count:
            if self.stop.is_set():
                raise InputStopped()
            if self.health_check:
                self.health_check()
            ready, _, _ = select.select([self.fd], [], [], .1)
            if not ready:
                continue
            part = os.read(self.fd, count - len(data))
            if not part:
                if eof_ok and not data:
                    return None
                raise ValueError("Truncated capture stream")
            data.extend(part)
        return bytes(data)

    def header(self):
        magic = self.read(4)
        formats = {b'\xd4\xc3\xb2\xa1': ('<', 1e6), b'\xa1\xb2\xc3\xd4': ('>', 1e6),
                   b'\x4d\x3c\xb2\xa1': ('<', 1e9), b'\xa1\xb2\x3c\x4d': ('>', 1e9)}
        if magic not in formats:
            raise ValueError("Expected classic PCAP")
        self.endian, self.scale = formats[magic]
        major, minor, _, _, snaplen, linktype = struct.unpack(self.endian+'HHIIII', self.read(20))
        if (major, minor) != (2, 4) or not 0 < snaplen <= MAX_PACKET or linktype not in (105, 127):
            raise ValueError("Unsupported capture header")
        self.layer = RadioTap if linktype == 127 else Dot11

    def packet(self):
        header = self.read(16, eof_ok=True)
        if header is None:
            return None
        seconds, fraction, length, wire_length = struct.unpack(self.endian+'IIII', header)
        if not 0 < length <= MAX_PACKET or wire_length < length or fraction >= self.scale:
            raise ValueError("Invalid packet length/time")
        payload = self.read(length)
        with DECODE_LOCK, redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            try:
                packet = self.layer(payload)
            except Exception:
                raise PacketDecodeError() from None
        packet.time = seconds + fraction/self.scale
        return packet


class Receiver:
    def __init__(self, *, simulation=None, speed=1, input_fd=None, live_interface=None, stale_seconds=30, clock=time.monotonic, capture_factory=LiveCapture, history=None, scan=False, channel=EXPECTED_CHANNEL, frequency=EXPECTED_FREQUENCY, recovery=None):
        if scan and (not live_interface or simulation is not None):
            raise ValueError('Scanning requires explicit live-interface mode.')
        self.mode = 'stream_simulation' if simulation is not None else 'live_stream'
        self.simulation, self.speed, self.input_fd = simulation, speed, input_fd
        self.recovery = recovery
        self.capture_recoveries = self.recovery_attempts = 0
        self.scan = scan
        self.expected_channel, self.expected_frequency = channel, frequency
        self.worker_daemon = True
        self.live_interface = live_interface
        self.capture_factory = ScanCapture if scan and capture_factory is LiveCapture else capture_factory
        self.capture = None
        self.stop_unconfirmed = False
        self.clock = clock
        self.start = clock()
        self.start_timestamp = time.time()
        self.session_id = uuid.uuid4().hex
        self.stale_seconds = stale_seconds
        self.lock = threading.RLock()
        self.control = threading.RLock()
        self.capture_control = threading.Lock()
        self.stop = threading.Event()
        self.thread = None
        self.state = 'stopped'
        self.message = 'Ready · press START to simulate recorded input' if simulation is not None else 'Ready · prepare Panda in your terminal, then press START'
        self.counts = dict(packets=0, decoded=0, errors=0, rejected=0, unidentified=0)
        self.last_packet = self.last_report = None
        self.sequence = 0
        self.events = deque(maxlen=MAX_EVENTS)
        self.tracks = {}
        self.evicted_tracks = 0
        self.frequencies = {}
        self.last_frequency = None
        self.capture_segments = 0
        self.history = history if history is not None else HistoryStore()
        self.history_saved = {}
        self.history_error = False
        self.tak_output = None
        self.map_revision = 0
        self.worker_heartbeat = None

    def elapsed(self):
        return max(0, self.clock()-self.start)

    def heartbeat(self):
        self.worker_heartbeat = self.clock()
        if self.recovery:
            self.recovery.check_capture(self.capture)

    def set_state(self, state, message):
        with self.lock:
            self.state, self.message = state, message

    def status(self):
        with self.lock:
            now = self.elapsed()
            process = getattr(self.capture, 'process', None)
            process_alive = process.poll() is None if process is not None else None
            value = {'mode': self.mode, 'session_id': self.session_id, 'state': self.state,
                    'message': self.message, 'elapsed': now,
                    'worker_heartbeat_age': None if self.worker_heartbeat is None else max(0,self.clock()-self.worker_heartbeat),
                    'capture_process_alive': process_alive, 'stop_unconfirmed': self.stop_unconfirmed, 'counts': dict(self.counts),
                    'last_packet_age': None if self.last_packet is None else max(0, now-self.last_packet),
                    'last_report_age': None if self.last_report is None else max(0, now-self.last_report),
                    'retained_tracks': len(self.tracks), 'evicted_tracks': self.evicted_tracks,
                    'detection_active': bool(self.stop_unconfirmed or self.thread and self.thread.is_alive()),
                    'tak_enabled': bool(self.tak_output and not self.tak_output.local_output),
                    'tak_output': self.tak_output.status() if self.tak_output else None, 'capture_managed_by_app': self.live_interface is not None,
                    'continuous_capture': bool(self.live_interface),
                    'capture_segment_seconds': CAPTURE_SEGMENT_SECONDS if self.live_interface else None,
                    'capture_segments': self.capture_segments,
                    'recovery_enabled': self.recovery is not None,
                    'capture_recoveries': self.capture_recoveries, 'recovery_attempts': self.recovery_attempts,
                    'map_revision': self.map_revision,
                    'history_error': self.history_error,
                    'scan_enabled': self.scan,
                    'channel_plan': [{'channel': c, 'frequency_mhz': f, 'dwell_seconds': d} for c, f, d in SCAN_PLAN] if self.scan else [],
                    'last_verified_radio': self._radio_status(),
                    'expected_channel': self.expected_channel if self.live_interface and not self.scan else None,
                    'expected_frequency_mhz': self.expected_frequency if self.live_interface and not self.scan else None,
                    'received_frequencies_mhz': sorted(self.frequencies),
                    'last_frequency_mhz': self.last_frequency}
            value['health']=receiver_health(value)
            return value

    def _radio_status(self):
        verified = getattr(self.capture, 'verified', None)
        if not verified:
            return None
        channel, frequency, checked = verified
        return {'channel': channel, 'frequency_mhz': frequency, 'age_seconds': max(0, time.monotonic()-checked)}


    def metadata(self):
        return {'mode': self.mode, 'source': 'Recorded input · timed simulation' if self.simulation is not None else 'Panda Wi-Fi receiver',
                'start_timestamp': self.start_timestamp, 'stale_seconds': self.stale_seconds,
                'duration': 0, 'tail_seconds': 0, 'records': [], 'tak_enabled': bool(self.tak_output and not self.tak_output.local_output)}

    def publish(self, item):
        with self.lock:
            now = self.elapsed()
            self.last_report = now
            self.sequence += 1
            item = {**item, 'source_timestamp': item['timestamp'], 't': now, 'sequence': self.sequence}
            self.events.append(item)
            key = item['key']
            track = self.tracks.get(key)
            if track is None:
                if len(self.tracks) >= MAX_TRACKS:
                    oldest = min(self.tracks, key=lambda k: self.tracks[k]['lastSeen'])
                    self._save_history([self.tracks[oldest]])
                    del self.tracks[oldest]
                    self.history_saved.pop(oldest, None)
                    self.evicted_tracks += 1
                track = {'key': key, 'firstSeen': now, 'lastSeen': now, 'fields': {}, 'fieldTimes': {}, 'trail': []}
                self.tracks[key] = track
            track['lastSeen'] = now
            for field, value in item.items():
                if field in ('t', 'timestamp', 'key', 'sequence') or value is None:
                    continue
                if field in ('aircraft', 'operator'):
                    previous = track['fields'].get(field, {})
                    merged = {**previous, 'lat': value['lat'], 'lon': value['lon']}
                    if value['altitude'] is not None:
                        merged['altitude'] = value['altitude']
                        track['fieldTimes'][field+'_altitude'] = now
                    track['fields'][field] = merged
                else:
                    track['fields'][field] = value
                track['fieldTimes'][field] = now
            if item['aircraft']:
                point = [item['aircraft']['lat'], item['aircraft']['lon']]
                if not track['trail'] or track['trail'][-1] != point:
                    track['trail'].append(point)
                    del track['trail'][:-500]
            if key not in self.history_saved or now-self.history_saved[key] >= 5:
                self._save_history([track])
                self.history_saved[key] = now

    def _save_history(self, tracks):
        # Called with the state lock held. Coalesce updates to once per 5s per
        # track; explicit stop, clear, eviction and shutdown flush the latest.
        try:
            self.history.save(self.session_id, self.start_timestamp, self.mode, tracks)
            self.history_error = False
        except (OSError, ValueError, sqlite3.Error):
            self.history_error = True

    def clear_map(self, only_stale=True):
        with self.lock:
            now = self.elapsed()
            keys = [key for key, track in self.tracks.items()
                    if not only_stale or now-track['lastSeen'] >= self.stale_seconds]
            self._save_history([self.tracks[key] for key in keys])
            if self.history_error:
                raise CaptureError('History could not be saved; map was left unchanged.')
            for key in keys:
                del self.tracks[key]
                self.history_saved.pop(key, None)
            if keys:
                self.events.clear()
                self.sequence += 1
                self.map_revision += 1
            value = self.snapshot()
            value['cleared_tracks'] = len(keys)
            return value

    def snapshot(self):
        with self.lock:
            return {'kind': 'snapshot', 'metadata': self.metadata(), 'status': self.status(),
                    'sequence': self.sequence, 'tracks': copy.deepcopy(list(self.tracks.values()))}

    def update(self, cursor):
        with self.lock:
            if cursor > self.sequence or self.events and cursor < self.events[0]['sequence']-1:
                value = self.snapshot()
                value['resynchronized'] = True
                return value
            return {'kind': 'update', 'status': self.status(), 'sequence': self.sequence,
                    'records': [copy.deepcopy(r) for r in self.events if r['sequence'] > cursor]}

    def begin(self):
        # Lifespan does not start detection. START is explicit in the dashboard.
        pass

    def start_detection(self):
        with self.control:
            if self.thread and self.thread.is_alive():
                return self.status()
            if self.capture:
                try:
                    with self.capture_control:
                        self.capture.stop()
                except CaptureError as error:
                    self.stop_unconfirmed = True
                    self.set_state('error', str(error))
                    raise
            self.stop_unconfirmed = False
            if self.live_interface:
                try:
                    with self.capture_control:
                        self.capture = self.capture_factory(self.live_interface)
                        self.input_fd = self.capture.start()
                except CaptureError as error:
                    self.set_state('error', str(error))
                    raise
            self.stop_unconfirmed = False
            with self.lock:
                self._save_history(list(self.tracks.values()))
                self.stop = threading.Event()
                self.start = self.clock()
                self.start_timestamp = time.time()
                self.session_id = uuid.uuid4().hex
                self.counts = dict(packets=0, decoded=0, errors=0, rejected=0, unidentified=0)
                self.last_packet = self.last_report = None
                self.events.clear()
                self.tracks.clear()
                self.sequence = self.evicted_tracks = 0
                self.frequencies.clear()
                self.last_frequency = None
                self.capture_segments = 1 if self.live_interface else 0
                self.capture_recoveries = self.recovery_attempts = 0
                self.history_saved.clear()
                self.map_revision = 0
                self.worker_heartbeat = None
                self.state = 'waiting'
                self.message = 'Starting timed recording simulation' if self.simulation is not None else 'Starting capture · waiting for input'
            self.thread = threading.Thread(target=self._run, name='scout-input', daemon=self.worker_daemon)
            self.thread.start()
            return self.status()

    def stop_detection(self):
        with self.control:
            # Cancel first: a renewal waiting for capture_control must not spawn
            # another process, and an in-flight start will be stopped afterward.
            self.stop.set()
            if self.capture:
                try:
                    with self.capture_control:
                        self.capture.stop()
                except CaptureError as error:
                    self.stop_unconfirmed = True
                    self.set_state('error', str(error))
                    raise
            self.stop_unconfirmed = False
            if self.thread:
                self.thread.join(timeout=2)
                if self.thread.is_alive():
                    self.stop_unconfirmed = True
                    self.set_state('error', 'Detection shutdown is still pending; retry STOP in the dashboard.')
                    raise CaptureError(self.message)
            self.set_state('stopped', 'Detection stopped · retained positions continue aging')
            with self.lock:
                self._save_history(list(self.tracks.values()))
            return self.status()

    def close(self):
        try:
            self.stop_detection()
        except CaptureError:
            self.stop.set()

    def _run(self):
        try:
            if self.simulation is not None:
                self._simulate()
            elif self.live_interface:
                self._live()
            else:
                self._read_pcap()
                self.set_state('stopped', 'Capture ended · retained positions continue aging')
        except InputStopped:
            self.set_state('stopped', 'Input stopped')
        except CaptureError as error:
            self.set_state('error', str(error))
        except Exception:
            # Never expose decoder exceptions (which may contain payload bytes).
            self.set_state('error', 'Receiver input failed; check capture setup in the launch terminal')
        finally:
            with self.lock:
                self._save_history(list(self.tracks.values()))
            if self.capture:
                try:
                    with self.capture_control:
                        self.capture.stop()
                except CaptureError as error:
                    self.stop_unconfirmed = True
                    self.set_state('error', str(error))

    def _recover_radio(self):
        from .recovery import RadioAbsent, RecoveryCancelled
        # Confirm the old capture is gone before touching its returned interface.
        with self.capture_control:
            self.capture.stop()
        delay=1
        while not self.stop.is_set():
            self.set_state('recovering',f'USB radio disconnected · retry in {delay}s · other feeds continue')
            if self.stop.wait(delay):raise InputStopped()
            try:
                with self.capture_control:
                    if self.stop.is_set():raise InputStopped()
                    with self.lock:self.recovery_attempts+=1
                    self.recovery.prepare(self.stop)
                    if self.stop.is_set():raise InputStopped()
                    replacement=self.capture_factory(self.live_interface)
                    try:
                        fd=replacement.start()
                    except Exception:
                        replacement.stop()
                        raise
                    self.capture,self.input_fd=replacement,fd
                    with self.lock:
                        self.capture_segments+=1
                        self.capture_recoveries+=1
                self.set_state('waiting','USB radio recovered · listening on its assigned channel')
                return
            except RadioAbsent:
                # Only confirmed physical absence/new generation is recoverable.
                delay=min(15,delay*2)
            except RecoveryCancelled:
                raise InputStopped() from None
        raise InputStopped()

    def _live(self):
        while not self.stop.is_set():
            try:
                self._read_pcap()
                with self.capture_control:
                    if self.stop.is_set():raise InputStopped()
                    outcome=self.capture.outcome()
                    self.capture.stop()
                if self.stop.is_set():raise InputStopped()
                if outcome!=124:
                    raise CaptureError('Capture ended unexpectedly; detection stopped. Check the receiver and private capture log before START.')
                self.set_state('renewing','Detection active · renewing capture')
                if self.stop.wait(.25):raise InputStopped()
                with self.capture_control:
                    if self.stop.is_set():raise InputStopped()
                    replacement=self.capture_factory(self.live_interface)
                    try:
                        fd=replacement.start()
                    except Exception:
                        replacement.stop()
                        raise
                    self.capture,self.input_fd=replacement,fd
                    with self.lock:self.capture_segments+=1
            except (CaptureError,ValueError,OSError):
                if self.stop.is_set():raise InputStopped() from None
                if self.recovery and self.recovery.disconnected(self.capture):
                    self._recover_radio()
                    continue
                raise
        raise InputStopped()

    def _simulate(self):
        if self.stop.wait(2):
            raise InputStopped()
        self.set_state('running', 'Timed recording simulation running · no radio capture')
        began = self.clock()
        for item in self.simulation['records']:
            if self.stop.wait(max(0, began+item['t']/self.speed-self.clock())):
                raise InputStopped()
            with self.lock:
                self.counts['packets'] += 1
                self.counts['decoded'] += 1
                self.last_packet = self.elapsed()
            self.publish(item)
        self.set_state('stopped', 'Simulation complete · retained positions continue aging')

    def _read_pcap(self):
        stream = PcapStream(self.input_fd, self.stop, self.heartbeat)
        stream.header()
        self.set_state('running', 'Receiver running · listening for Remote ID')
        while not self.stop.is_set():
            # Decoder output is redirected only during synchronous decode, never
            # while waiting for input (redirects are process-global).
            try:
                packet = stream.packet()
            except PacketDecodeError:
                with self.lock:
                    self.counts['packets'] += 1
                    self.counts['errors'] += 1
                    self.last_packet = self.elapsed()
                continue
            if packet is None:
                return
            with self.lock:
                self.counts['packets'] += 1
                self.last_packet = self.elapsed()
            frequency = packet[RadioTap].ChannelFrequency if RadioTap in packet else None
            if frequency is not None and 1000 <= frequency <= 10000:
                with self.lock:
                    self.last_frequency = frequency
                    if len(self.frequencies) < 64 or frequency in self.frequencies:
                        self.frequencies[frequency] = self.frequencies.get(frequency, 0)+1
                allowed = {entry[1] for entry in SCAN_PLAN} if self.scan else {self.expected_frequency}
                if self.live_interface and frequency not in allowed:
                    expected = 'scan channels 6/149 (2437/5745 MHz)' if self.scan else f'channel {self.expected_channel} ({self.expected_frequency} MHz)'
                    raise CaptureError(f'Capture stopped: received packets at {frequency} MHz, expected {expected}. Check the dedicated radio channel in the launch terminal.')
            try:
                with DECODE_LOCK, redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                    extracted = extract_odid_from_scapy_packet(packet)
                    if not extracted:
                        continue
                    data = bytes_to_rid_dict(*extracted)
                if not data:
                    with self.lock:
                        self.counts['rejected'] += 1
                    continue
                item = report(data, float(packet.time))
                with self.lock:
                    self.counts['decoded'] += 1
                    if item is None:
                        self.counts['unidentified'] += 1
                if item is not None:
                    self.publish(item)
            except Exception:
                with self.lock:
                    self.counts['errors'] += 1
        raise InputStopped()


def event(value):
    return 'event: receiver\ndata: '+json.dumps(value, separators=(',', ':'), allow_nan=False)+'\n\n'
