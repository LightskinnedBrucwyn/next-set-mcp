"""Call every read-only tool over real STDIO; never prints credentials or full data."""
from __future__ import annotations
import asyncio
import json
import os
from pathlib import Path
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

SERVER = Path(__file__).with_name('server.py')
CASES = (
    ('get_training_summary', {}),
    ('get_training_context', {'history_sessions': 3}),
    ('get_latest_workout', {}),
    ('get_next_workout', {}),
    ('get_exercise_history', {'exercise_id': 2, 'history_sessions': 5}),
    ('search_exercises', {'query': 'bench press'}),
)


async def run_smoke(*, key=None, api_url='http://127.0.0.1:8787'):
    # SDK supplies its OS-only environment allowlist. Never inherit the parent's
    # sync key, bearer token, proxy credentials, or arbitrary environment vars.
    env = {'PYTHONIOENCODING': 'utf-8', 'NEXT_SET_COACH_API_URL': api_url}
    if key is None:
        env['NEXT_SET_COACH_KEY_SOURCE'] = 'windows-user'
    else:
        env['NEXT_SET_COACH_READ_KEY'] = key
    params = StdioServerParameters(command=sys.executable, args=['-B', str(SERVER), 'stdio'],
                                   cwd=str(SERVER.parent.parent), env=env)
    output = {}
    with open(os.devnull, 'w') as stderr:
        async with stdio_client(params, errlog=stderr) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                listed = await session.list_tools()
                expected = {name for name, _ in CASES}
                if {tool.name for tool in listed.tools} != expected:
                    raise RuntimeError('MCP tool discovery did not match the six read-only tools.')
                for tool in listed.tools:
                    if not tool.annotations or not tool.annotations.read_only_hint:
                        raise RuntimeError('MCP read-only annotation is missing.')
                for name, arguments in CASES:
                    result = await session.call_tool(name, arguments, read_timeout_seconds=20)
                    if getattr(result, 'is_error', True):
                        raise RuntimeError(f'{name} failed. Check the Coach API and read key; tool error contents were suppressed.')
                    payload = getattr(result, 'structured_content', None)
                    if payload is None:
                        payload = json.loads(next(block.text for block in result.content if block.type == 'text'))
                    if not isinstance(payload, dict):
                        raise RuntimeError(f'{name} returned an unexpected shape.')
                    output[name] = payload
    return output


def validate_live_results(results):
    # Sanity targets, not hardcoded counts or application decisions.
    summary = results['get_training_summary']
    search = results['search_exercises']
    if not isinstance(search.get('matches'), list) or len(search['matches']) > 10 or not isinstance(search.get('has_more'), bool):
        raise RuntimeError('Search result is missing expected bounded fields.')
    if summary.get('ok') is not True or 'performed_sets' not in summary.get('database_totals', {}):
        raise RuntimeError('Summary result is missing expected fields.')
    for name in ('get_latest_workout', 'get_next_workout'):
        if 'status' not in results[name] or 'session' not in results[name]:
            raise RuntimeError(f'{name} returned an unexpected shape.')
    if 'next_session' not in results['get_training_context'] or 'sessions' not in results['get_exercise_history']:
        raise RuntimeError('Context/history result is missing expected fields.')


def main():
    try:
        results = asyncio.run(run_smoke())
        validate_live_results(results)
    except Exception:
        print('FAIL: STDIO smoke test. Keep Uvicorn running and configure the Windows User coach read key. No credentials or tool error bodies were printed.', file=sys.stderr)
        return 1
    for name, _ in CASES:
        print(f'PASS {name}')
    summary = results['get_training_summary']
    print(json.dumps({'database_totals': summary['database_totals'],
                      'latest_workout_date': summary.get('latest_workout_date'),
                      'next_session': {key: (results['get_next_workout'].get('session') or {}).get(key)
                                       for key in ('workout_id', 'week', 'day')}}, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
