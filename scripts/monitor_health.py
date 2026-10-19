"""Bounded read-only laptop endurance recording; no detection controls or radio changes."""
import argparse
from collections import Counter
from datetime import datetime, timezone
import json
import hashlib
import os
from pathlib import Path
import sys
import tempfile
import time

import httpx

ROOT=Path(__file__).resolve().parent.parent
MAX_SAMPLES=10000
MAX_BYTES=16*1024*1024


def numeric(value):
    import math
    return value if type(value) in (int,float) and math.isfinite(value) else None


def sample(value):
    """Allowlist only; do not persist reports, raw messages, config or server responses."""
    if not isinstance(value,dict):raise ValueError('Invalid health response')
    receiver=value.get('receiver') or {}
    if not isinstance(receiver,dict):raise ValueError('Invalid receiver health')
    allowed_states={'stopped','waiting','running','renewing','recovering','error'}
    def state(value):return value if value in allowed_states else 'unknown'
    def counts(source):return {key:numeric((source.get('counts') or {}).get(key)) for key in ('packets','decoded','errors','rejected','unidentified')}
    sources=[]
    for source in receiver.get('sources',[])[:8]:
        if not isinstance(source,dict):continue
        sources.append({'id':source.get('id') if source.get('id') in ('wifi6','wifi149','ble') else 'receiver',
                        'state':state(source.get('state')),'counts':counts(source),
                        **{key:numeric(source.get(key)) for key in ('worker_heartbeat_age','last_packet_age','last_report_age','capture_segments','capture_recoveries','recovery_attempts')}})
    runtime=value.get('runtime') or {}
    logs=runtime.get('runtime_logs') or {}
    tak=receiver.get('tak_output') or {}
    tak_state=tak.get('state') if tak.get('state') in ('idle','connecting','connected','local','error') else 'unknown'
    operational=value.get('operational') or {}
    health=operational.get('state') if operational.get('state') in ('healthy','degraded','error','stopped','starting','historical','simulation') else 'unknown'
    session=receiver.get('session_id')
    mode=value.get('mode') if value.get('mode') in ('live_stream','stream_simulation','historical_replay') else 'unknown'
    return {'observed_utc':datetime.now(timezone.utc).isoformat(),'mode':mode,
            'session_marker':hashlib.sha256(session.encode()).hexdigest()[:12] if isinstance(session,str) else None,
            'state':state(receiver.get('state')),
            'health':health,'detection_active':receiver.get('detection_active') is True,
            'counts':counts(receiver),'sources':sources,
            'tak':{'state':tak_state,**{key:numeric(tak.get(key)) for key in ('sent','errors','reconnects')}},
            'runtime':{**{key:numeric(runtime.get(key)) for key in ('uptime_seconds','rss_bytes','peak_rss_bytes','cpu_seconds','open_fds','disk_free_bytes')},
                       'runtime_logs':{**{key:numeric(logs.get(key)) for key in ('files','bytes','active','max_files','max_bytes','max_file_bytes')},'maintenance_error':logs.get('maintenance_error') is True}}}


def private_json(path,value):
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,'w') as output:json.dump(value,output,indent=2,allow_nan=False)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--duration',type=int,default=7200,help='Seconds, 1–86400; default two hours')
    parser.add_argument('--interval',type=int,default=10,help='Seconds, 1–60')
    parser.add_argument('--port',type=int,default=8765,help='Scout port on fixed loopback 127.0.0.2')
    args=parser.parse_args()
    if not 1<=args.duration<=86400 or not 1<=args.interval<=60 or not 1024<=args.port<=65535:parser.error('Invalid duration, interval or loopback port')
    parent=ROOT/'local-data';parent.mkdir(exist_ok=True)
    folder=Path(tempfile.mkdtemp(prefix='reliability-',dir=parent))
    private_json(folder/'run.json',{'product':'AetherLink Scout','mode':'READ-ONLY HEALTH OBSERVATION','duration_seconds':args.duration,'interval_seconds':args.interval,'started_utc':datetime.now(timezone.utc).isoformat()})
    fd=os.open(folder/'health.jsonl',os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    states=Counter();samples=failures=written=0;began=time.monotonic();reason='duration_completed'
    print('Read-only Scout health observation; no capture, network-state or detection-control changes.')
    print('Private aggregate artifacts:',folder)
    try:
        with os.fdopen(fd,'w') as output,httpx.Client(trust_env=False,timeout=2) as client:
            while time.monotonic()-began<args.duration:
                try:
                    response=client.get(f'http://127.0.0.2:{args.port}/api/health')
                    response.raise_for_status()
                    if len(response.content)>256*1024:raise ValueError('Oversized health response')
                    value=sample(response.json())
                except (httpx.HTTPError,ValueError,TypeError,AttributeError):
                    failures+=1;value={'observed_utc':datetime.now(timezone.utc).isoformat(),'health':'unavailable'}
                encoded=json.dumps(value,allow_nan=False)+'\n'
                if written+len(encoded.encode())>MAX_BYTES or samples>=MAX_SAMPLES:
                    reason='recording_limit';break
                output.write(encoded);output.flush();written+=len(encoded.encode());samples+=1;states[value['health']]+=1
                time.sleep(min(args.interval,max(0,args.duration-(time.monotonic()-began))))
    except KeyboardInterrupt:reason='interrupted'
    finally:
        private_json(folder/'summary.json',{'samples':samples,'unavailable_samples':failures,'health_states':dict(states),'elapsed_seconds':time.monotonic()-began,'completion':reason,'result':'OBSERVATION ONLY; no automatic field-test pass claim'})
    print(f'Finished: {samples} aggregate samples; {failures} unavailable. Completion: {reason}.')


if __name__=='__main__':main()
