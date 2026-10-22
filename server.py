#!/usr/bin/env python3
"""review-board server — http://127.0.0.1:8798 (端口已记账 the local service configuration).

stdlib only, 无依赖。写路径只 append comments.jsonl (人侧);
replies.jsonl 归 agent (boardctl.py), server 只读 — single-writer-per-file。
全部响应 no-store (day-1 规则: 新 HTML 配旧 JS = 静默死 tab)。
"""
import json
import os
import re
import posixpath
import time
import threading
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import board_lib as lib

PORT = int(os.environ.get("RB_PORT") or 8798)   # 生产固定 8798 (the local service configuration); 旁路测试实例用 RB_PORT
STATIC = os.path.join(lib.ROOT, "static")
EXPLORE_LOCK = threading.Lock()

ASSET_TYPES = {
    "png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
    "svg": "image/svg+xml", "gif": "image/gif", "webp": "image/webp",
    "html": "text/html; charset=utf-8", "pdf": "application/pdf",
    "md": "text/plain; charset=utf-8", "txt": "text/plain; charset=utf-8",
}


def _round_brief(r):
    return {"batch": r["batch"], "status": r["status"],
            "n_ids": len(r["ids"]), "n_answered": len(r["answered_ids"]),
            "edited_ids": r["edited_ids"], "approve_note": r["approve_note"]}


def _claim_view(docid, claims=None, now=None):
    """v2.3 认领 chip 数据: 机器本地 claims.local.json 的一条 + 活性判断."""
    claims = lib.load_claims() if claims is None else claims
    c = claims.get(docid)
    if not c:
        return None
    now = now or time.time()
    return dict(c, alive=lib.claim_alive(c, now), beat_ago=int(now - (c.get("beat") or 0)))


def _stats(docid, a, b):
    """两个版本间的块级改动计数; 缺快照就返回 None (只损失回看)."""
    ab, bb = lib.snapshot_blocks(docid, a), lib.snapshot_blocks(docid, b)
    if ab is None or bb is None:
        return None
    out = {"same": 0, "mod": 0, "add": 0, "del": 0}
    for op in lib.diff_blocks(ab, bb):
        out[op["op"]] = out.get(op["op"], 0) + 1
    return out


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        data = body if isinstance(body, bytes) else json.dumps(
            body, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _file(self, full, ctype):
        try:
            with open(full, "rb") as f:
                data = f.read()
        except OSError:
            return self._send(404, {"err": "not found"})
        return self._send(200, data, ctype)

    # ---------- GET ----------

    def do_GET(self):
        path, _, qs = self.path.partition("?")
        self.q = urllib.parse.parse_qs(qs)
        if path == "/":
            return self._file(os.path.join(STATIC, "index.html"),
                              "text/html; charset=utf-8")
        if path.startswith("/d/") or path.startswith("/x/"):
            # /x/<docid>/<rel> = 外链答案只读页 — 同一 viewer shell, app.js 按前缀进只读模式
            return self._file(os.path.join(STATIC, "viewer.html"),
                              "text/html; charset=utf-8")
        if path.startswith("/static/"):
            fn = posixpath.normpath(path[len("/static/"):])
            if fn.startswith("..") or fn.startswith("/"):
                return self._send(400, {"err": "bad path"})
            full = os.path.join(STATIC, fn)
            if not os.path.isfile(full):
                return self._send(404, {"err": "not found"})
            ctype = ("application/javascript" if fn.endswith(".js")
                     else "text/css" if fn.endswith(".css")
                     else "application/octet-stream")
            return self._file(full, ctype)
        if path.startswith("/api/asset/"):
            return self._asset(path[len("/api/asset/"):])
        if path.startswith("/api/answerdoc/"):
            return self._answerdoc(path[len("/api/answerdoc/"):])
        if path == "/api/docs":
            return self._docs()
        if path == "/api/peers":
            return self._peers()
        if path == "/api/search":
            return self._search()
        if path == "/favicon.svg" or path == "/favicon.ico":
            return self._favicon()
        if path.startswith("/api/doc/"):
            return self._doc(path[len("/api/doc/"):])
        if path.startswith("/api/threads/"):
            return self._threads(path[len("/api/threads/"):])
        if path.startswith("/api/explore/"):
            docid = urllib.parse.unquote(path[len("/api/explore/"):])
            reg = lib.load_registry()
            if docid not in reg:
                return self._send(404, {"err": "doc"})
            return self._send(200, lib.exploration_family(reg, docid))
        if path.startswith("/api/revs/"):
            return self._revs(path[len("/api/revs/"):])
        if path.startswith("/api/rounds/"):
            return self._rounds(path[len("/api/rounds/"):])
        if path.startswith("/api/diff/"):
            return self._diff(path[len("/api/diff/"):])
        return self._send(404, {"err": "route"})

    def _doc_path(self, docid):
        reg = lib.load_registry()
        return reg[docid]["path"] if docid in reg else None

    def _asset(self, rest):
        """doc 目录内相对资源; realpath 越界拒绝."""
        docid, _, rel = rest.partition("/")
        reg = lib.load_registry()
        if docid not in reg:
            return self._send(404, {"err": "doc"})
        base = os.path.dirname(os.path.realpath(reg[docid]["path"]))
        full = os.path.realpath(os.path.join(base, rel))
        if full != base and not full.startswith(base + os.sep):
            return self._send(403, {"err": "traversal"})
        if not os.path.isfile(full):
            return self._send(404, {"err": "not found"})
        ext = full.rsplit(".", 1)[-1].lower()
        return self._file(full, ASSET_TYPES.get(ext, "application/octet-stream"))

    def _answerdoc(self, rest):
        """外链答案只读渲染源 (契约 v2): doc 目录内 .md, jail 同 _asset;
        无 sidecar / 无装饰 / **不落快照**."""
        docid, _, rel = rest.partition("/")
        rel = urllib.parse.unquote(rel)
        reg = lib.load_registry()
        if docid not in reg:
            return self._send(404, {"err": "doc"})
        if not rel.endswith(".md"):
            return self._send(400, {"err": "Markdown only; use /api/asset for other resources"})
        base = os.path.dirname(os.path.realpath(reg[docid]["path"]))
        full = os.path.realpath(os.path.join(base, rel))
        if full != base and not full.startswith(base + os.sep):
            return self._send(403, {"err": "traversal"})
        md = lib.read_doc(full)
        if md is None:
            return self._send(404, {"err": "not found"})
        born = (reg[docid].get("born") or None)
        return self._send(200, {
            "rel": rel, "doc": docid, "mtime": os.path.getmtime(full),
            "blocks": lib.split_blocks(md), "born": born})

    def _docref_url(self, reg, docid, rel):
        """doc_ref 两态 (契约 v2): 命中已注册文档 → /d/<那个id> (可评论卫星);
        否则 → /x/<docid>/<rel> 只读页."""
        base = os.path.dirname(os.path.realpath(reg[docid]["path"]))
        full = os.path.realpath(os.path.join(base, rel))
        for oid, meta in reg.items():
            if os.path.realpath(meta["path"]) == full:
                return "/d/" + urllib.parse.quote(oid, safe="")
        return "/x/%s/%s" % (urllib.parse.quote(docid, safe=""),
                             urllib.parse.quote(rel))

    def _docs(self):
        out = []
        reg = lib.load_registry()
        claims = lib.load_claims()
        now = time.time()
        for docid, meta in reg.items():
            ths, _ = lib.fold_threads(meta["path"])
            counts = {}
            for th in ths:
                counts[th["status"]] = counts.get(th["status"], 0) + 1
            rounds = lib.fold_rounds(meta["path"])
            exists = os.path.isfile(meta["path"])
            cov = None
            if exists and not meta.get("archived"):
                md = lib.read_doc(meta["path"])
                if md is not None:
                    c = lib.coverage(meta["path"], docid, lib.split_blocks(md))
                    cov = {"n_seen": c["n_seen"], "n_total": c["n_total"]}
            out.append({"id": docid, "title": meta.get("title") or docid,
                        "path": meta["path"], "counts": counts,
                        "round": _round_brief(rounds[-1]) if rounds else None,
                        "parent": meta.get("parent"), "born": meta.get("born"),
                        "question": meta.get("question"),
                        "emoji": meta.get("emoji"),
                        "draft": bool(meta.get("draft")),
                        "archived": bool(meta.get("archived")),
                        "exists": exists,
                        "claim": _claim_view(docid, claims, now),   # v2.3 值守
                        "coverage": cov})                            # v2.3 看过 n/N
        return self._send(200, {"docs": out, "host": lib.machine_name()})

    def _search(self):
        # v2.4 F14: 精确子串 (归一: 全角/大小写/空白), 含 archived; 语义版后置
        q = (self.q.get("q") or [""])[0]
        doc = (self.q.get("doc") or [""])[0] or None
        if len(lib.search_norm(q)) < 2:
            return self._send(400, {"err": "Enter at least 2 characters"})
        return self._send(200, {"q": q, "hits": lib.search(q, doc)})

    def _favicon(self):
        """v2.4 F12: 服务端合成 SVG favicon — 画框底图; ?doc= 加 emoji (卫星=母 emoji 垫底).
        Edge 竖 tabs: favicon 即身份 — 画框认出是板, emoji 区分文档, 母子有血缘."""
        docid = (self.q.get("doc") or [""])[0]
        reg = lib.load_registry()
        emoji = pemoji = None
        color = "#60a5fa"
        if docid and docid in reg:
            emoji = reg[docid].get("emoji")
            color = reg[docid].get("color") or color   # v2.5 F17: 画框色可改
            par = reg[docid].get("parent")
            if par and par in reg:
                pemoji = reg[par].get("emoji")
        parts = ['<rect x="2" y="6" width="28" height="24" rx="5" fill="#171c23" '
                 'stroke="%s" stroke-width="2.6"/>' % color,
                 '<rect x="11" y="2" width="10" height="7" rx="2.5" fill="%s"/>' % color]
        if not emoji:   # 首页/无 emoji: 画框里三条评论线
            parts.append('<path d="M8 14h16M8 19h16M8 24h10" stroke="%s" '
                         'stroke-width="2.2" stroke-linecap="round" opacity=".85"/>' % color)
        else:
            if pemoji:
                parts.append('<text x="8" y="28" font-size="11" opacity=".5" '
                             'text-anchor="middle">%s</text>' % pemoji)
                parts.append('<text x="19" y="25" font-size="14" '
                             'text-anchor="middle">%s</text>' % emoji)
            else:
                parts.append('<text x="16" y="26" font-size="16" '
                             'text-anchor="middle">%s</text>' % emoji)
        svg = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32">'
               + "".join(parts) + "</svg>")
        return self._send(200, svg.encode("utf-8"), "image/svg+xml")

    def _peers(self):
        """v2.3 联合首页: 逐 peer 抓 /api/docs (超时 1 s), 不通就 ok=false — 状态不搬家, 只并排列."""
        out = []
        for peer in lib.load_peers():
            url = peer["url"].rstrip("/")
            item = {"name": peer.get("name") or url, "url": url, "ok": False, "docs": []}
            try:
                with urllib.request.urlopen(url + "/api/docs", timeout=1.0) as r:
                    j = json.loads(r.read().decode("utf-8"))
                item["docs"] = j.get("docs") or []
                item["host"] = j.get("host")
                item["ok"] = True
            except (urllib.error.URLError, OSError, ValueError) as e:
                item["err"] = str(getattr(e, "reason", e))[:120]
            out.append(item)
        return self._send(200, {"peers": out})

    def _doc(self, docid):
        reg = lib.load_registry()
        if docid not in reg:
            return self._send(404, {"err": "doc"})
        p = reg[docid]["path"]
        md = lib.read_doc(p)
        if md is None:
            return self._send(500, {"err": "unreadable"})
        # 快照缓存在**读**时填充: 保证"他此刻看的这一版"将来一定取得回来
        # (评论的 anchor.rev 指的就是它)。只写内容寻址缓存, 源 md 仍只读。
        sha = lib.snapshot_put(docid, md)
        blocks = lib.split_blocks(md)
        chg, causes, rnd, stats, base = lib.decoration(p, docid, blocks)
        for b in blocks:
            b["chg"] = chg.get(b["i"])
        born = reg[docid].get("born")
        if born and born.get("doc") in reg:
            born = dict(born, title=reg[born["doc"]].get("title") or born["doc"])
        return self._send(200, {
            "id": docid, "title": reg[docid].get("title") or docid,
            "path": p, "mtime": os.path.getmtime(p), "sha": sha,
            "blocks": blocks, "causes": causes, "chgstats": stats,
            "round": _round_brief(rnd) if rnd else None, "born": born,
            "question": reg[docid].get("question"),
            "emoji": reg[docid].get("emoji"),
            "color": reg[docid].get("color"),
            "draft": bool(reg[docid].get("draft")),
            "archived": bool(reg[docid].get("archived")),
            "baseline": base,                                   # v2.3 装饰基线 (最后发言 rev)
            "coverage": lib.coverage(p, docid, blocks),         # v2.3 看过 n/N + 未看块
            "claim": _claim_view(docid),                        # v2.3 值守 session
            "host": lib.machine_name(),
        })

    def _threads(self, docid):
        reg = lib.load_registry()
        if docid not in reg:
            return self._send(404, {"err": "doc"})
        p = reg[docid]["path"]
        ths, nbatch = lib.fold_threads(p)
        md = lib.read_doc(p)
        blocks = lib.split_blocks(md) if md is not None else []
        # 卫星反查: 哪些注册文档出身于本文档的哪条评论 (registry born, 契约 v2)
        spawned = {}
        for oid, meta in reg.items():
            b = meta.get("born") or {}
            if b.get("doc") == docid and b.get("cid"):
                spawned.setdefault(b["cid"], []).append(
                    {"doc": oid, "title": meta.get("title") or oid})
        sidx = lib.sessions_index()                 # v2.3: turn.session -> name@host / sid8
        host = lib.machine_name()
        for th in ths:
            for turn in th["turns"]:
                if turn.get("session"):
                    turn["session_label"] = lib.session_label(turn["session"], sidx, host)
            th["block"], th["anchor_status"] = lib.resolve_anchor_v1(
                th.get("anchor"), blocks, docid)
            # block_hash = 客户端滚动前的核验票据 (修 client/server 索引竞态)
            th["block_hash"] = (blocks[th["block"]]["hash"]
                                if th["block"] is not None and th["block"] < len(blocks)
                                else None)
            sel = (th.get("anchor") or {}).get("sel")
            if sel and th["block"] is not None:
                th["sel_pos"], th["sel_status"] = lib.resolve_sel(
                    sel, blocks[th["block"]]["md"])
            for turn in th["turns"]:
                if turn.get("doc_ref"):
                    turn["doc_ref_url"] = self._docref_url(reg, docid, turn["doc_ref"])
            for r in th["replies"]:
                if r.get("doc_ref"):
                    r["doc_ref_url"] = self._docref_url(reg, docid, r["doc_ref"])
            if (th.get("answer") or {}).get("doc_ref"):
                th["answer"]["doc_ref_url"] = self._docref_url(
                    reg, docid, th["answer"]["doc_ref"])
            if th["id"] in spawned:
                th["spawned"] = spawned[th["id"]]
            g = th.get("graduated")
            if g and g.get("dest") in reg:   # dest 恰好是注册 docid → 深链
                g["url"] = "/d/" + urllib.parse.quote(g["dest"], safe="")
        rounds = lib.fold_rounds(p)
        cov = lib.coverage(p, docid, blocks) if md is not None else None
        return self._send(200, {"threads": ths, "nbatch": nbatch,
                                "round": _round_brief(rounds[-1]) if rounds else None,
                                "sha": lib.doc_sha(md) if md is not None else None,
                                "mtime": os.path.getmtime(p) if os.path.isfile(p) else 0,
                                "coverage": ({"n_seen": cov["n_seen"], "n_total": cov["n_total"]}
                                             if cov else None),
                                "claim": _claim_view(docid)})

    def _revs(self, docid):
        p = self._doc_path(docid)
        if p is None:
            return self._send(404, {"err": "doc"})
        revs = [e for e in lib.all_events(p) if e.get("type") == "rev"]
        md = lib.read_doc(p)
        return self._send(200, {"revs": revs,
                                "sha": lib.doc_sha(md) if md is not None else None})

    def _rounds(self, docid):
        p = self._doc_path(docid)
        if p is None:
            return self._send(404, {"err": "doc"})
        out = []
        for r in lib.fold_rounds(p):
            r = dict(r)
            r["stats"] = _stats(docid, r["rev_before"],
                                r["approve_rev"] or r["rev_after"])
            out.append(r)
        return self._send(200, {"rounds": out})

    def _diff(self, docid):
        """?a=<sha|round:N> &b=<sha|now>; 只回改动块的 md (same 块不回正文)."""
        p = self._doc_path(docid)
        if p is None:
            return self._send(404, {"err": "doc"})
        a = (self.q.get("a") or [""])[0]
        b = (self.q.get("b") or ["now"])[0]
        rounds = lib.fold_rounds(p)
        if not a or a == "baseline":
            # v2.3: 缺省基线 = 最后一次人类发言的 rev (无回合的纯即发文档也有改动页)
            a = lib.baseline_rev(lib.all_events(p)) or ""
            if not a:
                return self._send(409, {"err": "No baseline yet; submit a comment or mark this document as read first"})
        if a.startswith("round:"):
            r = next((x for x in rounds if str(x["batch"]) == a[6:]), None)
            if r is None:
                return self._send(404, {"err": "round"})
            a, b = r["rev_before"] or "", (b if b != "now" else
                                           (r["approve_rev"] or r["rev_after"] or "now"))
        ab = lib.snapshot_blocks(docid, a)
        if ab is None:
            return self._send(409, {"err": "Previous snapshot missing; historical display is unavailable, decisions are preserved"})
        if b in ("now", "", None):
            md = lib.read_doc(p)
            bb = lib.split_blocks(md) if md is not None else []
            b = lib.doc_sha(md) if md is not None else ""
        else:
            bb = lib.snapshot_blocks(docid, b)
            if bb is None:
                return self._send(409, {"err": "Snapshot for this version is unavailable"})
        ops = []
        for op in lib.diff_blocks(ab, bb):
            o = dict(op)
            if op["op"] != "same":
                if op["old"] is not None:
                    o["old_md"] = ab[op["old"]]["md"]
                    o["slug"] = ab[op["old"]]["slug"]
                if op["new"] is not None:
                    o["new_md"] = bb[op["new"]]["md"]
                    o["slug"] = bb[op["new"]]["slug"]
            ops.append(o)
        return self._send(200, {"a": a, "b": b, "ops": ops,
                                "n_old": len(ab), "n_new": len(bb)})

    # ---------- POST (人侧写路径, 只碰 comments.jsonl) ----------

    def do_POST(self):
        path = self.path.split("?")[0]
        try:
            n = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(n) or b"{}")
        except (ValueError, TypeError):
            return self._send(400, {"err": "json"})
        reg = lib.load_registry()
        docid = body.get("doc")
        if docid not in reg:
            return self._send(404, {"err": "doc"})
        doc_path = reg[docid]["path"]
        cpath, _ = lib.sidecar_paths(doc_path)
        now = time.time()

        if path == "/api/comment":
            intent = body.get("intent")
            if intent not in (None, "explore") or (intent and body.get("kind") == "note"):
                return self._send(400, {"err": "bad intent"})
            text = (body.get("text") or "").strip()
            if not text:
                return self._send(400, {"err": "empty"})
            anchor = body.get("anchor")
            sel = (anchor or {}).get("sel")
            if sel is not None:
                # 契约 v2: sel = {text ≤200 (normalize 后), 0<=start<end}
                if (not isinstance(sel, dict)
                        or not (sel.get("text") or "").strip()
                        or not isinstance(sel.get("start"), int)
                        or not isinstance(sel.get("end"), int)
                        or not 0 <= sel["start"] < sel["end"]):
                    return self._send(400, {"err": "bad sel"})
                sel["text"] = " ".join(sel["text"].split())[:200]
            sha = lib.ensure_rev(doc_path, docid, cpath, "commenting", "server")
            if anchor is not None and not anchor.get("rev"):
                anchor["rev"] = sha          # 评论永远记下它是对着哪一版写的
            now = lib.now_ts()
            cid = lib.next_id(lib.read_events(cpath), "c")
            kind = body.get("kind") or ("block" if anchor else "doc")
            if kind not in ("block", "doc", "note"):
                return self._send(400, {"err": "bad kind"})
            ev = {
                "type": "comment", "id": cid, "ts": now,
                "author": body.get("author") or "Reviewer",
                "kind": kind, "anchor": anchor, "text": text,
            }
            if intent:
                ev["intent"] = intent
            if body.get("send") and kind != "note":
                ev["sent"] = True            # 即发单条: 生而 open, 永不入回合 (note 永不发)
            lib.append_event(cpath, ev)
            return self._send(200, {"ok": True, "id": cid,
                                    "sent": bool(body.get("send"))})

        if path == "/api/explore":
            cid = body.get("id")
            with EXPLORE_LOCK:
                ths, _ = lib.fold_threads(doc_path)
                th = next((t for t in ths if t["id"] == cid), None)
                if not th or th["status"] == "draft" or th["kind"] == "note":
                    return self._send(409, {"err": "Only delivered comments can become exploration branches"})
                if th.get("intent") != "explore":
                    lib.append_event(cpath, {"type": "explore", "re": cid, "ts": lib.now_ts()})
            return self._send(200, {"ok": True, "id": cid})

        if path == "/api/followup/send":
            fid = body.get("id")
            with EXPLORE_LOCK:
                ths, _ = lib.fold_threads(doc_path)
                pair = next(((th, turn) for th in ths for turn in th["turns"]
                             if turn.get("kind") == "followup" and turn.get("id") == fid), None)
                if not pair or pair[0]["kind"] == "note" or pair[0]["status"] == "draft":
                    return self._send(409, {"err": "unknown followup"})
                if pair[1].get("draft"):
                    sha = lib.ensure_rev(doc_path, docid, cpath, "delivering followup", "server")
                    lib.append_event(cpath, {"type": "deliver", "ts": lib.now_ts(), "fids": [fid], "rev": sha})
            return self._send(200, {"ok": True, "id": fid})

        if path == "/api/followup":
            # 契约 v2: 人类 thread 追问; sent 即发 = 隐式 reopen ⇒ 唤醒 agent
            cid = body.get("id")
            text = (body.get("text") or "").strip()
            if not text:
                return self._send(400, {"err": "empty"})
            ths, _ = lib.fold_threads(doc_path)
            th = next((t for t in ths if t["id"] == cid), None)
            if th is None:
                return self._send(409, {"err": "unknown id"})
            if th["status"] == "draft":
                return self._send(409, {"err": "This draft has not been delivered; edit the draft directly"})
            sha = lib.ensure_rev(doc_path, docid, cpath, "followup", "server")
            fid = lib.next_id(lib.read_events(cpath), "f")
            ev = {"type": "followup", "id": fid, "re": cid, "ts": lib.now_ts(),
                  "author": body.get("author") or "Reviewer",
                  "text": text, "rev": sha}
            if body.get("send"):
                ev["sent"] = True
            lib.append_event(cpath, ev)
            return self._send(200, {"ok": True, "id": fid,
                                    "sent": bool(body.get("send"))})

        if path in ("/api/comment/edit", "/api/comment/drop"):
            cid = body.get("id") or ""
            ths, _ = lib.fold_threads(doc_path)
            if cid.startswith("f-"):
                # v2: draft followup 同样可改可撤 (draft-only 规则原样)
                turn = next((u for t in ths for u in t["turns"]
                             if u.get("id") == cid and u["kind"] == "followup"), None)
                if turn is None:
                    return self._send(409, {"err": "unknown id"})
                if not turn.get("draft"):
                    return self._send(409, {"err": "Already delivered; add a follow-up instead of editing or withdrawing it"})
            else:
                th = next((t for t in ths if t["id"] == cid), None)
                if th is None:
                    return self._send(409, {"err": "unknown id"})
                if th["status"] != "draft":
                    # 已提交的发言不可撤不可改 (契约 v1)
                    return self._send(409, {"err": "Already submitted; add a new comment instead of editing or withdrawing it"})
            if path.endswith("edit"):
                text = (body.get("text") or "").strip()
                if not text:
                    return self._send(400, {"err": "empty"})
                lib.append_event(cpath, {"type": "edit", "re": cid,
                                         "ts": now, "text": text})
            else:
                lib.append_event(cpath, {"type": "drop", "re": cid, "ts": now})
            return self._send(200, {"ok": True})

        if path == "/api/submit":
            ths, nbatch = lib.fold_threads(doc_path)
            ids = [t["id"] for t in ths if t["status"] == "draft" and t["kind"] != "note"]
            fids = [u["id"] for t in ths for u in t["turns"]
                    if u["kind"] == "followup" and u.get("draft")]
            if not ids and not fids:
                return self._send(400, {"err": "no drafts"})
            lib.ensure_rev(doc_path, docid, cpath,
                           "submit batch %d" % (nbatch + 1) if ids else "deliver",
                           "server")
            batch = None
            now2 = lib.now_ts()
            if ids:
                batch = nbatch + 1
                lib.append_event(cpath, {"type": "submit", "batch": batch,
                                         "ts": now2, "ids": ids})
            if fids:
                # deliver 不开回合 (契约 v2: submit 冻结, 批号↔回合 1:1)
                lib.append_event(cpath, {"type": "deliver", "ts": now2 + 0.001,
                                         "fids": fids})
            return self._send(200, {"ok": True, "batch": batch,
                                    "ids": ids, "fids": fids})

        if path == "/api/meta":
            # v2.5 F17: 页面直改 emoji / 画框色 (registry 板侧元数据)
            em = (body.get("emoji") or "").strip()
            col = (body.get("color") or "").strip()
            if col and not re.match(r"^#[0-9a-fA-F]{6}$", col):
                return self._send(400, {"err": "Color must use #RRGGBB"})
            if em:
                if len(em) > 8:
                    return self._send(400, {"err": "Emoji is too long"})
                reg[docid]["emoji"] = em
            if col:
                reg[docid]["color"] = col
            lib.save_registry(reg)
            return self._send(200, {"ok": True, "emoji": reg[docid].get("emoji"),
                                    "color": reg[docid].get("color")})

        if path == "/api/seen":
            # v2.3「看完了」: 记下此刻这版全部块 hash = 看过 (mode b 一键); 沉默仍 = 没看
            md = lib.read_doc(doc_path)
            if md is None:
                return self._send(500, {"err": "unreadable"})
            cur = lib.doc_sha(md)
            if body.get("rev") and body["rev"] != cur:
                return self._send(409, {"err": "The document changed; review the refreshed version and try again"})
            sha = lib.ensure_rev(doc_path, docid, cpath, "seen", "server")
            blocks = lib.split_blocks(md)
            lib.append_event(cpath, {"type": "seen", "ts": lib.now_ts(), "rev": sha,
                                     "hashes": [b["hash"] for b in blocks]})
            cov = lib.coverage(doc_path, docid, blocks)
            return self._send(200, {"ok": True, "rev": sha,
                                    "n_seen": cov["n_seen"], "n_total": cov["n_total"]})

        if path == "/api/approve":
            rounds = lib.fold_rounds(doc_path)
            if not rounds:
                return self._send(409, {"err": "No review round yet"})
            batch = body.get("batch") or rounds[-1]["batch"]
            r = next((x for x in rounds if x["batch"] == batch), None)
            if r is None:
                return self._send(409, {"err": "unknown batch"})
            if r["status"] == "approved":
                return self._send(409, {"err": "already approved"})
            sha = lib.ensure_rev(doc_path, docid, cpath,
                                 "approve batch %s" % batch, "server")
            lib.append_event(cpath, {"type": "approve", "batch": batch,
                                     "ts": lib.now_ts(), "rev": sha,
                                     "note": (body.get("note") or "").strip() or None})
            return self._send(200, {"ok": True, "batch": batch, "rev": sha})

        if path == "/api/reject":
            # 打回不是新事件类型: 一条 kind=reject 的 doc 级评论 + 立即成批
            # ⇒ pending/wait/answer 整条唤醒链零改动复用 (契约 v1)
            note = (body.get("note") or "").strip()
            if not note:
                return self._send(400, {"err": "Please explain what needs to change"})
            ths, nbatch = lib.fold_threads(doc_path)
            drafts = [t["id"] for t in ths if t["status"] == "draft"]
            rounds = lib.fold_rounds(doc_path)
            batch = body.get("batch") or (rounds[-1]["batch"] if rounds else None)
            sha = lib.ensure_rev(doc_path, docid, cpath, "reject", "server")
            now = lib.now_ts()
            cid = lib.next_id(lib.read_events(cpath), "c")
            lib.append_event(cpath, {
                "type": "comment", "id": cid, "ts": now, "author": "Reviewer",
                "kind": "reject", "re_batch": batch, "anchor": {"rev": sha},
                "text": "[Changes requested for round %s] %s" % (batch, note),
            })
            lib.append_event(cpath, {"type": "submit", "batch": nbatch + 1,
                                     "ts": now + 0.001, "ids": drafts + [cid]})
            return self._send(200, {"ok": True, "id": cid, "batch": nbatch + 1})

        if path in ("/api/resolve", "/api/reopen"):
            cid = body.get("id")
            ths, _ = lib.fold_threads(doc_path)
            th = next((t for t in ths if t["id"] == cid), None)
            if th is None:
                return self._send(409, {"err": "unknown id"})
            if path.endswith("resolve") and th["status"] == "resolved":
                return self._send(409, {"err": "already resolved"})
            if path.endswith("reopen") and th["status"] not in ("resolved", "answered"):
                return self._send(409, {"err": "not reopenable"})
            lib.append_event(cpath, {
                "type": "resolve" if path.endswith("resolve") else "reopen",
                "id": cid, "ts": now,
            })
            return self._send(200, {"ok": True})

        return self._send(404, {"err": "route"})


def main():
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print("review-board on http://127.0.0.1:%d" % PORT, flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
