"""Release regressions: intentional dependencies and isolated launcher failure."""
import ast
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


def declared(path, seen=None):
    seen = set() if seen is None else seen
    path = path.resolve()
    if path in seen:
        return set()
    seen.add(path)
    result = set()
    for line in path.read_text().splitlines():
        line = line.split('#', 1)[0].strip()
        if line.startswith('-r '):
            result |= declared(path.parent / line[3:], seen)
        elif line and not line.startswith('-'):
            result.add(re.split(r'[<>=!~\[]', line)[0].lower())
    return result


class ReleaseTests(unittest.TestCase):
    def test_runtime_dependency_separation(self):
        self.assertEqual(declared(ROOT/'requirements.txt'), {'fastapi', 'uvicorn', 'pydantic'})
        self.assertEqual(declared(ROOT/'mcp/requirements-mcp.txt'), {'mcp', 'httpx', 'pydantic'})
        self.assertEqual(declared(ROOT/'adapters/biolayne/requirements.txt'),
                         {'fastapi', 'uvicorn', 'pydantic', 'requests'})

    def test_all_direct_external_imports_are_declared(self):
        local = {p.stem for p in ROOT.glob('*.py')} | {'adapters'}
        local |= {p.stem for p in (ROOT/'tests').glob('*.py')}
        scopes = [(ROOT.glob('*.py'), ROOT/'requirements.txt'),
                  ((ROOT/'mcp').glob('*.py'), ROOT/'mcp/requirements-mcp.txt'),
                  ((ROOT/'adapters/biolayne').rglob('*.py'), ROOT/'adapters/biolayne/requirements.txt'),
                  ((ROOT/'tests').glob('*.py'), ROOT/'requirements-test.txt'),
                  ((ROOT/'scripts').glob('*.py'), ROOT/'requirements-test.txt')]
        for paths, requirements in scopes:
            allowed = declared(requirements) | local | set(sys.stdlib_module_names)
            for path in paths:
                for node in ast.walk(ast.parse(path.read_text(encoding='utf-8-sig'))):
                    names = ([n.name.split('.')[0] for n in node.names] if isinstance(node, ast.Import)
                             else [node.module.split('.')[0]] if isinstance(node, ast.ImportFrom) and node.module else [])
                    for name in names:
                        self.assertIn(name, allowed, f'{path.name}: undeclared import {name}')

    @unittest.skipUnless(os.name == 'nt', 'Windows launcher')
    def test_explicit_root_does_not_fall_back_to_personal_binding(self):
        with tempfile.TemporaryDirectory() as temporary:
            result = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-File',
                str(ROOT/'plugins/next-set/scripts/start-mcp.ps1'), '-ProjectRoot', temporary,
                '-KeySource', 'process'], capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, '')
        self.assertIn('Configured maintained MCP server does not exist', result.stderr)
