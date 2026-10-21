#!/usr/bin/env python3
"""Exploration contracts against real temporary sidecars, without production data or a listening socket."""
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import board_lib as lib
from server import Handler


class ExploreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.doc = self.root / "parent.md"
        self.doc.write_text("# Root\n\nA paragraph for exploration.\n")
        self.cp, self.rp = lib.sidecar_paths(str(self.doc))
        self.reg = {"root": {"path": str(self.doc), "title": "Root"}}
        self.patchers = [patch.object(lib, "load_registry", lambda: self.reg),
                         patch.object(lib, "SNAPDIR", str(self.root / "snapshots"))]
        for p in self.patchers:
            p.start()

    def tearDown(self):
        for p in self.patchers:
            p.stop()
        self.temp.cleanup()

    def request(self, path, body=None):
        handler = object.__new__(Handler)
        handler.path = path
        result = []
        handler._send = lambda status, payload, *args: result.append((status, payload))
        if body is None:
            handler.do_GET()
        else:
            data = json.dumps(body).encode()
            handler.headers = {"Content-Length": str(len(data))}
            handler.rfile = io.BytesIO(data)
            handler.do_POST()
        return result[0]

    def comment(self, text="A question", **extra):
        status, result = self.request("/api/comment", {"doc": "root", "kind": "doc", "text": text, **extra})
        self.assertEqual(status, 200)
        return result["id"]

    def test_private_and_drafts_never_in_tree_or_pending(self):
        self.comment("PRIVATE-NOTE", kind="note")
        self.comment("PRIVATE-DRAFT", intent="explore")
        cid = self.comment("Public branch", intent="explore", send=True)
        self.request("/api/followup", {"doc": "root", "id": cid, "text": "PRIVATE-FOLLOWUP"})
        status, tree = self.request("/api/explore/root")
        self.assertEqual(status, 200)
        self.assertEqual([b["id"] for b in tree["branches"]], [cid])
        self.assertNotIn("PRIVATE", json.dumps(tree))
        pending = lib.pending(self.reg)
        self.assertEqual(pending[0]["intent"], "explore")
        self.assertNotIn("PRIVATE", json.dumps(pending))
        self.assertEqual(self.doc.read_text(), "# Root\n\nA paragraph for exploration.\n")

    def test_promote_preserves_answered_and_is_idempotent(self):
        cid = self.comment(send=True)
        lib.append_event(self.rp, {"type": "answer", "re": cid, "ts": lib.now_ts(), "summary": "Done"})
        for _ in range(2):
            self.assertEqual(self.request("/api/explore", {"doc": "root", "id": cid})[0], 200)
        th = lib.fold_threads(str(self.doc))[0][0]
        self.assertEqual(th["status"], "answered")
        self.assertEqual(th["intent"], "explore")
        self.assertEqual(lib.pending(self.reg), [])
        self.assertEqual(sum(e["type"] == "explore" for e in lib.read_events(self.cp)), 1)
        for target in [self.comment(), self.comment(kind="note"), "c-9999"]:
            self.assertEqual(self.request("/api/explore", {"doc": "root", "id": target})[0], 409)
        self.assertEqual(self.request("/api/comment", {"doc": "root", "kind": "note", "text": "x", "intent": "explore"})[0], 400)

    def test_send_one_followup_leaves_other_drafts_private(self):
        cid = self.comment(send=True)
        lib.append_event(self.rp, {"type": "answer", "re": cid, "ts": lib.now_ts(), "summary": "Done"})
        self.request("/api/resolve", {"doc": "root", "id": cid})
        one = self.request("/api/followup", {"doc": "root", "id": cid, "text": "Send only me"})[1]["id"]
        self.request("/api/followup", {"doc": "root", "id": cid, "text": "PRIVATE-DRAFT"})
        for _ in range(2):
            self.assertEqual(self.request("/api/followup/send", {"doc": "root", "id": one})[0], 200)
        self.assertEqual(sum(e["type"] == "deliver" for e in lib.read_events(self.cp)), 1)
        pending = lib.pending(self.reg)
        self.assertEqual(pending[0]["status"], "open")
        self.assertIn("Send only me", json.dumps(pending))
        self.assertNotIn("PRIVATE-DRAFT", json.dumps(pending))
        self.assertEqual(self.request("/api/followup/send", {"doc": "root", "id": "f-missing"})[0], 409)

    def test_legacy_subdocs_family_and_broken_parent_graph(self):
        cid = self.comment(send=True)
        self.reg["child"] = {"path": str(self.doc), "title": "Child", "parent": "root", "born": {"doc": "root", "cid": cid}}
        self.reg["grandchild"] = {"path": str(self.root / "missing.md"), "parent": "child"}
        self.reg["other"] = {"path": str(self.root / "unrelated.md")}
        tree = lib.exploration_family(self.reg, "child")
        self.assertEqual(tree["root"], "root")
        self.assertEqual({d["id"] for d in tree["docs"]}, {"root", "child", "grandchild"})
        self.assertEqual(tree["branches"][0]["id"], cid)
        self.assertFalse(next(d for d in tree["docs"] if d["id"] == "grandchild")["exists"])
        self.reg["root"]["parent"] = "grandchild"
        self.assertEqual(len(lib.exploration_family(self.reg, "child")["docs"]), 3)
        self.reg["root"]["parent"] = "not-registered"
        self.assertEqual(lib.exploration_family(self.reg, "child")["root"], "root")
        self.assertEqual(self.request("/api/explore/not-registered")[0], 404)


if __name__ == "__main__":
    unittest.main()
