"""Health signals and aggregate diagnostics contain no reports or credentials."""
import copy
import json
import io
import os
import threading
from contextlib import redirect_stdout
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from unittest.mock import patch
from pathlib import Path
import tempfile
import unittest
from fastapi.testclient import TestClient
from aetherlink_scout.app import create_app
from aetherlink_scout.health import receiver_health,RuntimeHealth
from aetherlink_scout.replay import demo_dataset
from scripts.monitor_health import sample


def status():
    child={'mode':'live_stream','state':'running','detection_active':True,'worker_heartbeat_age':.1,'capture_process_alive':True,'last_packet_age':None,'last_report_age':None,'counts':{'packets':0,'decoded':0,'errors':0}}
    return {**child,'sources':[dict(child,id=id) for id in ('wifi6','wifi149','ble')]}


class HealthTests(unittest.TestCase):
    def test_quiet_receivers_are_listening_without_a_detection_claim(self):
        value=receiver_health(status())
        self.assertEqual(value['state'],'healthy');self.assertEqual(value['listening'],3)
        self.assertIn('not guaranteed',value['message'])
    def test_recovery_stale_worker_dead_capture_and_optional_tak_independence(self):
        for field,value in (('state','recovering'),('worker_heartbeat_age',11),('capture_process_alive',False)):
            item=status();item['sources'][0][field]=value
            self.assertEqual(receiver_health(item)['state'],'degraded')
        item=status();item['tak_output']={'state':'error','message':'synthetic TLS error'}
        self.assertEqual(receiver_health(item)['state'],'healthy')
    def test_stopped_error_replay_and_simulation_are_explicit(self):
        item=status();item.update(state='stopped',detection_active=False)
        self.assertEqual(receiver_health(item)['state'],'stopped')
        item.update(state='error');self.assertEqual(receiver_health(item)['state'],'error')
        for mode,expected in (('historical_replay','historical'),('stream_simulation','simulation')):
            self.assertEqual(receiver_health({'mode':mode})['state'],expected)
    def test_runtime_health_does_not_scan_credentials_or_change_state(self):
        with tempfile.TemporaryDirectory() as folder:
            health=RuntimeHealth(folder);value=health.snapshot()
            self.assertGreater(value['rss_bytes'],0)
            self.assertGreater(value['disk_free_bytes'],0)
            self.assertEqual(value,health.snapshot())
    def test_api_preserves_replay_and_exposes_health_and_resources(self):
        with TestClient(create_app(demo_dataset()),base_url='http://127.0.0.2') as client:
            value=client.get('/api/health').json()
            self.assertEqual(value['operational']['state'],'historical')
            self.assertFalse(value['live_capture']);self.assertFalse(value['tak_enabled'])
            self.assertIn('runtime_logs',value['runtime'])
    def test_aggregate_recorder_drops_all_unknown_strings_and_sensitive_fields(self):
        private='SYNTHETIC-PRIVATE-VALUE'
        value={'receiver':{**status(),'serial':private,'tracks':[{'lat':12.34}],'message':private,'session_id':private,'tak_output':{'state':'error','message':private,'password':private}},'runtime':{'path':private,'rss_bytes':100},'operational':{'state':'healthy'}}
        encoded=json.dumps(sample(value))
        self.assertNotIn(private,encoded);self.assertNotIn('12.34',encoded)
        self.assertIn('healthy',encoded)
        value['receiver']['state']=private;value['receiver']['sources'][0]['id']=private
        self.assertNotIn(private,json.dumps(sample(value)))

    def test_read_only_endurance_command_writes_private_allowlisted_artifacts(self):
        from scripts import monitor_health
        private='SYNTHETIC-DO-NOT-SAVE'
        value={'mode':'live_stream','receiver':{**status(),'session_id':'synthetic-session','password':private,'tracks':[{'serial':private,'aircraft':{'lat':12.34}}]},'operational':{'state':'healthy'},'runtime':{'rss_bytes':100}}
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path!='/api/health':raise AssertionError('Monitor must only read health')
                self.send_response(200);self.end_headers();self.wfile.write(json.dumps(value).encode())
            def log_message(self,*_):pass
        server=ThreadingHTTPServer(('127.0.0.2',0),Handler)
        thread=threading.Thread(target=server.serve_forever);thread.start()
        try:
            with tempfile.TemporaryDirectory() as folder:
                with patch.object(monitor_health,'ROOT',Path(folder)),patch('sys.argv',['monitor_health','--duration','1','--interval','1','--port',str(server.server_port)]),redirect_stdout(io.StringIO()):
                    monitor_health.main()
                runs=list((Path(folder)/'local-data').iterdir());self.assertEqual(len(runs),1)
                self.assertEqual(runs[0].stat().st_mode&0o777,0o700)
                for path in runs[0].iterdir():
                    self.assertEqual(path.stat().st_mode&0o777,0o600)
                    self.assertNotIn(private,path.read_text())
                summary=json.loads((runs[0]/'summary.json').read_text())
                self.assertEqual(summary['samples'],1);self.assertEqual(summary['unavailable_samples'],0)
                self.assertEqual(summary['health_states'],{'healthy':1})
        finally:server.shutdown();server.server_close();thread.join(3)


if __name__=='__main__':unittest.main()
