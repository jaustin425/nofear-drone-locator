"""Private bounded capture stderr; no retention changes to existing field artifacts."""
import fcntl
import os
from pathlib import Path
import re
import stat
import tempfile
import threading
import time

MAX_FILE_BYTES=256*1024
MAX_FILES=100
MAX_TOTAL_BYTES=16*1024*1024
MAX_AGE_SECONDS=7*24*3600
MARKER=b'\n[AetherLink Scout: further stderr omitted; capture continues.]\n'


class RuntimeLogs:
    def __init__(self,folder,*,max_files=MAX_FILES,max_bytes=MAX_TOTAL_BYTES,max_age=MAX_AGE_SECONDS,file_bytes=MAX_FILE_BYTES):
        self.folder=Path(folder)
        self.max_files,self.max_bytes,self.max_age,self.file_bytes=max_files,max_bytes,max_age,file_bytes
        self.lock=threading.RLock()
        self.cached={'files':0,'bytes':0,'active':0,'maintenance_error':False}

    def _directory(self):
        self.folder.mkdir(parents=True,mode=0o700,exist_ok=True)
        info=self.folder.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid!=os.getuid() or info.st_mode&0o077:
            raise OSError('Runtime log directory must be private and owned by the current user.')

    def status(self):
        with self.lock:
            return {**self.cached,'max_files':self.max_files,'max_bytes':self.max_bytes,'max_file_bytes':self.file_bytes}

    def prune(self):
        """Only our closed regular log files; active files are protected by flock."""
        with self.lock:
            self._directory()
            candidates=[];active=0;failed=False
            for path in self.folder.iterdir():
                if not re.fullmatch(r'scout-(?:wifi|ble)-[A-Za-z0-9_-]+\.log',path.name):continue
                try:
                    info=path.lstat()
                    if not stat.S_ISREG(info.st_mode) or info.st_uid!=os.getuid():continue
                    candidates.append((info.st_mtime_ns,info.st_size,path))
                except OSError:failed=True
            candidates.sort(reverse=True)
            retained_files=retained_bytes=0
            for modified,size,path in candidates:
                try:
                    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW)
                    try:
                        try:fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
                        except BlockingIOError:
                            active+=1;retained_files+=1;retained_bytes+=size
                            continue
                        expired=time.time()-modified/1e9>self.max_age
                        excess=retained_files>=self.max_files or retained_bytes+size>self.max_bytes
                        if expired or excess:path.unlink()
                        else:retained_files+=1;retained_bytes+=size
                    finally:os.close(fd)
                except OSError:failed=True
            self.cached={'files':retained_files,'bytes':retained_bytes,'active':active,'maintenance_error':failed}

    def open(self,kind):
        if kind not in ('wifi','ble'):raise ValueError('Unknown runtime log kind')
        with self.lock:
            self._directory()
            log=BoundedStderr(self,kind)
            try:self.prune()
            except OSError:self.cached['maintenance_error']=True
            return log


class BoundedStderr:
    def __init__(self,store,kind):
        self.store=store
        fd,self.name=tempfile.mkstemp(prefix=f'scout-{kind}-',suffix='.log',dir=store.folder)
        fcntl.flock(fd,fcntl.LOCK_EX)
        self.file=os.fdopen(fd,'wb',buffering=0)
        self.read_fd,self.write_fd=os.pipe()
        self.closed=False
        self.failure=False
        self.worker=threading.Thread(target=self._drain,name='scout-stderr',daemon=False)
        self.worker.start()

    def fileno(self):return self.write_fd

    def started(self):
        # Popen has duplicated this pipe to the child's stderr. Parent must not
        # keep a writer open, or reader EOF would never arrive after child exit.
        if self.write_fd is not None:
            os.close(self.write_fd);self.write_fd=None

    def _drain(self):
        written=0;marked=False
        budget=max(0,self.store.file_bytes-len(MARKER))
        try:
            while chunk:=os.read(self.read_fd,8192):
                take=min(len(chunk),max(0,budget-written))
                if take and not self.failure:
                    try:self.file.write(chunk[:take]);written+=take
                    except OSError:self.failure=True
                if len(chunk)>take and not marked:
                    if not self.failure:
                        try:self.file.write(MARKER[:self.store.file_bytes-written])
                        except OSError:self.failure=True
                    marked=True
                # Always drain/discard beyond the cap; stderr must not block capture.
        except OSError:self.failure=True
        finally:
            os.close(self.read_fd)

    def close(self):
        if self.closed:return
        self.started()
        self.worker.join(timeout=2)
        if self.worker.is_alive():raise OSError('Capture stderr shutdown is unconfirmed.')
        self.file.close();self.closed=True
        try:self.store.prune()
        except OSError:
            with self.store.lock:self.store.cached['maintenance_error']=True
        if self.failure:
            with self.store.lock:self.store.cached['maintenance_error']=True


_default=None
_default_lock=threading.Lock()

def runtime_logs():
    global _default
    with _default_lock:
        if _default is None:_default=RuntimeLogs(Path(__file__).resolve().parent.parent/'local-data/runtime-logs')
        return _default
