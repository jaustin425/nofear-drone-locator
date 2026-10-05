"""Synthetic local history retention and map clearing; no real positions or radio."""
import copy
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from aetherlink_scout.app import create_app
from aetherlink_scout.capture import CaptureError
from aetherlink_scout.history import HistoryStore, MAX_HISTORY
from aetherlink_scout.receiver import Receiver
from aetherlink_scout.replay import demo_dataset


def track(key='synthetic', seen=0):
    item=demo_dataset()['records'][0]
    return {'key':key,'firstSeen':0,'lastSeen':seen,'fields':copy.deepcopy(item),
            'fieldTimes':{'operator':seen,'aircraft':seen},'trail':[]}


class HistoryTests(unittest.TestCase):
    def test_private_file_persists_and_updates_one_snapshot_per_track_session(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'history.sqlite3'
            history=HistoryStore(path)
            history.save('session',1000,'live_stream',[track()])
            first=history.list()['items'][0]['id']
            history.save('session',1000,'live_stream',[track(seen=20)])
            self.assertEqual(history.list()['total'],1)
            self.assertEqual(history.get(first)['observed_at'],1020)
            history.close()
            history=HistoryStore(path)
            self.assertEqual(history.get(first)['track']['lastSeen'],20)
            self.assertEqual(path.stat().st_mode & 0o777,0o600)
            history.save('another-session',2000,'live_stream',[track()])
            self.assertEqual(history.list()['total'],2)
            history.close()

    def test_retention_pagination_and_missing_record(self):
        history=HistoryStore()
        history.save('s',1000,'live_stream',[track(str(i),i) for i in range(MAX_HISTORY+3)])
        page=history.list()
        self.assertEqual(page['total'],MAX_HISTORY)
        self.assertEqual(len(page['items']),100)
        self.assertEqual(page['next_offset'],100)
        last=history.list(900)
        self.assertEqual(len(last['items']),100)
        self.assertIsNone(last['next_offset'])
        self.assertIsNone(history.get("' OR 1=1 --"))
        history.close()

    def test_clear_old_keeps_active_tracks_and_history_then_all_can_be_reviewed(self):
        clock=[100.]
        receiver=Receiver(clock=lambda:clock[0])
        item=demo_dataset()['records'][0]
        receiver.publish({**item,'key':'old'})
        clock[0]+=31
        receiver.publish({**item,'key':'active'})
        session=receiver.session_id
        cleared=receiver.clear_map()
        self.assertEqual(cleared['cleared_tracks'],1)
        self.assertEqual(list(receiver.tracks),['active'])
        self.assertEqual(receiver.history.list()['total'],2)
        self.assertEqual(receiver.session_id,session)
        self.assertEqual(receiver.status()['map_revision'],1)
        counts=receiver.status()['counts']
        cleared=receiver.clear_map(only_stale=False)
        self.assertEqual(cleared['cleared_tracks'],1)
        self.assertEqual(receiver.tracks,{})
        self.assertEqual(receiver.status()['counts'],counts)
        receiver.publish({**item,'key':'active'})
        self.assertIn('active',receiver.tracks)
        self.assertEqual(receiver.history.list()['total'],2)
        receiver.close()

    def test_history_failure_does_not_lose_live_track_or_allow_destructive_clear(self):
        receiver=Receiver()
        with patch.object(receiver.history,'save',side_effect=sqlite3.OperationalError('synthetic failure')):
            receiver.publish(demo_dataset()['records'][0])
            self.assertTrue(receiver.status()['history_error'])
            with self.assertRaises(CaptureError): receiver.clear_map(False)
            self.assertEqual(len(receiver.tracks),1)
        receiver.close()

    def test_local_api_history_and_map_controls(self):
        clock=[100.]
        receiver=Receiver(simulation=demo_dataset(),clock=lambda:clock[0])
        receiver.publish(demo_dataset()['records'][0])
        with TestClient(create_app(receiver=receiver),base_url='http://127.0.0.2') as client:
            self.assertEqual(client.get('/api/history',headers={'sec-fetch-site':'cross-site'}).status_code,403)
            entry=client.get('/api/history').json()['items'][0]
            saved=client.get('/api/history/'+entry['id']).json()
            self.assertIn('operator',saved['track']['fields'])
            self.assertEqual(client.get('/api/history/missing').status_code,404)
            self.assertEqual(client.get('/api/history?offset=-1').status_code,422)
            self.assertEqual(client.post('/api/map/clear',json={},headers={'origin':'https://elsewhere.invalid'}).status_code,403)
            self.assertEqual(client.post('/api/map/clear',json={'only_stale':False}).status_code,422)
            clock[0]+=31
            result=client.post('/api/map/clear',json={}).json()
            self.assertEqual(result['cleared_tracks'],1)
            self.assertEqual(result['tracks'],[])
            self.assertEqual(client.get('/api/history').json()['total'],1)
        with TestClient(create_app(demo_dataset()),base_url='http://127.0.0.2') as client:
            self.assertEqual(client.post('/api/map/clear',json={}).status_code,409)
            self.assertEqual(client.get('/api/history').json()['total'],0)


if __name__=='__main__': unittest.main()
