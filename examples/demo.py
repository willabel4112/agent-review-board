#!/usr/bin/env python3
"""Serve a disposable, synthetic review. No production data is loaded."""
from pathlib import Path
import os
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import board_lib as lib
from server import Handler, ThreadingHTTPServer


def seed(root):
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    lib.REGISTRY = str(root / 'registry.local.json')
    lib._LEGACY_REGISTRY = str(root / 'no-legacy.json')
    lib.CLAIMS = str(root / 'claims.local.json')
    lib.SNAPDIR = str(root / '.snapshots')
    os.environ['RB_HOST'] = 'demo'
    doc = root / 'research-plan.md'
    child = root / 'sampling.md'
    current = Path(__file__).with_name('research-plan.md').read_text(encoding="utf-8")
    old = current.replace(
        'The evaluation includes exact-name lookups, paraphrased questions, ambiguous\n'
        'queries, and questions whose answer is absent. Report each group separately.\n'
        'Keep the source snippets so the human can inspect why a result was judged useful.',
        'The evaluation includes exact-name lookups and paraphrased questions.\n'
        'Report a single average score. Keep the source snippets so the human can\n'
        'inspect why a result was judged useful.')
    doc.write_text(old, encoding="utf-8", newline="\n")
    child.write_text(Path(__file__).with_name('sampling.md').read_text(encoding="utf-8"), encoding="utf-8", newline="\n")
    lib.save_registry({
        'research': {'path': str(doc), 'title': 'Choosing a local search tool',
                     'question': 'Can a review turn a plausible plan into a better decision?', 'emoji': '🔎'},
        'sampling': {'path': str(child), 'title': 'How should we choose the questions?',
                     'parent': 'research', 'born': {'doc': 'research', 'cid': 'c-0002'}, 'emoji': '🌱'}
    })
    before = lib.snapshot_put('research', old)
    after = lib.snapshot_put('research', current)
    cp, rp = lib.sidecar_paths(str(doc))
    blocks = lib.split_blocks(old)
    success = next(b for b in blocks if b['md'].startswith('The evaluation includes'))
    sample = next(b for b in blocks if b['md'].startswith('How should we select'))
    anchor = lambda b: {'hash': b['hash'], 'slug': b['slug'], 'quote': lib.normalize(b['md'])[:120], 'rev': before}
    ts = 1800000000.0  # Fixed fictional timeline, never copied from usage records.
    lib.append_event(cp, {'type': 'rev', 'sha': before, 'ts': ts, 'by': 'server'})
    lib.append_event(cp, {'type': 'comment', 'id': 'c-0001', 'ts': ts+1, 'author': 'Reviewer',
                         'kind': 'block', 'anchor': anchor(success),
                         'text': 'An average could hide bad failures. Please include ambiguous and absent-answer cases, then report the groups separately.'})
    lib.append_event(cp, {'type': 'submit', 'batch': 1, 'ids': ['c-0001'], 'rev': before, 'ts': ts+2})
    lib.append_event(cp, {'type': 'comment', 'id': 'c-0002', 'ts': ts+3, 'author': 'Reviewer',
                         'kind': 'block', 'anchor': anchor(sample), 'sent': True, 'intent': 'explore',
                         'text': 'How can we choose the question set before seeing the results?'})
    doc.write_text(current, encoding="utf-8", newline="\n")
    lib.append_event(rp, {'type': 'rev', 'sha': after, 'ts': ts+10, 'by': 'agent'})
    lib.append_event(rp, {'type': 'answer', 're': 'c-0001', 'ts': ts+11, 'mode': 'edit', 'rev': after,
                         'summary': 'Added ambiguous and absent-answer cases; the plan now reports each group separately.'})
    lib.append_event(rp, {'type': 'answer', 're': 'c-0002', 'ts': ts+6, 'mode': 'reply', 'rev': before,
                         'summary': 'Define the question types in advance. The child document explains the sampling rule.', 'doc_ref': 'sampling.md'})
    lib.append_event(cp, {'type': 'followup', 'id': 'f-0001', 're': 'c-0002', 'ts': ts+7,
                         'sent': True, 'author': 'Reviewer', 'text': 'Does a small matrix make the result representative?'})
    lib.append_event(rp, {'type': 'reply', 'id': 'r-0001', 're': 'c-0002', 'ts': ts+8, 'author': 'Demo agent',
                         'text': 'No. It exposes different failure types, but sample size and collection bias remain limitations.'})
    lib.append_event(rp, {'type': 'answer', 're': 'c-0002', 'ts': ts+9, 'mode': 'reply', 'rev': before,
                         'summary': 'A useful test structure is not a guarantee of representativeness.', 'doc_ref': 'sampling.md'})
    return root


if __name__ == '__main__':
    with tempfile.TemporaryDirectory(prefix='review-board-demo-') as directory:
        seed(directory)
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        print(f'http://127.0.0.1:{server.server_port}/d/research', flush=True)
        print(f'Agent CLI environment: RB_DATA_DIR={directory} RB_PORT={server.server_port}', flush=True)
        print('Synthetic data only. Ctrl-C removes the demo data.', flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()
