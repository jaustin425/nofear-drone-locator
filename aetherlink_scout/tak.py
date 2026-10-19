"""Optional write-only CoT from shared live tracks; never a radio receiver."""
import argparse
import asyncio
import configparser
import getpass
import hashlib
import os
from pathlib import Path
import socket
import ssl
import stat
import threading
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

import pytak
from dronecot import DEFAULT_OP_COT_TYPE, DEFAULT_UAS_COT_TYPE
from dronecot.functions import rid_op_to_cot_xml, rid_uas_to_cot_xml
from .decode_lock import DECODE_LOCK

ROOT = Path(__file__).resolve().parent.parent
LOCAL_CONFIG = ROOT / 'config/local-tak.ini'
URL = 'tls+wo://tak.c5solutionsgroup.com:8089'
DEFAULTS = {'COT_URL': URL, 'PYTAK_TLS_CLIENT_CERT': '~/tak-dronecot/DRONECOT.p12',
            'PYTAK_TLS_CLIENT_CAFILE': '~/tak-dronecot/ca.pem', 'PYTAK_TLS_DONT_VERIFY': '0',
            'PYTAK_TLS_DONT_CHECK_HOSTNAME': '0', 'COT_STALE': '30', 'SENSOR_ID': 'AetherLink-Scout-laptop'}
ALLOWED = set(DEFAULTS) | {'PYTAK_TLS_CLIENT_PASSWORD'}


class ConfigurationError(ValueError):
    pass


def read_config(path=LOCAL_CONFIG, *, credentials=True):
    path = Path(path)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd) as source:
        info = os.fstat(source.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077:
            raise ConfigurationError('TAK config must be an owned regular file with mode 600.')
        parser = configparser.ConfigParser(interpolation=None)
        parser.read_file(source)
    if parser.sections() != ['aetherlink_scout'] or parser.defaults():
        raise ConfigurationError('TAK config requires only [aetherlink_scout], with no DEFAULT section.')
    values = {key.upper(): value for key, value in parser['aetherlink_scout'].items()}
    if set(values)-ALLOWED:
        raise ConfigurationError('Unsupported TAK config key; use the sanitized example.')
    values = {**DEFAULTS, **values}
    if values['COT_URL'] != URL or any(values[key] != '0' for key in ('PYTAK_TLS_DONT_VERIFY', 'PYTAK_TLS_DONT_CHECK_HOSTNAME')):
        raise ConfigurationError('This profile requires the approved write-only URL and full TLS verification.')
    if not 15 <= int(values['COT_STALE']) <= 300:
        raise ConfigurationError('COT_STALE must be 15–300 seconds.')
    if not values['SENSOR_ID'].strip() or len(values['SENSOR_ID']) > 100:
        raise ConfigurationError('Use a short nonempty sensor identity.')
    for key in ('PYTAK_TLS_CLIENT_CERT', 'PYTAK_TLS_CLIENT_CAFILE'):
        values[key] = str(Path(values[key]).expanduser().absolute())
        if credentials and not Path(values[key]).is_file():
            raise ConfigurationError('Sensor certificate or CA file missing; supply the dedicated DRONECOT identity and CA.')
    if not values['PYTAK_TLS_CLIENT_CERT'].lower().endswith('.p12'):
        raise ConfigurationError('This profile requires the dedicated sensor PKCS#12 file.')
    if credentials and 'PYTAK_TLS_CLIENT_PASSWORD' not in values:
        raise ConfigurationError('Enter the certificate password privately with python -m aetherlink_scout.tak --configure.')
    # Keep credentials out of object repr/logs. Never hand config to a logging CLI.
    parser = configparser.ConfigParser(interpolation=None)
    parser['aetherlink_scout'] = values
    return parser['aetherlink_scout']


def configure(path=LOCAL_CONFIG):
    values = dict(read_config(path, credentials=False)) if Path(path).exists() else {k.lower(): v for k,v in DEFAULTS.items()}
    for key in ('pytak_tls_client_cert', 'pytak_tls_client_cafile'):
        if not Path(values[key]).expanduser().is_file():
            raise ConfigurationError('Sensor certificate or CA file missing; install the dedicated DRONECOT files first. No password requested.')
    if not os.isatty(0):
        raise ConfigurationError('Run configure in your private interactive terminal; never pipe a password.')
    values['pytak_tls_client_password'] = getpass.getpass('Dedicated DRONECOT certificate password (stored only in private local config): ')
    parser = configparser.ConfigParser(interpolation=None)
    parser['aetherlink_scout'] = values
    path = Path(path)
    # Existing files must already be private; O_NOFOLLOW prevents symlink writes.
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'w') as destination:
        parser.write(destination)
    print('Saved private TAK configuration. Password is not displayed or placed in shell arguments.')


def safe_error(error):
    """Keep transport diagnostics useful without reflecting config/payloads."""
    cause = error
    while cause.__cause__ is not None:
        cause = cause.__cause__
    if isinstance(cause, ssl.SSLCertVerificationError):
        return f'TLS verification failed (code {cause.verify_code}): {cause.verify_message}'
    if isinstance(cause, ssl.SSLError):
        return f"TLS failed: {getattr(cause, 'reason', None) or type(cause).__name__}"
    if isinstance(cause, (TimeoutError, asyncio.TimeoutError)):
        return 'Connection timed out (8-second bound).'
    if isinstance(cause, socket.gaierror):
        return f'DNS resolution failed (errno {cause.errno}).'
    if isinstance(cause, OSError):
        return f'Connection failed: {type(cause).__name__}, errno {cause.errno} ({os.strerror(cause.errno) if cause.errno else "connection closed"}).'
    if isinstance(cause, ValueError):
        return 'Client certificate/password/configuration could not be loaded; verify them privately.'
    return f'TAK output failed ({type(cause).__name__}); no credentials or payload logged.'


async def open_transport(config):
    reader, writer = await asyncio.wait_for(pytak.protocol_factory(config), 8)
    return reader, writer


def cot_time(value):
    return datetime.fromtimestamp(value, timezone.utc).isoformat(timespec='milliseconds').replace('+00:00', 'Z')


def uid(config, key, kind):
    sensor = hashlib.sha256(config['SENSOR_ID'].encode()).hexdigest()[:12]
    return f'AetherLinkScout.{sensor}.{key}.{kind}'


def cot_events(track, status, config, *, now=None):
    """Only fresh independently observed positions; metadata never refreshes a fix."""
    now = time.time() if now is None else now
    fields, times = track['fields'], track['fieldTimes']
    stale = int(config.get('COT_STALE', '30'))
    identity = fields.get('serial') or track['key'][:12]
    data = {'BasicID': identity, 'data': {'sensor_id': config['SENSOR_ID'], 'type': fields.get('radio', 'Remote ID')}}
    for source, destination in (('operator_id','OperatorID'), ('speed','SpeedHorizontal'), ('heading','Direction'),
                                ('horizontal_accuracy_code','HorizAccuracy'), ('vertical_accuracy_code','VertAccuracy')):
        if fields.get(source) is not None and (source=='operator_id' or status['elapsed']-times.get(source,-1e9)<stale):
            data[destination] = fields[source]
    result = []
    for kind, converter, prefix in (('operator',rid_op_to_cot_xml,'Operator'), ('aircraft',rid_uas_to_cot_xml,'')):
        point = fields.get(kind)
        if not point or kind not in times:
            continue
        observed = status['start_timestamp'] + times[kind]
        if now-observed >= stale or observed-now > 5:
            continue
        values = {**data, prefix+'Latitude': point['lat'], prefix+'Longitude': point['lon']}
        # Aircraft accuracy fields do not establish operator-point accuracy.
        if kind=='operator':
            values.pop('HorizAccuracy',None)
            values.pop('VertAccuracy',None)
        # Retained altitude may be older than this position. Unknown stays unknown.
        if point.get('altitude') is not None and status['elapsed']-times.get(kind+'_altitude', -1e9) < stale:
            values[prefix+'AltitudeGeo'] = point['altitude']
        with DECODE_LOCK:
            event = converter(values, {'COT_STALE': str(stale), 'COT_HOST_ID': config['SENSOR_ID']})
        event.set('uid', uid(config, track['key'], kind))
        # PyTAK rounds cot_point by default; keep Scout's decoded precision.
        event.find('point').set('lat',str(point['lat']))
        event.find('point').set('lon',str(point['lon']))
        for field, value in (('time',observed), ('start',observed), ('stale',observed+stale)):
            event.set(field,cot_time(value))
        contact = event.find('./detail/contact')
        if contact is not None:
            contact.set('callsign', f'AetherLink Scout {"reported operator" if kind=="operator" else "aircraft"} {identity}')
        movement=event.find('./detail/track')
        if movement is not None:
            for attribute, source in (('speed','speed'),('course','heading')):
                if fields.get(source) is None:movement.attrib.pop(attribute,None)
            if not movement.attrib:event.find('detail').remove(movement)
        detail = event.find('detail')
        # Replace converter links/creator with the actual correlated Scout pair.
        for child in list(detail):
            if child.tag in ('link','creator'):
                detail.remove(child)
        ET.SubElement(detail,'link',uid=uid(config,track['key'],'aircraft' if kind=='operator' else 'operator'),relation='p-p',type=DEFAULT_UAS_COT_TYPE if kind=='operator' else DEFAULT_OP_COT_TYPE)
        remarks = event.find('./detail/remarks')
        remarks.text = (f'AetherLink Scout: unverified Remote ID report; received {cot_time(observed)}; '
                        f'sources={",".join(fields.get("sources", [])) or fields.get("radio", "Remote ID")}; '
                        f'claimed ID={identity}; operator point is reported, not verified identity/residence.'
                        + (' Conflicting identity/position observations: review required.' if fields.get('identity_conflict') else ''))
        result.append((kind, observed, ET.tostring(event,encoding='utf-8')+b'\n'))
    return result


class TakOutput:
    """Bounded latest-state sampling, independent from capture; no durable outbox."""
    def __init__(self, receiver, config, *, local_output=None, connector=open_transport, interval=.25):
        if receiver.mode != 'live_stream':
            raise ConfigurationError('TAK output is forbidden for replay/simulation.')
        self.receiver, self.config = receiver, config
        self.local_output, self.connector, self.interval = local_output, connector, interval
        self.lock, self.stop = threading.RLock(), threading.Event()
        self.thread = None
        self.state, self.message = 'idle', 'Local CoT output ready' if local_output else 'TAK ready · waiting for START'
        self.sent_count = self.reconnects = self.errors = 0
        self.session = None
        self.sent, self.published = {}, {}

    def status(self):
        with self.lock:
            return {'mode':'local' if self.local_output else 'tls', 'state':self.state, 'message':self.message,
                    'sent':self.sent_count, 'reconnects':self.reconnects, 'errors':self.errors}

    def set_state(self,state,message):
        with self.lock:
            self.state,self.message=state,message

    def begin(self):
        self.thread=threading.Thread(target=self._thread_main,name='scout-tak',daemon=True)
        self.thread.start()

    def _thread_main(self):
        try:
            asyncio.run(self.run())
        except Exception as error:
            with self.lock:self.errors+=1
            self.set_state('error',safe_error(error)+' Output worker stopped; local detection continues.')

    def close(self):
        self.stop.set()
        if self.thread:
            self.thread.join(timeout=10)
        if self.thread and self.thread.is_alive():
            self.set_state('error','TAK output shutdown pending; network operations remain bounded.')

    def active(self, status):
        return (status['detection_active'] and status['state'] not in ('error','stopped')
                and not getattr(self.receiver, 'stop', self.stop).is_set())

    async def run(self):
        reader=writer=drain=None
        backoff=1
        retry_at=0
        fatal=False
        try:
            while not self.stop.is_set():
                snapshot=self.receiver.snapshot()
                status={**snapshot['status'],'start_timestamp':snapshot['metadata']['start_timestamp']}
                active=self.active(status)
                if status['session_id'] != self.session:
                    self.session=status['session_id'];self.sent.clear();self.published.clear()
                    fatal=False;retry_at=0;backoff=1
                if not active:
                    if writer:
                        writer.close();writer=None
                    if drain:
                        drain.cancel();await asyncio.gather(drain,return_exceptions=True);drain=None
                    self.set_state('idle','CoT output idle · detections retained locally')
                elif not fatal and time.monotonic() >= retry_at:
                    try:
                        if not self.local_output and writer is None:
                            self.set_state('connecting','Connecting to verified TAK TLS endpoint')
                            reader,writer=await self.connector(self.config)
                            drain=asyncio.create_task(self.drain_inbound(reader))
                            # Fresh current fixes may be resent with their original expiry.
                            self.sent.clear()
                            with self.lock:self.reconnects+=1
                        if drain and drain.done():
                            drain.result()
                            raise ConnectionError('TAK closed connection')
                        pending=[]
                        now=time.time()
                        for track in snapshot['tracks']:
                            pending.extend((track['key'],*value) for value in cot_events(track,status,self.config,now=now))
                        # Retire only markers actually sent, using their last known
                        # point. A failed write can retry without a fake (0,0) fix.
                        for key in status.get('merge_aliases',{}):
                            for kind in ('operator','aircraft'):
                                previous=self.published.get((key,kind))
                                if not previous or previous[1] <= now:continue
                                event=ET.fromstring(previous[0])
                                for field in ('time','start'):event.set(field,cot_time(now))
                                event.set('stale',cot_time(now-1))
                                pending.append((key,kind,-1,ET.tostring(event)+b'\n'))
                        for key,kind,observed,payload in pending:
                            previous=self.sent.get((key,kind))
                            if previous is not None and (observed==previous[0] or observed!=-1 and time.monotonic()-previous[1]<1):continue
                            # Recheck STOP/session immediately before writing; never replay retained tracks.
                            current=self.receiver.status()
                            if self.stop.is_set() or not self.active(current) or current['session_id']!=self.session:break
                            if self.local_output:
                                self.local_output.write(payload);self.local_output.flush()
                            else:
                                writer.write(payload);await asyncio.wait_for(writer.drain(),3)
                            self.sent[(key,kind)]=(observed,time.monotonic())
                            if observed != -1:self.published[(key,kind)]=(payload,observed+int(self.config.get('COT_STALE','30')))
                            else:self.published.pop((key,kind),None)
                            with self.lock:self.sent_count+=1
                        # Bound dedup state to current tracks plus a bounded merge map.
                        keys={track['key'] for track in snapshot['tracks']}|set(status.get('merge_aliases',{}))
                        self.sent={k:v for k,v in self.sent.items() if k[0] in keys}
                        self.published={k:v for k,v in self.published.items() if k[0] in keys and v[1]>now}
                        backoff=1
                        self.set_state('local' if self.local_output else 'connected','LOCAL CoT ONLY · no TAK transmission' if self.local_output else 'Verified TLS connected · writes are not delivery acknowledgements')
                    except Exception as error:
                        if writer:writer.close();writer=None
                        if drain:drain.cancel();await asyncio.gather(drain,return_exceptions=True);drain=None
                        with self.lock:self.errors+=1
                        # Trust/authentication/configuration failures require explicit restart.
                        cause=error
                        while cause.__cause__ is not None:cause=cause.__cause__
                        fatal=isinstance(cause,(ssl.SSLError,ValueError,SyntaxError,PermissionError))
                        self.set_state('error',safe_error(error)+(' Restart detection after correcting TLS/config.' if fatal else f' Retrying in {backoff}s; local detection continues.'))
                        retry_at=time.monotonic()+backoff;backoff=min(30,backoff*2)
                await asyncio.sleep(self.interval)
        finally:
            if drain:drain.cancel();await asyncio.gather(drain,return_exceptions=True)
            if writer:
                writer.close()
                try:await asyncio.wait_for(writer.wait_closed(),1)
                except Exception:pass
            if self.local_output:
                self.local_output.close()

    @staticmethod
    async def drain_inbound(reader):
        while await reader.read(65536):
            pass


async def check_connection(config):
    reader,writer=await open_transport(config)
    try:
        # TLS 1.3 client-auth alerts can arrive after open_connection succeeds.
        try:
            data=await asyncio.wait_for(reader.read(1),1)
            if not data:raise ConnectionError('Peer closed after TLS handshake')
        except asyncio.TimeoutError:
            pass
        print('PASS: mutual TLS connected; private CA and hostname verified. No CoT sent.')
    finally:
        writer.close()
        try:await asyncio.wait_for(writer.wait_closed(),1)
        except Exception:pass


def main():
    parser=argparse.ArgumentParser(description='AetherLink Scout private TAK setup; never opens a radio.')
    action=parser.add_mutually_exclusive_group(required=True)
    action.add_argument('--configure',action='store_true')
    action.add_argument('--check',action='store_true')
    action.add_argument('--validate-config',action='store_true')
    action.add_argument('--local-test',action='store_true',help='Synthetic local XML to stdout only; no socket, files or replay transmission')
    parser.add_argument('--config',type=Path,default=LOCAL_CONFIG)
    args=parser.parse_args()
    try:
        if args.local_test:
            now=time.time()
            track={'key':'AETHERLINK-SCOUT-SYNTHETIC','fields':{'serial':'AETHERLINK-SCOUT-SYNTHETIC','aircraft':{'lat':35.0,'lon':-115.0,'altitude':100},'operator':{'lat':35.001,'lon':-115.001,'altitude':None},'sources':['wifi6','wifi149','ble']},'fieldTimes':{'aircraft':0,'operator':0,'aircraft_altitude':0}}
            print('<!-- AetherLink Scout SYNTHETIC LOCAL TEST ONLY; no network -->')
            for _,_,payload in cot_events(track,{'start_timestamp':now,'elapsed':0},DEFAULTS):print(payload.decode().strip())
        elif args.configure:configure(args.config)
        else:
            config=read_config(args.config)
            if args.check:asyncio.run(check_connection(config))
            else:print('Private TAK configuration and file paths validated; no network or radio changes.')
    except Exception as error:
        # Config validation errors are our own fixed strings; never dump parser content.
        message=str(error) if isinstance(error,ConfigurationError) else safe_error(error)
        parser.exit(1,message+'\n')


if __name__=='__main__':main()
