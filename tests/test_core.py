"""The default API must work without upstream configuration or private files."""
import importlib.util
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
import app
import coach_api

ROOT = Path(__file__).resolve().parents[1]


class CoreTests(unittest.TestCase):
    def test_core_import_does_not_load_optional_adapter(self):
        result = subprocess.run([sys.executable, '-B', '-c',
            "import app,sys; assert not any(k.startswith('adapters') for k in sys.modules)"],
            cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_demo_and_all_six_core_views_are_read_only(self):
        with tempfile.TemporaryDirectory() as temporary:
            db = Path(temporary) / 'demo.db'
            subprocess.run([sys.executable, str(ROOT/'scripts/create-demo.py'), '--db', str(db)],
                           check=True, capture_output=True)
            before = db.read_bytes()
            with patch.object(coach_api, 'DB_FILE', db), patch.object(app, 'COACH_READ_KEY', 'fake-core-key'), TestClient(app.app) as client:
                paths = ['/training/context', '/training/latest', '/training/next', '/training/summary',
                         '/training/exercise/1/history', '/training/exercises/search?query=bench']
                for endpoint in paths:
                    self.assertEqual(client.get(endpoint).status_code, 401)
                    self.assertEqual(client.get(endpoint, headers={'X-Coach-Read-Key':'fake-core-key'}).status_code, 200)
                headers = {'X-Coach-Read-Key':'fake-core-key'}
                self.assertEqual(client.get('/training/next', headers=headers).json()['session']['day'], 2)
                self.assertEqual(client.get('/training/latest', headers=headers).json()['session']['completion'], 'inferred_complete')
                self.assertEqual(client.get('/training/exercise/1/history', headers=headers).json()['sessions'][0]['exercises'][0]['sets'][0]['actual_reps'], 8)
                for endpoint in ['/token', '/sync/workout/100', '/workout/100']:
                    self.assertEqual(client.post(endpoint).status_code, 404)
            self.assertEqual(db.read_bytes(), before)
            retry = subprocess.run([sys.executable, str(ROOT/'scripts/create-demo.py'), '--db', str(db)], capture_output=True)
            self.assertNotEqual(retry.returncode, 0)
            self.assertEqual(db.read_bytes(), before)

    def test_unconfigured_core_fails_closed(self):
        with patch.object(app, 'COACH_READ_KEY', ''), TestClient(app.app) as client:
            self.assertEqual(client.get('/training/summary').status_code, 503)
