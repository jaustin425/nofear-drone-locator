"""One detector lifecycle and shared observations over independently bounded inputs."""
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
import copy
from dataclasses import dataclass
import hashlib
import math
import sqlite3
import threading
import time

from .ble import BleReceiver
from .capture import LiveCapture, CaptureError, CAPTURE_SEGMENT_SECONDS
from .receiver import Receiver, MAX_TRACKS
from .health import receiver_health


@dataclass
class Source:
    id: str
    label: str
    receiver: Receiver


def default_sources(recover_radios=False):
    def panda(interface):
        return LiveCapture(interface, driver='mt76x0u')
    def techkey(interface):
        return LiveCapture(interface, channel=149, frequency=5745, driver='rtl88x2bu')
    sources = [Source('wifi6', 'Panda · Wi-Fi 6', Receiver(live_interface='wlx9cefd5f67d4c', capture_factory=panda)),
            Source('wifi149', 'Techkey · Wi-Fi 149', Receiver(live_interface='wlx1cbfce038535', capture_factory=techkey, channel=149, frequency=5745)),
            Source('ble', 'Bluetooth · legacy BLE', BleReceiver())]
    if recover_radios:
        from .recovery import RadioRecovery
        for source,address,driver,channel,frequency in (
            (sources[0],'9c:ef:d5:f6:7d:4c','mt76x0u',6,2437),
            (sources[1],'1c:bf:ce:03:85:35','rtl88x2bu',149,5745)):
            recovery=RadioRecovery(source.receiver.live_interface,address,driver,channel,frequency)
            source.receiver.recovery=recovery
            source.receiver.capture_factory=recovery.capture
    return sources


class CombinedReceiver(Receiver):
    def __init__(self, *, sources=None, recover_radios=False, **kwargs):
        super().__init__(**kwargs)
        self.sources = list(default_sources(recover_radios) if sources is None else sources)
        if not self.sources or len({s.id for s in self.sources}) != len(self.sources):
            raise ValueError('Combined detector requires distinct sources.')
        self.worker_daemon = False  # launcher cannot restore while cleanup is pending
        self.aliases = OrderedDict()
        self.claims = OrderedDict()
        self.source_claims = OrderedDict()
        self.merge_aliases = OrderedDict()
        self.positions = OrderedDict()
        self.accepting = False
        self.failure = None
        self.message = 'Ready · prepare both Wi-Fi radios and powered Bluetooth, then press START'
        for source in self.sources:
            source.receiver.publish = self._forwarder(source)

    def _forwarder(self, source):
        def forward(item):
            with source.receiver.lock:
                source.receiver.last_report = source.receiver.elapsed()
            try:
                self.ingest(source.id, item)
            except Exception:
                self.failure = 'Combined report processing failed; all sources are stopping.'
                self.stop.set()
        return forward

    def metadata(self):
        value = super().metadata()
        value['source'] = 'Combined detector · Wi-Fi 6 + Wi-Fi 149 + legacy BLE'
        return value

    def status(self):
        children = [(s, s.receiver.status()) for s in self.sources]
        with self.lock:
            self.counts = {key: sum(status['counts'][key] for _, status in children) for key in self.counts}
            ages = [status['last_packet_age'] for _, status in children if status['last_packet_age'] is not None]
            self.last_packet = self.elapsed()-min(ages) if ages else None
            self.capture_segments = sum(status['capture_segments'] for _, status in children)
            value = super().status()
            value.update(combined_enabled=True, continuous_capture=True, capture_managed_by_app=True,
                         capture_segment_seconds=CAPTURE_SEGMENT_SECONDS,
                         capture_renewals=sum(max(0, status['capture_segments']-1-status['capture_recoveries']) for _, status in children),
                         radio_recovery_enabled=any(s.receiver.recovery for s in self.sources),
                         capture_recoveries=sum(status['capture_recoveries'] for _,status in children),
                         expected_channel=None, expected_frequency_mhz=None,
                         merge_aliases=dict(self.merge_aliases),
                         sources=[{**status, 'id': source.id, 'label': source.label} for source, status in children])
            value['health']=receiver_health(value)
            return value

    def start_detection(self):
        with self.control:
            if self.status()['detection_active']:
                return self.status()
            with self.lock:
                for mapping in (self.aliases, self.claims, self.source_claims, self.merge_aliases, self.positions):
                    mapping.clear()
                self.failure = None
            for source in self.sources:
                with source.receiver.lock:
                    source.receiver.counts = dict(packets=0, decoded=0, errors=0, rejected=0, unidentified=0)
                    source.receiver.last_packet = source.receiver.last_report = None
                    source.receiver.capture_segments = 0
                    source.receiver.capture_recoveries = source.receiver.recovery_attempts = 0
                    if hasattr(source.receiver, 'service_updates'):
                        source.receiver.service_updates = source.receiver.rid_candidates = 0
            return super().start_detection()

    def stop_detection(self):
        with self.control:
            self.stop.set()
            for source in self.sources:
                source.receiver.stop.set()
            if self.thread:
                self.thread.join(timeout=35)
                if self.thread.is_alive():
                    self.stop_unconfirmed = True
                    self.set_state('error', 'Combined shutdown is pending; retry STOP. Radios must not be restored yet.')
                    raise CaptureError(self.message)
            self.stop_unconfirmed = False
            self.set_state('stopped', 'All detection sources stopped · retained positions continue aging')
            with self.lock:
                self._save_history(list(self.tracks.values()))
            return self.status()

    def _run(self):
        self.accepting = True
        try:
            for source in self.sources:
                if self.stop.is_set():
                    break
                try:
                    source.receiver.start_detection()
                except CaptureError as error:
                    raise CaptureError(f'{source.label}: {error} · all sources are stopping') from None
            while not self.stop.wait(.1):
                self.heartbeat()
                statuses = [(s, s.receiver.status()) for s in self.sources]
                for source, status in statuses:
                    if status['state'] == 'error' or not status['detection_active']:
                        raise CaptureError(f'{source.label}: {status["message"]} · all sources are stopping')
                states = [status['state'] for _, status in statuses]
                if 'recovering' in states:
                    self.set_state('recovering', 'Detection degraded · waiting for a dedicated USB radio; healthy feeds continue')
                elif 'renewing' in states:
                    self.set_state('renewing', 'Combined detection active · a bounded source is renewing')
                elif all(state == 'running' for state in states):
                    self.set_state('running', 'Combined detection running · Wi-Fi 6, Wi-Fi 149 and legacy BLE')
                else:
                    self.set_state('waiting', 'Combined detection starting · waiting for source readiness')
        except CaptureError as error:
            self.failure = str(error)
        except Exception:
            self.failure = 'A combined source could not start; check prepared radios and Bluetooth. All sources are stopping.'
        finally:
            self.stop.set()
            self.accepting = False
            for source in self.sources:
                source.receiver.stop.set()
            # Independent stops run concurrently. Keep the non-daemon supervisor
            # alive on unconfirmed exit, so the terminal launcher cannot restore.
            while True:
                errors = []
                with ThreadPoolExecutor(max_workers=len(self.sources), thread_name_prefix='scout-source-stop') as pool:
                    jobs = [pool.submit(s.receiver.stop_detection) for s in self.sources]
                    for job in jobs:
                        try:
                            job.result()
                        except Exception:
                            errors.append(True)
                if not errors:
                    self.stop_unconfirmed = False
                    break
                self.stop_unconfirmed = True
                self.set_state('error', 'A source shutdown is unconfirmed; all sources are stopping. Do not restore radios yet.')
                time.sleep(.2)
            self.status()
            with self.lock:
                self._save_history(list(self.tracks.values()))
            self.set_state('error' if self.failure else 'stopped', self.failure or 'All detection sources stopped · retained positions continue aging')

    @staticmethod
    def _key(value):
        return hashlib.sha256(value.encode()).hexdigest()[:24]

    def _conflicting_position(self, key, source_id, item):
        point = item.get('aircraft')
        if not point:
            return False
        now = self.elapsed()
        for peer, (seen, lat, lon) in self.positions.get(key, {}).items():
            if peer != source_id and now-seen <= 5:
                # Conservative gross disagreement flag, not an accuracy estimate.
                dlat = math.radians(point['lat']-lat)
                dlon = math.radians(point['lon']-lon)
                a = math.sin(dlat/2)**2 + math.cos(math.radians(lat))*math.cos(math.radians(point['lat']))*math.sin(dlon/2)**2
                if 6371000*2*math.asin(min(1, math.sqrt(max(0, a)))) > 1000:
                    return True
        return False

    def _merge(self, first, second):
        if first == second:
            return first
        available = [key for key in (first, second) if key in self.tracks]
        if len(available) < 2:
            return available[0] if available else first
        winner, loser = sorted(available, key=lambda k: self.tracks[k]['firstSeen'])
        target, old = self.tracks[winner], self.tracks[loser]
        previous_aircraft_time = target['fieldTimes'].get('aircraft', -1)
        source_union = sorted(set(target['fields'].get('sources', [])) | set(old['fields'].get('sources', [])))
        for field, value in old['fields'].items():
            if field in ('aircraft', 'operator'):
                combined = dict(target['fields'].get(field, {}))
                if old['fieldTimes'].get(field, -1) > target['fieldTimes'].get(field, -1):
                    combined.update({k: value[k] for k in ('lat','lon')})
                    target['fieldTimes'][field] = old['fieldTimes'][field]
                alt = field+'_altitude'
                if old['fieldTimes'].get(alt, -1) > target['fieldTimes'].get(alt, -1):
                    combined['altitude'] = value.get('altitude')
                    target['fieldTimes'][alt] = old['fieldTimes'][alt]
                target['fields'][field] = combined
            elif old['fieldTimes'].get(field, -1) > target['fieldTimes'].get(field, -1):
                target['fields'][field] = copy.deepcopy(value)
                target['fieldTimes'][field] = old['fieldTimes'][field]
        target['fields']['sources'] = source_union
        # Preserve one known trail rather than fabricate cross-radio ordering.
        if old['trail'] and old['fieldTimes'].get('aircraft', -1) > previous_aircraft_time:
            target['trail'] = copy.deepcopy(old['trail'])
        target['firstSeen'] = min(target['firstSeen'], old['firstSeen'])
        target['lastSeen'] = max(target['lastSeen'], old['lastSeen'])
        del self.tracks[loser]
        self.history_saved.pop(loser, None)
        for alias, key in list(self.aliases.items()):
            if key == loser:
                self.aliases[alias] = winner
        for keys in self.claims.values():
            if loser in keys:
                keys.discard(loser);keys.add(winner)
        for source_id, observation in self.positions.pop(loser, {}).items():
            existing = self.positions.setdefault(winner, {}).get(source_id)
            if not existing or observation[0] >= existing[0]:
                self.positions[winner][source_id] = observation
        for key, destination in list(self.merge_aliases.items()):
            if destination == loser:
                self.merge_aliases[key] = winner
        self.merge_aliases[loser] = winner
        self._save_history([target])
        if not self.history_error:
            try:
                self.history.remove_tracks(self.session_id, [loser])
            except (OSError, sqlite3.Error):
                self.history_error = True
        self.sequence += 1
        self.events.append({'key': winner, 'sequence': self.sequence, 'merge_from': [loser], 'merged_track': copy.deepcopy(target)})
        return winner

    def ingest(self, source_id, item):
        with self.lock:
            if not self.accepting or self.stop.is_set():
                return
            alias = (source_id, item['key'])
            key = self.aliases.get(alias)
            prior_claim = self.source_claims.get(alias)
            claim = f'{item.get("id_type")}:{item["serial"]}' if item.get('serial') else prior_claim
            changed_claim = bool(prior_claim and item.get('serial') and prior_claim != claim)
            keys = self.claims.setdefault(claim, set()) if claim else set()
            candidates = [k for k in keys if k in self.tracks]
            conflict = changed_claim or bool(key and self._conflicting_position(key, source_id, item))
            if not key:
                if len(candidates) == 1 and not self._conflicting_position(candidates[0], source_id, item):
                    key = candidates[0]
                else:
                    key = self._key('claim:'+claim) if claim and not candidates else self._key(f'{source_id}:{item["key"]}')
                    conflict = len(candidates) > 0
            elif claim and not conflict and len(candidates) == 1 and key != candidates[0]:
                # Two provisional tracks can be reconciled after Basic ID arrives.
                other = candidates[0]
                comparison = item
                previous = self.positions.get(key, {}).get(source_id)
                if not item.get('aircraft') and previous and self.elapsed()-previous[0] <= 5:
                    comparison = {**item, 'aircraft': {'lat': previous[1], 'lon': previous[2]}}
                if not self._conflicting_position(other, source_id, comparison):
                    key = self._merge(key, other)
                else:
                    conflict = True
            if conflict:
                old_key = key
                provisional = self.tracks.get(key)
                if changed_claim or not provisional or set(provisional['fields'].get('sources', [])) != {source_id}:
                    key = self._key(f'conflict:{source_id}:{item["key"]}:{claim}')
                for conflict_key in set(candidates+[old_key]):
                    if conflict_key in self.tracks:
                        self.tracks[conflict_key]['fields']['identity_conflict'] = True
                        self.sequence += 1
                        self.events.append({'key': conflict_key, 'sequence': self.sequence, 'merge_from': [], 'merged_track': copy.deepcopy(self.tracks[conflict_key])})
                if old_key != key:
                    self.positions.get(old_key, {}).pop(source_id, None)
                item = {**item, 'identity_conflict': True}
            self.aliases[alias] = key
            self.aliases.move_to_end(alias)
            if claim:
                self.source_claims[alias] = claim
                keys.add(key)
                self.claims.move_to_end(claim)
            seen = set(self.tracks.get(key, {}).get('fields', {}).get('sources', []))
            seen.add(source_id)
            value = {**item, 'key': key, 'sources': sorted(seen), 'last_source': source_id}
            super().publish(value)
            if len(keys) > MAX_TRACKS:
                keys.intersection_update(self.tracks)
            if item.get('aircraft'):
                point = item['aircraft']
                self.positions.setdefault(key, {})[source_id] = (self.elapsed(), point['lat'], point['lon'])
                self.positions.move_to_end(key)
            # Association memory is bounded independently of map clearing/history.
            for mapping, limit in ((self.aliases, MAX_TRACKS*6), (self.source_claims, MAX_TRACKS*6), (self.claims, MAX_TRACKS*2), (self.merge_aliases, MAX_TRACKS*6), (self.positions, MAX_TRACKS)):
                while len(mapping) > limit:
                    mapping.popitem(last=False)
