"""Read-only operational health; quiet RF and optional TAK are separate concerns."""
import os
from pathlib import Path
import resource
import shutil
import threading
import time
from .runtime_logs import runtime_logs

HEARTBEAT_LIMIT=10


def receiver_health(status):
    mode=status.get('mode')
    if mode!='live_stream':
        return {'state':'simulation' if mode=='stream_simulation' else 'historical','message':'Recorded input only · no live receiver health claim','listening':0,'configured':0,'sources':[]}
    children=status.get('sources') or [{'id':'receiver',**status}]
    summaries=[]
    for source in children:
        state=source['state'];active=source['detection_active']
        heartbeat=source.get('worker_heartbeat_age')
        process=source.get('capture_process_alive')
        if source.get('stop_unconfirmed'):
            health='error';message='Shutdown unconfirmed'
        elif state=='error':health='error';message='Receiver failed'
        elif state=='recovering':health='recovering';message='USB recovery pending'
        elif state=='stopped' or not active:
            health='stopped';message='Receiver stopped'
        elif state=='running' and process is False:
            health='error';message='Capture process exited'
        elif state=='running' and heartbeat is not None and heartbeat>HEARTBEAT_LIMIT:
            health='unresponsive';message='Input worker heartbeat overdue'
        elif state=='running':
            health='listening';message='Listening · absence of reports does not prove absence of aircraft'
        else:health='starting';message='Capture starting or renewing'
        summaries.append({'id':source.get('id','receiver'),'state':health,'message':message,
                          'heartbeat_age':heartbeat,'last_packet_age':source.get('last_packet_age'),
                          'last_report_age':source.get('last_report_age')})
    listening=sum(source['state']=='listening' for source in summaries)
    configured=len(summaries)
    if status.get('stop_unconfirmed') or status['state']=='error':
        state='error';message='Detection error · inspect receiver status'
    elif not status['detection_active']:
        state='stopped';message='Detection stopped · retained positions continue aging'
    elif any(s['state'] in ('error','unresponsive','recovering','stopped') for s in summaries):
        state='degraded';message=f'Detection degraded · {listening}/{configured} inputs listening'
    elif listening==configured:
        state='healthy';message=f'{listening}/{configured} inputs listening · RF coverage is not guaranteed'
    else:state='starting';message=f'Detection starting/renewing · {listening}/{configured} inputs listening'
    return {'state':state,'message':message,'listening':listening,'configured':configured,'sources':summaries}


class RuntimeHealth:
    def __init__(self,folder=None):
        self.folder=Path(folder) if folder else Path(__file__).resolve().parent.parent/'local-data'
        self.started=time.monotonic();self.lock=threading.Lock();self.checked=0;self.cached={}

    def snapshot(self):
        with self.lock:
            if time.monotonic()-self.checked<1:return dict(self.cached)
            usage=resource.getrusage(resource.RUSAGE_SELF)
            rss=None;fds=None;free=None
            try:
                rss=int(Path('/proc/self/statm').read_text().split()[1])*os.sysconf('SC_PAGE_SIZE')
                fds=len(list(Path('/proc/self/fd').iterdir()))
            except (OSError,ValueError,IndexError):pass
            try:free=shutil.disk_usage(self.folder if self.folder.exists() else self.folder.parent).free
            except OSError:pass
            self.cached={'uptime_seconds':time.monotonic()-self.started,'rss_bytes':rss,
                         'peak_rss_bytes':usage.ru_maxrss*1024,'cpu_seconds':usage.ru_utime+usage.ru_stime,
                         'open_fds':fds,'disk_free_bytes':free,'runtime_logs':runtime_logs().status()}
            self.checked=time.monotonic()
            return dict(self.cached)
