"""Validate the portable manifests and optionally call all tools via the launcher."""
import argparse
import asyncio
import importlib.util
import json
import os
from pathlib import Path
import sys
import urllib.request

import jsonschema
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = Path(__file__).resolve().parents[1]
CASES = (
    ('get_training_summary', {}),
    ('get_training_context', {'history_sessions': 3}),
    ('get_latest_workout', {}),
    ('get_next_workout', {}),
    ('get_exercise_history', {'exercise_id': 2, 'history_sessions': 5}),
    ('search_exercises', {'query': 'bench press'}),
)


async def smoke(plugin_root, config):
    entry = config['mcpServers']['next-set']
    expand = lambda value: value.replace('${PLUGIN_ROOT}', str(plugin_root))
    params = StdioServerParameters(
        command=entry['command'], args=[expand(arg) for arg in entry['args']],
        cwd=expand(entry['cwd']), env=entry['env'],
    )
    results = {}
    with open(os.devnull, 'w') as error_log:
        async with stdio_client(params, errlog=error_log) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                listing = await session.list_tools()
                assert {tool.name for tool in listing.tools} == {name for name, _ in CASES}
                assert all(tool.annotations and tool.annotations.read_only_hint for tool in listing.tools)
                for name, arguments in CASES:
                    result = await session.call_tool(name, arguments, read_timeout_seconds=20)
                    if result.is_error:
                        raise RuntimeError('Tool call failed; details suppressed.')
                    payload = result.structured_content
                    if payload is None:
                        payload = json.loads(next(block.text for block in result.content if block.type == 'text'))
                    results[name] = payload
                    tool = next(tool for tool in listing.tools if tool.name == name)
                    if tool.output_schema:
                        jsonschema.validate(payload, tool.output_schema)
                    json.dumps(payload, allow_nan=False)
                    if json.loads(result.content[0].text) != payload:
                        raise RuntimeError('Text and structured results disagree.')
    spec = importlib.util.spec_from_file_location('coach_smoke_validation', ROOT / 'mcp/smoke_test.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.validate_live_results(results)
    for name, _ in CASES:
        print('PASS', name)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--plugin-root', type=Path, default=ROOT / 'plugins/next-set')
    parser.add_argument('--smoke', action='store_true')
    args = parser.parse_args()
    plugin_root = args.plugin_root.resolve()
    try:
        documents = {}
        for name in ('plugin', 'mcp'):
            document = json.loads((plugin_root / (name + '.json')).read_text(encoding='utf-8'))
            schema_url = f'https://agent-plugins.org/schemas/1.0.0/{name}.schema.json'
            assert document['$schema'] == schema_url
            with urllib.request.urlopen(schema_url, timeout=20) as response:
                schema = json.load(response)
            jsonschema.Draft202012Validator(schema).validate(document)
            documents[name] = document
            print('PASS', name + '.json schema')
        assert documents['plugin']['name'] == 'next-set'
        assert set(documents['mcp']['mcpServers']) == {'next-set'}
        assert documents['mcp']['mcpServers']['next-set']['type'] == 'stdio'
        assert (plugin_root / 'skills/coach/SKILL.md').is_file()
        if args.smoke:
            asyncio.run(smoke(plugin_root, documents['mcp']))
    except Exception:
        print('FAIL: plugin validation. Check schemas, local preparation, and the running Coach API. Error details suppressed.', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
