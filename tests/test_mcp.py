import asyncio
import importlib.util
import json
import os
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading
import unittest
import sys
from unittest.mock import patch

import httpx
import jsonschema
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = Path(__file__).resolve().parents[1]


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


bridge = load_module('coach_mcp_bridge', ROOT / 'mcp/server.py')
smoke = load_module('coach_mcp_smoke', ROOT / 'mcp/smoke_test.py')


class MCPTests(unittest.IsolatedAsyncioTestCase):
    async def test_advertised_stdio_parameter_bounds_match_runtime(self):
        params = StdioServerParameters(
            # Match the installed launcher's runpy entry point, where __main__
            # otherwise refers to the bootstrap rather than the server module.
            command=sys.executable, args=['-B', '-c',
                "import runpy,sys; sys.argv=['server','stdio']; runpy.run_module('server',run_name='__main__')"],
            cwd=str(ROOT / 'mcp'),
            env={'NEXT_SET_COACH_READ_KEY': 'fake-schema-test-key',
                 'NEXT_SET_COACH_API_URL': 'http://127.0.0.1:9'},
        )
        with open(os.devnull, 'w') as errors:
            async with stdio_client(params, errlog=errors) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    tools = {tool.name: tool for tool in (await session.list_tools()).tools}
                    search = tools['search_exercises']
                    query_validator = jsonschema.Draft202012Validator(search.input_schema)
                    self.assertTrue(query_validator.is_valid({'query':'bench press'}))
                    for arguments in ({}, {'query':''}, {'query':None}, {'query':1}, {'query':'x'*101}):
                        self.assertFalse(query_validator.is_valid(arguments))
                    result_validator = jsonschema.Draft202012Validator(search.output_schema)
                    self.assertTrue(result_validator.is_valid({'matches':[{'exercise_id':2,'name':'Bench Press'}], 'has_more':False}))
                    self.assertFalse(result_validator.is_valid({'matches':[{'exercise_id':2,'name':'Bench Press'}]*11, 'has_more':True}))
                    self.assertFalse(result_validator.is_valid({'matches':[{'exercise_id':'wrong','name':None}], 'has_more':False}))
                    for name, required in [('get_training_context', {}),
                                           ('get_exercise_history', {'exercise_id': 2})]:
                        schema = tools[name].input_schema
                        validator = jsonschema.Draft202012Validator(schema)
                        self.assertTrue(validator.is_valid(required))
                        for value in (1, 3, 5):
                            self.assertTrue(validator.is_valid(dict(required, history_sessions=value)))
                        for value in (0, 6, 10, -1, None, True, '3'):
                            with self.subTest(tool=name, value=value):
                                self.assertFalse(validator.is_valid(dict(required, history_sessions=value)))
                    validator = jsonschema.Draft202012Validator(tools['get_exercise_history'].input_schema)
                    for value in (0, -1, 2**63, None, True, 'Bench Press'):
                        self.assertFalse(validator.is_valid({'exercise_id': value}))

    async def test_tool_limits_match_api(self):
        for value in (0, 6, True, '3'):
            with self.assertRaises(ValueError):
                await bridge.get_training_context(value)
            with self.assertRaises(ValueError):
                await bridge.get_exercise_history(2, value)

    async def test_get_request_only_and_no_secret_in_errors(self):
        key = 'fake-coach-key-mcp-tests'
        original = httpx.AsyncClient
        requests = []
        def respond(request):
            requests.append(request)
            self.assertEqual(request.method, 'GET')
            self.assertEqual(request.headers['X-Coach-Read-Key'], key)
            self.assertNotIn('X-Next-Set-Key', request.headers)
            return httpx.Response(401, text='sensitive-server-error '+key)
        def client(**kwargs):
            self.assertFalse(kwargs['trust_env'])
            self.assertFalse(kwargs['follow_redirects'])
            return original(**kwargs, transport=httpx.MockTransport(respond))
        with patch.dict(os.environ, {'NEXT_SET_COACH_READ_KEY': key, 'NEXT_SET_COACH_KEY_SOURCE': ''}), patch.object(bridge.httpx, 'AsyncClient', side_effect=client):
            with self.assertRaises(RuntimeError) as error:
                await bridge.get_training_summary()
            self.assertNotIn(key, str(error.exception))
            self.assertNotIn('sensitive-server-error', str(error.exception))
        self.assertEqual(len(requests), 1)

    async def test_connection_failure_suppresses_exception_details(self):
        original = httpx.AsyncClient
        def fail(request):
            raise httpx.ConnectError('fake-secret-underlying-error', request=request)
        with patch.dict(os.environ, {'NEXT_SET_COACH_READ_KEY':'fake-key','NEXT_SET_COACH_KEY_SOURCE':''}), patch.object(bridge.httpx, 'AsyncClient', side_effect=lambda **kw: original(**kw,transport=httpx.MockTransport(fail))):
            with self.assertRaises(RuntimeError) as error:
                await bridge.get_training_summary()
            self.assertTrue(error.exception.__suppress_context__)
            self.assertNotIn('fake-secret', str(error.exception))

    async def test_no_redirect_or_invalid_json_exposure(self):
        original = httpx.AsyncClient
        for status, body in [(302, 'fake-secret'), (200, 'not-json-fake-secret'), (200, '[]')]:
            with patch.dict(os.environ, {'NEXT_SET_COACH_READ_KEY':'fake-key','NEXT_SET_COACH_KEY_SOURCE':''}), patch.object(bridge.httpx, 'AsyncClient', side_effect=lambda **kw: original(**kw,transport=httpx.MockTransport(lambda r: httpx.Response(status,text=body)))):
                with self.assertRaises(RuntimeError) as error:
                    await bridge.get_training_summary()
                self.assertNotIn('fake-secret', str(error.exception))

    def test_remote_api_and_credential_urls_rejected(self):
        for url in ('https://example.com', 'http://127.0.0.1:8787/?secret=x', 'http://user:password@localhost:8787', 'http://127.0.0.1:8787/path'):
            with patch.dict(os.environ, {'NEXT_SET_COACH_API_URL':url}), self.assertRaises(RuntimeError):
                bridge._api_url()

    def test_missing_key_and_unsafe_header_fail_closed(self):
        for key in ('', ' ', 'fake\r\nheader', 'nonascii-\u00e9'):
            with patch.dict(os.environ, {'NEXT_SET_COACH_READ_KEY':key,'NEXT_SET_COACH_KEY_SOURCE':''}), self.assertRaises(RuntimeError):
                bridge._coach_key()

    async def test_real_stdio_all_six_tools_against_local_fixture(self):
        calls = []
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass
            def do_GET(self):
                calls.append(self.path)
                if self.headers.get('X-Coach-Read-Key') != 'fake-coach-key-mcp-tests' or self.headers.get('X-Next-Set-Key'):
                    self.send_error(401); return
                payload = {'ok':True,'database_totals':{'performed_sets':6},'latest_workout_date':'2025-01-03'}
                if self.path.startswith('/training/context'):
                    payload = {'next_session':{'status':'ok'}}
                elif self.path.startswith('/training/exercises/search'):
                    payload = {'matches':[{'exercise_id':2,'name':'Bench Press'}], 'has_more':False}
                elif self.path.startswith('/training/exercise/'):
                    payload = {'sessions':[]}
                elif self.path.startswith(('/training/latest','/training/next')):
                    payload = {'status':'ok','session':{'week':1,'day':4}}
                raw = json.dumps(payload).encode()
                self.send_response(200); self.send_header('Content-Type','application/json')
                self.send_header('Content-Length',str(len(raw))); self.end_headers(); self.wfile.write(raw)
        httpd = ThreadingHTTPServer(('127.0.0.1',0), Handler)
        thread = threading.Thread(target=httpd.serve_forever,daemon=True)
        thread.start()
        try:
            with patch.dict(os.environ, {'NEXT_SET_SYNC_KEY':'fake-write-key-must-not-forward'}):
                result = await smoke.run_smoke(key='fake-coach-key-mcp-tests',api_url=f'http://127.0.0.1:{httpd.server_port}')
            smoke.validate_live_results(result)
            self.assertEqual(len(result),6)
            self.assertEqual(len(calls),6)
            self.assertEqual(result['search_exercises']['matches'][0]['name'], 'Bench Press')
            self.assertIn('/training/context?history_sessions=3',calls)
            self.assertIn('/training/exercise/2/history?history_sessions=5',calls)
        finally:
            httpd.shutdown(); httpd.server_close(); thread.join(timeout=5)
