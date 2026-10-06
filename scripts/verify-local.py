"""Exercise real API and both MCP launch paths on synthetic data and a free port."""
import asyncio
import importlib.util
import json
import os
from pathlib import Path
import secrets
import socket
import sys
import tempfile
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import httpx
import uvicorn


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def main():
    with tempfile.TemporaryDirectory() as temporary:
        db = Path(temporary) / 'demo.db'
        load('demo', ROOT/'scripts/create-demo.py').create_demo(db)
        key = secrets.token_urlsafe(32)
        os.environ['NEXT_SET_DB'] = str(db)
        os.environ['NEXT_SET_COACH_READ_KEY'] = key
        import app
        app.COACH_READ_KEY = key
        app.coach_api.DB_FILE = db
        smoke = load('direct_smoke', ROOT/'mcp/smoke_test.py')
        package = load('package_smoke', ROOT/'scripts/validate-coach-plugin.py')
        original = db.read_bytes()
        for cycle in range(2):
            listener = socket.socket()
            listener.bind(('127.0.0.1', 0))
            origin = f'http://127.0.0.1:{listener.getsockname()[1]}'
            server = uvicorn.Server(uvicorn.Config(app.app, log_level='critical', access_log=False))
            worker = threading.Thread(target=server.run, kwargs={'sockets':[listener]}, daemon=True)
            worker.start()
            try:
                deadline = time.monotonic()+15
                while not server.started and worker.is_alive() and time.monotonic()<deadline:
                    time.sleep(.05)
                if not server.started:
                    raise RuntimeError('Temporary API did not start.')
                with httpx.Client(base_url=origin, trust_env=False) as client:
                    assert client.get('/training/summary').status_code == 401
                    assert client.get('/training/summary', headers={'X-Coach-Read-Key':'invalid-test-key'}).status_code == 401
                    assert client.post('/token').status_code == 404
                results = asyncio.run(asyncio.wait_for(smoke.run_smoke(key=key, api_url=origin), timeout=45))
                smoke.validate_live_results(results)
                assert results['get_next_workout']['session']['day'] == 2
                print(f'PASS real API + six direct STDIO tools, start cycle {cycle+1}')
                if os.name == 'nt':
                    config = json.loads((ROOT/'plugins/next-set/mcp.json').read_text(encoding='utf-8'))
                    entry = config['mcpServers']['next-set']
                    entry['args'] += ['-ProjectRoot', str(ROOT), '-ApiUrl', origin, '-KeySource', 'process']
                    entry['env']['NEXT_SET_COACH_READ_KEY'] = key
                    asyncio.run(asyncio.wait_for(package.smoke(ROOT/'plugins/next-set', config), timeout=45))
                    print(f'PASS packaged PowerShell launcher, start cycle {cycle+1}')
                assert db.read_bytes() == original
            finally:
                server.should_exit = True
                worker.join(timeout=15)
                listener.close()
                if worker.is_alive():
                    raise RuntimeError('Temporary API did not stop.')
        print('PASS synthetic database unchanged; temporary services stopped. No upstream requests.')


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print(f'FAIL isolated verification ({type(error).__name__}); details suppressed to protect credentials.', file=sys.stderr)
        raise SystemExit(1)
