"""Local, bounded snapshots for review. No raw packets or external transmission."""
import json
import os
from pathlib import Path
import sqlite3
import threading
import time
import uuid

MAX_HISTORY = 1000


class HistoryStore:
    def __init__(self, path=None):
        self.lock = threading.RLock()
        if path is not None:
            path = Path(path)
            path.parent.mkdir(parents=True, exist_ok=True)
            # Newly created local history is private; never alter permissions of
            # an existing user file or grant system capture permissions.
            try:
                fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            except FileExistsError:
                pass
            else:
                os.close(fd)
        self.connection = sqlite3.connect(str(path) if path else ':memory:', check_same_thread=False)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute('''CREATE TABLE IF NOT EXISTS reports (
            id TEXT PRIMARY KEY, session_id TEXT NOT NULL, track_key TEXT NOT NULL,
            mode TEXT NOT NULL, start_timestamp REAL NOT NULL,
            observed_at REAL NOT NULL, saved_at REAL NOT NULL, track_json TEXT NOT NULL,
            UNIQUE(session_id, track_key))''')
        self.connection.commit()

    def save(self, session_id, start_timestamp, mode, tracks):
        with self.lock, self.connection:
            for track in tracks:
                self.connection.execute('''INSERT INTO reports VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(session_id, track_key) DO UPDATE SET
                    observed_at=excluded.observed_at, saved_at=excluded.saved_at,
                    track_json=excluded.track_json''',
                    (uuid.uuid4().hex, session_id, track['key'], mode, start_timestamp,
                     start_timestamp + track['lastSeen'], time.time(), json.dumps(track, allow_nan=False)))
            self.connection.execute('''DELETE FROM reports WHERE id IN (
                SELECT id FROM reports ORDER BY observed_at DESC, id LIMIT -1 OFFSET ?)''', (MAX_HISTORY,))

    def list(self, offset=0):
        with self.lock:
            rows = self.connection.execute('SELECT * FROM reports ORDER BY observed_at DESC, id LIMIT 100 OFFSET ?', (offset,)).fetchall()
            count = self.connection.execute('SELECT COUNT(*) FROM reports').fetchone()[0]
        items = []
        for row in rows:
            track = json.loads(row['track_json'])
            items.append({'id': row['id'], 'serial': track['fields'].get('serial') or 'Unidentified transmitter',
                          'observed_at': row['observed_at'], 'mode': row['mode'],
                          'aircraft_reported': bool(track['fields'].get('aircraft')),
                          'operator_reported': bool(track['fields'].get('operator'))})
        return {'items': items, 'total': count, 'retention_limit': MAX_HISTORY,
                'offset': offset, 'next_offset': offset+100 if offset+100 < count else None}

    def get(self, report_id):
        with self.lock:
            row = self.connection.execute('SELECT * FROM reports WHERE id=?', (report_id,)).fetchone()
        if row is None:
            return None
        return {'id': row['id'], 'mode': row['mode'], 'start_timestamp': row['start_timestamp'],
                'observed_at': row['observed_at'], 'saved_at': row['saved_at'],
                'track': json.loads(row['track_json'])}

    def close(self):
        with self.lock:
            self.connection.close()
