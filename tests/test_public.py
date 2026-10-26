#!/usr/bin/env python3
"""Public-package integration checks: identities, storage, real CLI and HTTP."""
from pathlib import Path
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import board_lib as lib
from examples.demo import seed
from server import Handler, ThreadingHTTPServer


class PublicPackage(unittest.TestCase):
    def test_native_identities_are_distinct(self):
        keys = ('RB_AGENT_SESSION', 'RB_AGENT_HARNESS', 'RB_AGENT_NAME',
                'CODEX_THREAD_ID', 'CLAUDE_CODE_SESSION_ID')
        clean = {k: v for k, v in os.environ.items() if k not in keys}
        for extra, session, harness in [
            ({}, '', 'unknown'),
            ({'CODEX_THREAD_ID': 'synthetic-codex'}, 'synthetic-codex', 'codex'),
            ({'CLAUDE_CODE_SESSION_ID': 'synthetic-claude'}, 'synthetic-claude', 'claude-code'),
            ({'RB_AGENT_SESSION': 'synthetic-custom', 'RB_AGENT_HARNESS': 'custom',
              'CODEX_THREAD_ID': 'synthetic-codex'}, 'synthetic-custom', 'custom')
        ]:
            with self.subTest(harness=harness), patch.dict(os.environ, {**clean, **extra}, clear=True):
                ident = lib.agent_identity()
                self.assertEqual((ident['session'], ident['harness']), (session, harness))
                self.assertNotIn('model', ident)
                self.assertEqual(lib.sessions_index(), {})

    def test_real_cli_and_http_share_isolated_state(self):
        names = ('REGISTRY', '_LEGACY_REGISTRY', 'CLAIMS', 'SNAPDIR')
        saved = {name: getattr(lib, name) for name in names}
        try:
            with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {'RB_HOST': 'demo'}):
                seed(temp)
                with patch('socket.getfqdn', side_effect=AssertionError('Local startup must not need DNS')):
                    srv = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
                thread = threading.Thread(target=srv.serve_forever, daemon=True)
                thread.start()
                base = f'http://127.0.0.1:{srv.server_port}'
                env = dict(os.environ, RB_DATA_DIR=temp, RB_PORT=str(srv.server_port),
                           RB_AGENT_SESSION='synthetic-session', RB_AGENT_HARNESS='test-harness',
                           RB_AGENT_NAME='Test agent')

                def request(route, body=None):
                    req = Request(base+route, data=None if body is None else json.dumps(body).encode(),
                                  headers={'Content-Type': 'application/json'})
                    with urlopen(req, timeout=5) as response:
                        return json.load(response)

                def cli(*args):
                    return subprocess.check_output([sys.executable, str(ROOT/'boardctl.py'), *args],
                                                   env=env, text=True)
                try:
                    rounds = request('/api/rounds/research')
                    self.assertTrue(rounds)
                    folded = lib.fold_rounds(str(Path(temp)/'research-plan.md'))
                    self.assertIsNotNone(folded[0]['rev_before'])
                    self.assertNotEqual(folded[0]['rev_before'], folded[0]['rev_after'])
                    decoration = lib.decoration(str(Path(temp)/'research-plan.md'), 'research',
                                                lib.split_blocks((Path(temp)/'research-plan.md').read_text()))
                    self.assertIn('mod', decoration[0].values())
                    threads = request('/api/threads/research')['threads']
                    edited = next(t for t in threads if t['id'] == 'c-0001')
                    self.assertEqual(edited['anchor_status'], 'modified')
                    self.assertEqual(edited['answer']['mode'], 'edit')
                    family = request('/api/explore/research')
                    self.assertIn('sampling', json.dumps(family))
                    before = (Path(temp)/'research-plan.md').read_bytes()
                    for text, extra in [('draft only', {}), ('private note', {'kind': 'note'}),
                                        ('delivered question', {'send': True})]:
                        request('/api/comment', {'doc': 'research', 'text': text, **extra})
                    # Old peer files are inert; the local server exposes no peer API.
                    (Path(temp)/'peers.local.json').write_text(
                        '[{"name":"retired","url":"http://127.0.0.1:1"}]', encoding='utf-8')
                    with self.assertRaises(HTTPError) as retired:
                        request('/api/peers')
                    self.assertEqual(retired.exception.code, 404)
                    retired.exception.close()
                    pending = json.loads(cli('pending', '--doc', 'research', '--json'))
                    self.assertEqual([p['text'] for p in pending], ['delivered question'])
                    cid = pending[0]['id']
                    answer = Path(temp)/'answer.txt'
                    answer.write_text('A synthetic response.')
                    cli('reply', 'research', cid, '--file', str(answer), '--summary', 'A test conclusion')
                    self.assertEqual(json.loads(cli('pending', '--doc', 'research', '--json')), [])
                    returned = request('/api/threads/research')['threads']
                    answered = next(t for t in returned if t['id'] == cid)
                    self.assertEqual(answered['replies'][-1]['author'], 'Test agent')
                    self.assertEqual(answered['replies'][-1]['session'], 'synthetic-session')
                    self.assertEqual((Path(temp)/'research-plan.md').read_bytes(), before)
                    # A Unicode ID must work through all encoded browser routes.
                    uid = '中文 文档'
                    cli('register', str(Path(temp)/'sampling.md'), '--id', uid)
                    for route in ('doc', 'threads', 'rounds', 'revs', 'explore'):
                        request('/api/' + route + '/' + quote(uid, safe=''))
                    asset = Path(temp)/'资料 百分%.md'
                    asset.write_text('# UTF-8 asset\n', encoding='utf-8')
                    result = request('/api/answerdoc/' + quote(uid, safe='') + '/' + quote(asset.name))
                    self.assertEqual(result['blocks'][0]['md'], '# UTF-8 asset')
                    for path in ('../outside.md', 'C:stream.md', 'file.md:ads.md', r'..\outside.md'):
                        with self.assertRaises(HTTPError) as escaped:
                            request('/api/answerdoc/' + quote(uid, safe='') + '/' + quote(path, safe=''))
                        self.assertEqual(escaped.exception.code, 403)
                        escaped.exception.close()
                finally:
                    srv.shutdown()
                    srv.server_close()
                    thread.join(timeout=5)
        finally:
            for name, value in saved.items():
                setattr(lib, name, value)


if __name__ == '__main__':
    unittest.main()
