"""Private bounded stderr with real pipes/processes; no radio or user data."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
from aetherlink_scout.runtime_logs import RuntimeLogs


class RuntimeLogTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='scout-log-test-',dir='/tmp')
        self.root=Path(self.temp.name)
        self.store=RuntimeLogs(self.root/'runtime',max_files=3,max_bytes=1024,max_age=60,file_bytes=256)
    def tearDown(self):self.temp.cleanup()
    def write(self,kind='wifi',data=b'test\n'):
        log=self.store.open(kind)
        os.write(log.fileno(),data)
        log.close();return Path(log.name)

    def test_large_stderr_is_drained_without_backpressure_and_capped_privately(self):
        log=self.store.open('wifi')
        process=subprocess.Popen([sys.executable,'-c','import os;os.write(2,b"X"*1048576)'],stderr=log)
        log.started();self.assertEqual(process.wait(timeout=5),0);log.close()
        path=Path(log.name)
        self.assertLessEqual(path.stat().st_size,256)
        self.assertIn(b'further stderr omitted',path.read_bytes())
        self.assertEqual(path.stat().st_mode&0o777,0o600)
        self.assertEqual(path.parent.stat().st_mode&0o777,0o700)
        self.assertFalse(log.worker.is_alive())

    def test_only_new_runtime_files_pruned_active_and_unrelated_files_kept(self):
        unrelated=self.store.folder;unrelated.mkdir(mode=0o700)
        note=unrelated/'field-note.log';note.write_bytes(b'preserve')
        legacy=self.root/'scout-capture-old.log';legacy.write_bytes(b'preserve')
        active=self.store.open('ble')
        for _ in range(6):self.write()
        self.assertTrue(Path(active.name).exists())
        self.assertTrue(note.exists());self.assertTrue(legacy.exists())
        active.close()
        self.assertLessEqual(len(list(unrelated.glob('scout-*.log'))),3)

    def test_byte_and_age_limits_and_symlinks(self):
        first=self.write(data=b'A'*100)
        os.utime(first,(time.time()-120,time.time()-120))
        target=self.root/'external';target.write_bytes(b'private')
        link=self.store.folder/'scout-wifi-link.log';link.symlink_to(target)
        self.store.prune()
        self.assertFalse(first.exists());self.assertTrue(link.is_symlink());self.assertEqual(target.read_bytes(),b'private')
        self.store.max_bytes=100
        for _ in range(3):self.write(data=b'B'*80)
        self.assertLessEqual(self.store.status()['bytes'],100)

    def test_repeated_segments_release_pipe_fds_threads_and_keep_retention_bound(self):
        before=len(list(Path('/proc/self/fd').iterdir()))
        for _ in range(30):self.write(data=b'X'*4096)
        self.assertLessEqual(len(list(Path('/proc/self/fd').iterdir())),before)
        self.assertLessEqual(self.store.status()['files'],3)
        self.assertLessEqual(self.store.status()['bytes'],1024)

    def test_log_directory_refuses_symlink_and_nonprivate_permissions(self):
        target=self.root/'other';target.mkdir(mode=0o700)
        self.store.folder.symlink_to(target,target_is_directory=True)
        with self.assertRaises(OSError):self.store.open('wifi')
        self.store.folder.unlink();self.store.folder.mkdir(mode=0o755)
        with self.assertRaises(OSError):self.store.open('wifi')

    def test_failed_log_maintenance_does_not_leave_a_reader_thread_waiting(self):
        with patch.object(self.store,'prune',side_effect=OSError('synthetic prune failure')):
            log=self.store.open('wifi');log.close()
        self.assertFalse(log.worker.is_alive())
        self.assertTrue(self.store.status()['maintenance_error'])


if __name__=='__main__':unittest.main()
