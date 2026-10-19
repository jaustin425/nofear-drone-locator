"""Synthetic CoT and local mutual TLS only; no production connection or radio."""
import asyncio
import copy
import configparser
from datetime import datetime, timedelta, timezone
import io
import os
from pathlib import Path
import socket
import ssl
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.serialization import pkcs12
from cryptography.x509.oid import NameOID
from aetherlink_scout.tak import DEFAULTS, ConfigurationError, TakOutput, cot_events, read_config, safe_error
from aetherlink_scout.receiver import Receiver
from aetherlink_scout.replay import demo_dataset


def wait_for(predicate, timeout=5):
    deadline=time.monotonic()+timeout
    while not predicate():
        if time.monotonic()>deadline:raise AssertionError('Synthetic TAK condition timed out')
        time.sleep(.02)


def fixture():
    now=time.time()
    return {'kind':'snapshot','metadata':{'start_timestamp':now},'status':{'start_timestamp':now,'session_id':'synthetic-session','state':'running','detection_active':True,'elapsed':0,'merge_aliases':{}},
            'tracks':[{'key':'synthetic-shared-track','fields':{'serial':'AETHERLINK-SCOUT-TEST','sources':['wifi6','wifi149','ble'],'aircraft':{'lat':35.0000000123,'lon':-115.0000000456,'altitude':None},'operator':{'lat':35.001,'lon':-115.001,'altitude':None}},'fieldTimes':{'aircraft':0,'operator':0}}]}


class ControlledReceiver:
    mode='live_stream'
    def __init__(self):self.value=fixture();self.lock=threading.RLock()
    def snapshot(self):
        with self.lock:return copy.deepcopy(self.value)
    def status(self):
        with self.lock:return dict(self.value['status'])
    def change(self, **status):
        with self.lock:self.value['status'].update(status)


class MemoryOutput(io.BytesIO):
    def close(self):pass


class TakTests(unittest.TestCase):
    def test_converter_preserves_precision_pair_identity_unknowns_and_independent_expiry(self):
        value=fixture();track=value['tracks'][0];status=value['status'];now=status['start_timestamp']
        events={kind:ET.fromstring(raw) for kind,seen,raw in cot_events(track,status,DEFAULTS,now=now)}
        self.assertEqual(set(events),{'operator','aircraft'})
        aircraft,operator=events['aircraft'],events['operator']
        self.assertEqual(aircraft.find('point').get('lat'),'35.0000000123')
        self.assertEqual(aircraft.find('point').get('hae'),'9999999.0')
        self.assertIsNone(aircraft.find('./detail/track'))
        self.assertNotEqual(aircraft.get('uid'),operator.get('uid'))
        self.assertEqual(operator.find('./detail/link').get('uid'),aircraft.get('uid'))
        self.assertIn('unverified',operator.find('./detail/remarks').text)
        track['fieldTimes']['operator']=-31
        # New aircraft metadata/position cannot renew the retained operator fix.
        self.assertEqual([k for k,_,_ in cot_events(track,status,DEFAULTS,now=now)],['aircraft'])
        self.assertEqual(cot_events(track,status,DEFAULTS,now=now+31),[])
        track['fieldTimes'].update(operator=0,horizontal_accuracy_code=0)
        track['fields']['horizontal_accuracy_code']=10
        events={kind:ET.fromstring(raw) for kind,seen,raw in cot_events(track,status,DEFAULTS,now=now)}
        self.assertEqual(float(events['aircraft'].find('point').get('ce')),10.0)
        self.assertEqual(float(events['operator'].find('point').get('ce')),9999999.0)

    def test_local_output_is_network_free_deduplicated_and_stop_freezes_it(self):
        receiver=ControlledReceiver();receiver.change(detection_active=False,state='stopped')
        output=MemoryOutput()
        async def forbidden(_):raise AssertionError('Network forbidden')
        forwarder=TakOutput(receiver,DEFAULTS,local_output=output,connector=forbidden,interval=.02)
        forwarder.begin()
        try:
            time.sleep(.08);self.assertEqual(output.getvalue(),b'')
            receiver.change(detection_active=True,state='running')
            wait_for(lambda:forwarder.sent_count==2)
            time.sleep(.1);self.assertEqual(forwarder.sent_count,2)
            receiver.change(detection_active=False,state='stopped')
            wait_for(lambda:forwarder.state=='idle')
            before=output.getvalue();time.sleep(.1);self.assertEqual(output.getvalue(),before)
        finally:forwarder.close()
        self.assertFalse(forwarder.thread.is_alive())

    def test_late_merge_expires_old_markers_at_their_actual_position(self):
        receiver=ControlledReceiver();output=MemoryOutput()
        forwarder=TakOutput(receiver,DEFAULTS,local_output=output,interval=.02);forwarder.begin()
        try:
            wait_for(lambda:forwarder.sent_count==2)
            with receiver.lock:
                receiver.value['tracks'][0]['key']='synthetic-winner'
                receiver.value['status']['merge_aliases']={'synthetic-shared-track':'synthetic-winner'}
            wait_for(lambda:forwarder.sent_count==6)
            events=[ET.fromstring(line) for line in output.getvalue().splitlines()]
            expired=events[-2:]
            self.assertTrue(all(event.get('stale') < event.get('time') for event in expired))
            self.assertTrue(all(float(event.find('point').get('lat')) != 0 for event in expired))
            time.sleep(.1);self.assertEqual(forwarder.sent_count,6)
        finally:forwarder.close()

    def test_transport_failure_never_stops_detector_and_retries(self):
        receiver=ControlledReceiver();calls=[]
        async def unavailable(_):calls.append(True);raise ConnectionRefusedError(111,'synthetic refusal')
        forwarder=TakOutput(receiver,DEFAULTS,connector=unavailable,interval=.02);forwarder.begin()
        try:
            wait_for(lambda:forwarder.errors>=2,3)
            self.assertTrue(receiver.status()['detection_active'])
            self.assertIn('errno 111',forwarder.status()['message'])
            self.assertEqual(forwarder.sent_count,0)
        finally:forwarder.close()

    def test_fatal_tls_error_stops_output_retries_until_next_explicit_session(self):
        receiver=ControlledReceiver();calls=[]
        async def bad_tls(_):calls.append(True);raise ssl.SSLError('SYNTHETIC SECRET NOT TO BE PRINTED')
        forwarder=TakOutput(receiver,DEFAULTS,connector=bad_tls,interval=.02);forwarder.begin()
        try:
            wait_for(lambda:forwarder.errors==1)
            time.sleep(1.1);self.assertEqual(len(calls),1)
            self.assertNotIn('SECRET',forwarder.message)
            self.assertTrue(receiver.status()['detection_active'])
            receiver.change(session_id='explicit-next-start')
            wait_for(lambda:len(calls)==2)
        finally:forwarder.close()

    def test_three_scanner_feeds_use_one_cot_pair_without_replacing_capture(self):
        from aetherlink_scout.combined import CombinedReceiver
        from test_combined import synthetic_sources
        sources,captures=synthetic_sources(renew=True)
        receiver=CombinedReceiver(sources=sources)
        output=MemoryOutput();forwarder=TakOutput(receiver,DEFAULTS,local_output=output,interval=.02)
        try:
            receiver.start_detection()
            wait_for(lambda:receiver.status()['counts']['decoded']==18)
            forwarder.begin();wait_for(lambda:forwarder.sent_count==2)
            events=[ET.fromstring(line) for line in output.getvalue().splitlines()]
            self.assertEqual(len({event.get('uid') for event in events}),2)
            self.assertTrue(all(all(source in event.find('./detail/remarks').text for source in ('wifi6','wifi149','ble')) for event in events))
            self.assertEqual(receiver.status()['capture_renewals'],3)
            receiver.stop_detection()
            self.assertTrue(all(c.process is None or c.process.poll() is not None for c in captures))
        finally:
            receiver.close();forwarder.close()

    def test_replay_and_simulation_cannot_enable_output(self):
        with self.assertRaises(ValueError):TakOutput(Receiver(simulation=demo_dataset()),DEFAULTS)
        for mode in ('--demo','--simulate-demo'):
            result=subprocess.run([sys.executable,'-m','aetherlink_scout',mode,'--tak-config','DO-NOT-READ'],capture_output=True,text=True)
            self.assertEqual(result.returncode,2)
            self.assertIn('replay/simulation never transmits',result.stderr)

    def test_private_config_rejects_unsafe_tls_unknown_options_and_symlinks(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);path=root/'local.ini'
            cert=root/'test.p12';cert.touch();ca=root/'ca.pem';ca.touch()
            values={**DEFAULTS,'PYTAK_TLS_CLIENT_CERT':str(cert),'PYTAK_TLS_CLIENT_CAFILE':str(ca),'PYTAK_TLS_CLIENT_PASSWORD':'synthetic%test-only'}
            def save():
                config=configparser.ConfigParser(interpolation=None);config['aetherlink_scout']=values
                with path.open('w') as f:config.write(f)
                path.chmod(0o600)
            save();self.assertEqual(read_config(path)['PYTAK_TLS_CLIENT_PASSWORD'],'synthetic%test-only')
            path.chmod(0o644)
            with self.assertRaises(ConfigurationError):read_config(path)
            save();values['PYTAK_TLS_DONT_VERIFY']='1';save()
            with self.assertRaises(ConfigurationError):read_config(path)
            values['PYTAK_TLS_DONT_VERIFY']='0';values['COT_URL']='tls://tak.c5solutionsgroup.com:8089';save()
            with self.assertRaises(ConfigurationError):read_config(path)
            values['COT_URL']=DEFAULTS['COT_URL'];values['DEBUG']='1';save()
            with self.assertRaises(ConfigurationError):read_config(path)
            link=root/'link';link.symlink_to(path)
            with self.assertRaises(OSError):read_config(link)


class LocalTlsTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='scout-synthetic-tls-',dir='/tmp');self.root=Path(self.temp.name)
        self.servers=[];self.connections=[]
        loop=asyncio.get_running_loop()
        self.old_handler=loop.get_exception_handler()
        def expected_handshake_failure(loop,context):
            if context.get('message')=='Error on transport creation for incoming connection' and isinstance(context.get('exception'),(ConnectionResetError,ssl.SSLError)):
                return
            loop.default_exception_handler(context)
        loop.set_exception_handler(expected_handshake_failure)
        self.password='synthetic-test-certificate-only'
        ca_key=rsa.generate_private_key(public_exponent=65537,key_size=2048)
        name=x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,'AetherLink Scout synthetic CA')])
        now=datetime.now(timezone.utc)
        def issue(subject,key,*,ca=False,san=None):
            builder=x509.CertificateBuilder().subject_name(subject).issuer_name(name).public_key(key.public_key()).serial_number(x509.random_serial_number()).not_valid_before(now-timedelta(minutes=1)).not_valid_after(now+timedelta(days=1)).add_extension(x509.BasicConstraints(ca=ca,path_length=None),critical=True)
            if san:builder=builder.add_extension(x509.SubjectAlternativeName([x509.DNSName(san)]),critical=False)
            return builder.sign(ca_key,hashes.SHA256())
        ca=issue(name,ca_key,ca=True);self.ca=self.root/'ca.pem';self.ca.write_bytes(ca.public_bytes(serialization.Encoding.PEM))
        server_key=rsa.generate_private_key(public_exponent=65537,key_size=2048)
        server_cert=issue(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,'localhost')]),server_key,san='localhost')
        self.server_cert=self.root/'server.pem';self.server_key=self.root/'server.key'
        self.server_cert.write_bytes(server_cert.public_bytes(serialization.Encoding.PEM))
        self.server_key.write_bytes(server_key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption()))
        client_key=rsa.generate_private_key(public_exponent=65537,key_size=2048)
        client_cert=issue(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,'AetherLink Scout synthetic sensor')]),client_key)
        self.p12=self.root/'sensor.p12';self.p12.write_bytes(pkcs12.serialize_key_and_certificates(b'AetherLink Scout synthetic',client_key,client_cert,[ca],serialization.BestAvailableEncryption(self.password.encode())))
        for path in self.root.iterdir():path.chmod(0o600)

    async def asyncTearDown(self):
        for writer in self.connections:writer.close()
        for server in self.servers:server.close();await server.wait_closed()
        await asyncio.sleep(.05);asyncio.get_running_loop().set_exception_handler(self.old_handler);self.temp.cleanup()

    def config(self,port,host='localhost'):
        parser=configparser.ConfigParser(interpolation=None)
        parser['test']={**DEFAULTS,'COT_URL':f'tls+wo://{host}:{port}','PYTAK_TLS_CLIENT_CERT':str(self.p12),'PYTAK_TLS_CLIENT_CAFILE':str(self.ca),'PYTAK_TLS_CLIENT_PASSWORD':self.password}
        return parser['test']

    async def server(self,handler):
        context=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);context.load_cert_chain(self.server_cert,self.server_key)
        context.load_verify_locations(self.ca);context.verify_mode=ssl.CERT_REQUIRED
        server=await asyncio.start_server(handler,'127.0.0.1',0,ssl=context);self.servers.append(server)
        return server.sockets[0].getsockname()[1]

    async def test_real_pytak_pkcs12_mutual_tls_reconnects_without_stale_operator_revival(self):
        received=[];peers=[]
        async def handler(reader,writer):
            self.connections.append(writer);peers.append(writer.get_extra_info('peercert'))
            count=0
            while data:=await reader.readline():
                received.append(ET.fromstring(data));count+=1
                if len(peers)==1 and count==2:writer.close();return
        port=await self.server(handler);receiver=ControlledReceiver()
        forwarder=TakOutput(receiver,self.config(port),interval=.02);forwarder.begin()
        try:
            deadline=time.monotonic()+6
            while len(peers)<2 or len(received)<4:
                if time.monotonic()>deadline:self.fail('Synthetic TLS reconnect timed out: '+forwarder.message)
                await asyncio.sleep(.03)
            self.assertTrue(all(peers))
            self.assertEqual(len({event.get('uid') for event in received}),2)
            self.assertTrue(all(event.get('stale')==received[i%2].get('stale') for i,event in enumerate(received)))
            receiver.change(detection_active=False,state='stopped')
            await asyncio.sleep(.2);count=len(received);await asyncio.sleep(.2);self.assertEqual(len(received),count)
        finally:await asyncio.to_thread(forwarder.close)
        self.assertFalse(forwarder.thread.is_alive())

    async def test_private_ca_hostname_and_wrong_password_fail_closed(self):
        async def handler(reader,writer):self.connections.append(writer);await reader.read()
        port=await self.server(handler)
        from aetherlink_scout.tak import open_transport
        with self.assertRaises(SyntaxError) as caught:await open_transport(self.config(port,'127.0.0.1'))
        self.assertIsInstance(caught.exception.__cause__,ssl.SSLCertVerificationError)
        self.assertIn('verification failed',safe_error(caught.exception))
        config=self.config(port);config['PYTAK_TLS_CLIENT_PASSWORD']='synthetic-wrong-password'
        with self.assertRaises(ValueError):await open_transport(config)
        config=self.config(port);config['PYTAK_TLS_CLIENT_CAFILE']=''
        with self.assertRaises(SyntaxError):await open_transport(config)


if __name__=='__main__':unittest.main()
