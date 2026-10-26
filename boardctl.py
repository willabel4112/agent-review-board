#!/usr/bin/env python3
"""boardctl — agent/session 侧 CLI (replies.jsonl 的唯一写者).

register / list / pending / wait / reply / answer-edit
铁律: reply 与 answer-edit 必带 --summary (一行外行结论, PRD §3 硬性验收)。
"""
import argparse
import json
import os
import sys
import time
from urllib.parse import quote

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import board_lib as lib  # noqa: E402

URL = "http://127.0.0.1:" + os.environ.get("RB_PORT", "8798")


def cmd_register(a):
    p = os.path.realpath(a.path)
    if not os.path.isfile(p):
        sys.exit("no such file: %s" % p)
    reg = lib.load_registry()
    docid = a.id or os.path.splitext(os.path.basename(p))[0].lower()
    if not lib.valid_docid(docid):
        sys.exit("invalid document id: use a single safe path component")
    base, k = docid, 2
    while docid in reg and reg[docid]["path"] != p:
        docid = "%s-%d" % (base, k)
        k += 1
    entry = reg.get(docid) if reg.get(docid, {}).get("path") == p else {}
    entry = dict(entry or {}, path=p, title=a.title or (entry or {}).get("title") or docid)
    # 契约 v2: 母子/出身关系只活在 registry (回合模型惩罚正文里的导航性编辑, trap #15)
    if a.parent:
        if a.parent not in reg:
            sys.exit("unknown parent doc: %s" % a.parent)
        entry["parent"] = a.parent
    if a.born_from:
        if not a.parent:
            sys.exit("--born-from requires --parent (the comment belongs to the parent document)")
        entry["born"] = {"doc": a.parent, "cid": a.born_from}
    if a.question:
        # v2.1 question-first: 孕育本文档的原始问题, 别让它死在 chat 里
        entry["question"] = a.question.strip()
    if a.emoji:
        entry["emoji"] = a.emoji.strip()   # v2.4 F12: favicon 身份 (skill: 起板必配, 避开已用)
    if a.draft:
        entry["draft"] = True              # v2.5 F18③: 草稿也上板, 但注明 (09-07 拍)
    reg[docid] = entry
    lib.save_registry(reg)
    print("registered %s -> %s" % (docid, p))
    print("%s/d/%s" % (URL, quote(docid, safe="")))


def cmd_archive(a):
    """冷却封存 (可逆): 首页折进归档, wait/pending 不再被它吵醒; 评论史不动.
    「active 不该越来越多」的第二去处 — 第一去处是毕业归仓 (move)."""
    reg = lib.load_registry()
    if a.doc not in reg:
        sys.exit("unknown doc: %s" % a.doc)
    if a.cmd == "archive":
        n_open = sum(1 for t in lib.fold_threads(reg[a.doc]["path"])[0]
                     if t["status"] == "open")
        if n_open:
            print("⚠ %d unanswered comments remain; archiving excludes them from pending" % n_open)
        reg[a.doc]["archived"] = True
        print("archived %s" % a.doc)
    else:
        reg[a.doc].pop("archived", None)
        print("unarchived %s" % a.doc)
    lib.save_registry(reg)


def cmd_graduate(a):
    """毕业指针 (v2.1): 裁决正文你先写进项目文档 (路由是 agent 的活), 这里只记指针."""
    reg = lib.load_registry()
    if a.doc not in reg:
        sys.exit("unknown doc: %s" % a.doc)
    ths, _ = lib.fold_threads(reg[a.doc]["path"])
    th = next((t for t in ths if t["id"] == a.cid), None)
    if th is None:
        sys.exit("unknown comment id: %s" % a.cid)
    if th["status"] not in ("answered", "resolved"):
        sys.exit("comment %s is %s — answer it before recording a decision destination" % (a.cid, th["status"]))
    _, rpath = lib.sidecar_paths(reg[a.doc]["path"])
    lib.append_event(rpath, {"type": "graduate", "re": a.cid, "ts": lib.now_ts(),
                             "dest": a.dest.strip(), "note": (a.note or "").strip() or None,
                             "session": lib.agent_identity()["session"]})
    print("graduated %s ⤤ %s" % (a.cid, a.dest))


def cmd_draft(a):
    """v2.5 F18③: 草稿标记开关 (草稿也上板, 注明是草稿)."""
    reg = lib.load_registry()
    if a.doc not in reg:
        sys.exit("unknown doc: %s" % a.doc)
    if a.state == "off":
        reg[a.doc].pop("draft", None)
    else:
        reg[a.doc]["draft"] = True
    lib.save_registry(reg)
    print("draft %s = %s" % (a.doc, a.state != "off"))


def cmd_emoji(a):
    """v2.4 F12: Set the document emoji and favicon."""
    reg = lib.load_registry()
    if a.doc not in reg:
        sys.exit("unknown doc: %s" % a.doc)
    used = {m.get("emoji"): d for d, m in reg.items() if m.get("emoji") and d != a.doc}
    if a.emoji in used:
        print("⚠ %s is already used by %s; assigned anyway, but a distinct emoji is recommended" % (a.emoji, used[a.emoji]))
    reg[a.doc]["emoji"] = a.emoji
    lib.save_registry(reg)
    print("emoji %s = %s" % (a.doc, a.emoji))


def cmd_move(a):
    """同 id 换 path — 文档毕业迁仓不丢评论史 (sidecar 随迁, snapshots 按 docid 键不受影响)."""
    reg = lib.load_registry()
    if a.doc not in reg:
        sys.exit("unknown doc: %s" % a.doc)
    new = os.path.realpath(a.newpath)
    if not os.path.isfile(new):
        sys.exit("Target file missing (move the Markdown file first): %s" % new)
    import shutil
    old = reg[a.doc]["path"]
    moved = []
    for oldsc, newsc in zip(lib.sidecar_paths(old), lib.sidecar_paths(new)):
        if os.path.isfile(oldsc) and not os.path.exists(newsc):
            shutil.move(oldsc, newsc)      # 不用 os.replace: 跨卷迁移会 EXDEV
            moved.append(os.path.basename(newsc))
    reg[a.doc]["path"] = new
    lib.save_registry(reg)
    print("moved %s -> %s (sidecars: %s)" % (a.doc, new, ", ".join(moved) or "none"))


def cmd_list(a):
    for docid, meta in lib.load_registry().items():
        ths, _ = lib.fold_threads(meta["path"])
        counts = {}
        for t in ths:
            counts[t["status"]] = counts.get(t["status"], 0) + 1
        print(docid, meta["path"], json.dumps(counts, ensure_ascii=False))


def _fmt(items):
    for it in items:
        anchor = it.get("anchor") or {}
        sel = anchor.get("sel") or {}
        where = anchor.get("slug") or ("Changes requested for round %s" % it.get("re_batch")
                                       if it.get("kind") == "reject" else "Whole document")
        print("== %s %s [batch %s] @%s" % (it["doc"], it["id"],
                                           it.get("batch"), where))
        if it.get("intent") == "explore":
            print("   [explore] Answer this question; for a child document, register --parent %s --born-from %s" % (it["doc"], it["id"]))
        # 划词锚给 sel.text (更准的上下文), 没有才退回块 quote
        if sel.get("text"):
            print("   >selection> %s" % sel["text"][:100])
        elif anchor.get("quote"):
            print("   > %s" % anchor["quote"][:100])
        for ln in it["text"].splitlines():
            print("   %s" % ln)
        # v2: thread 有多轮时打印最近 ≤3 轮 (根 comment 上面已印, 跳过)
        turns = [t for t in it.get("turns") or [] if t["kind"] != "comment"]
        for t in turns[-3:]:
            who = "Reviewer follow-up" if t["kind"] == "followup" else \
                  (t.get("author") or "agent") if t["kind"] == "reply" else \
                  "answer·%s" % t.get("mode")
            first = (t.get("text") or t.get("summary") or "").splitlines() or [""]
            print("   ↳ [%s] %s" % (who, first[0][:100]))
    print("-- %d pending" % len(items))


def _pending(doc=None):
    items = lib.pending()
    if not doc:
        return items
    want = set(d.strip() for d in doc.split(",") if d.strip())
    return [i for i in items if i["doc"] in want]


def cmd_pending(a):
    items = _pending(a.doc)
    if a.json:
        print(json.dumps(items, ensure_ascii=False, indent=1))
    else:
        _fmt(items)
        _fmt_coverage(items)


def _claim(docs, me, takeover):
    """v2.3 认领 (契约): 一个 doc 同一时刻最多一个活着的值守 session. 拒绝 → exit 3."""
    claims = lib.load_claims()
    now = time.time()
    for d in docs:
        c = claims.get(d)
        if c and c.get("sid") != me["sid"] and lib.claim_alive(c, now):
            who = "%s (%s@%s, %s, heartbeat %ds ago)" % (c.get("name"), (c.get("sid") or "")[:8],
                                                 c.get("host"), c.get("cwd"),
                                                 int(now - (c.get("beat") or 0)))
            if not takeover:
                print("✗ %s is watched by %s; coordinate with that session, or explicitly use --takeover" % (d, who))
                return 3
            print("⚠ takeover: claiming %s from %s" % (d, who))
        claims[d] = {"sid": me["sid"], "name": me["name"], "host": me["host"],
                     "cwd": me["cwd"], "pid": me["pid"], "since": now, "beat": now}
    lib.save_claims(claims)
    print("Watching %s ← %s (%s@%s)" % (",".join(docs), me["name"], me["sid"][:8], me["host"]))
    return 0


def _beat(docs, me):
    claims = lib.load_claims()
    now = time.time()
    for d in docs:
        c = claims.get(d)
        if c and c.get("pid") == me["pid"]:
            c["beat"] = now
    lib.save_claims(claims)


def _release(docs, me):
    claims = lib.load_claims()
    for d in docs:
        c = claims.get(d)
        if c and c.get("pid") == me["pid"]:
            claims.pop(d, None)
    lib.save_claims(claims)


def _fmt_coverage(items):
    """pending 尾巴: 每份有Pending的文档一行覆盖率 — agent 别把没看当默许 (契约 v2.3)."""
    reg = lib.load_registry()
    for docid in sorted({it["doc"] for it in items}):
        meta = reg.get(docid)
        if not meta:
            continue
        md = lib.read_doc(meta["path"])
        if md is None:
            continue
        cov = lib.coverage(meta["path"], docid, lib.split_blocks(md))
        heads = []
        for u in cov["unseen"]:
            sl = (u.get("slug") or "").split("~")[0]     # 同节多块的 ~n 后缀去掉, 按节报
            if sl and sl not in heads:
                heads.append(sl)
        print("   Coverage %s: read %d/%d blocks%s" % (
            docid, cov["n_seen"], cov["n_total"],
            (" · Unread: " + ", ".join(heads[:6]) + (" …" if len(heads) > 6 else "")) if heads else ""))


def cmd_wait(a):
    """阻塞直到有已提交未回答的评论 (退出码 0), 超时退出码 1, 被别人值守退出码 3.
    设计给 run_in_background 用: 进程结束 = 有活了.
    trap #16: 退出前双拍确认 — 单拍读到旧折叠会假唤醒 (08-23 实测秒退).
    v2.3: --doc 即认领 (claims.local.json), 每 interval 心跳, 退出释放."""
    deadline = time.time() + a.timeout
    docs = [d.strip() for d in (a.doc or "").split(",") if d.strip()]
    me = lib.session_self()
    # TaskStop / kill 发的是 SIGTERM — 默认直接死, finally 不跑 ⇒ 认领残留到 TTL (09-01 实撞); 转成 SystemExit
    import signal
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(143))
    if docs:
        rc = _claim(docs, me, a.takeover)
        if rc:
            return rc
    else:
        print("⚠ Global wait does not claim documents; use --doc to avoid waking multiple sessions for the same comment")
    armed = False
    try:
        while time.time() < deadline:
            items = _pending(a.doc)
            if items and armed:
                _fmt(items)
                _fmt_coverage(items)
                return 0
            armed = bool(items)
            if docs:
                _beat(docs, me)
            time.sleep(a.interval)
        print("-- wait timeout, 0 pending")
        return 1
    finally:
        if docs:
            _release(docs, me)


def cmd_claims(a):
    """Show local document watchers."""
    claims = lib.load_claims()
    now = time.time()
    if not claims:
        print("-- No documents are being watched")
    for d, c in claims.items():
        alive = lib.claim_alive(c, now)
        print("%-24s %s %s (%s@%s, %s) heartbeat %ds ago" % (
            d, "●" if alive else "○", c.get("name"), (c.get("sid") or "")[:8],
            c.get("host"), c.get("cwd"), int(now - (c.get("beat") or 0))))


def cmd_release(a):
    """手放认领: 指定 doc 放那份 (不管谁的), 不指定放本 session 全部."""
    claims = lib.load_claims()
    me = lib.session_self()
    gone = []
    for d in list(claims):
        if (a.doc and d == a.doc) or (not a.doc and claims[d].get("sid") == me["sid"]):
            claims.pop(d)
            gone.append(d)
    lib.save_claims(claims)
    print("released %s" % (", ".join(gone) or "none"))


def cmd_unregister(a):
    """摘掉注册条目 (sidecar / 快照不动, 可重新 register 找回) — B1 死条目的正解."""
    reg = lib.load_registry()
    if a.doc not in reg:
        sys.exit("unknown doc: %s" % a.doc)
    p = reg[a.doc]["path"]
    if os.path.isfile(p):
        n = len(lib.fold_threads(p)[0])
        print("⚠ File still exists (%d threads); unregistered, review history remains in sidecars" % n)
    else:
        print("File not on this machine: %s" % p)
    reg.pop(a.doc)
    lib.save_registry(reg)
    print("unregistered %s" % a.doc)


def _docref_rel(doc_path, ref):
    """--doc-ref 校验 + 归一为 doc 目录相对路径 (jail 判据与 server 同款)."""
    base = os.path.dirname(os.path.realpath(doc_path))
    full = os.path.realpath(ref if os.path.isabs(ref)
                            else os.path.join(base, ref))
    if not os.path.isfile(full):
        sys.exit("--doc-ref file does not exist: %s" % full)
    if full != base and not full.startswith(base + os.sep):
        sys.exit("--doc-ref must stay inside the document directory: %s" % full)
    rel = os.path.relpath(full, base)
    conv = os.path.basename(doc_path) + ".answers" + os.sep
    if not rel.startswith(conv):
        print("⚠ doc_ref is outside the conventional <doc>.answers/ directory; accepted: %s" % rel)
    return rel.replace(os.sep, "/")


def _answer(doc, cid, text, summary, mode, commit=None, doc_ref=None):
    if not summary or not summary.strip():
        sys.exit("--summary is required: give a useful one-line conclusion")
    reg = lib.load_registry()
    if doc not in reg:
        sys.exit("unknown doc: %s" % doc)
    ths, _ = lib.fold_threads(reg[doc]["path"])
    th = next((t for t in ths if t["id"] == cid), None)
    if th is None:
        sys.exit("unknown comment id: %s" % cid)
    if th["status"] not in ("open", "answered"):
        sys.exit("comment %s is %s — 409, no event written" % (cid, th["status"]))
    if doc_ref:
        doc_ref = _docref_rel(reg[doc]["path"], doc_ref)
    _, rpath = lib.sidecar_paths(reg[doc]["path"])
    # 先落版本 (rev 事件必须早于它所描述的 answer), 再落 reply/answer
    sha = lib.ensure_rev(reg[doc]["path"], doc, rpath,
                         "answered %s" % cid, "agent", commit)
    now = time.time()
    sid = lib.agent_identity()["session"]
    if text and text.strip():
        rid = lib.next_id(lib.read_events(rpath), "r")
        ev = {"type": "reply", "id": rid, "re": cid, "ts": now,
              "author": lib.agent_identity()["author"], "harness": lib.agent_identity()["harness"], "session": sid, "text": text}
        if doc_ref:
            ev["doc_ref"] = doc_ref
        lib.append_event(rpath, ev)
    ev = {"type": "answer", "re": cid, "ts": now + 0.001,
          "mode": mode, "commit": commit, "rev": sha,
          "summary": summary.strip(), "session": sid, "harness": lib.agent_identity()["harness"]}
    if doc_ref:
        ev["doc_ref"] = doc_ref
    lib.append_event(rpath, ev)
    print("answered %s (%s) @rev %s%s" % (cid, mode, sha,
                                          " · doc_ref " + doc_ref if doc_ref else ""))


def cmd_reply(a):
    text = a.text
    if a.file:
        with open(a.file, encoding="utf-8") as f:
            text = f.read()
    if (not text or not text.strip()) and a.doc_ref:
        text = a.summary          # B2 (v2.3): 外链答案时一行结论就是正文
    if not text or not text.strip():
        sys.exit("empty reply — use --text or --file")
    _answer(a.doc, a.cid, text, a.summary, "reply", doc_ref=a.doc_ref)


def cmd_answer_edit(a):
    _answer(a.doc, a.cid, a.note or "", a.summary, "edit", commit=a.commit,
            doc_ref=a.doc_ref)


def cmd_answer_path(a):
    """打印外链答案的约定路径 (只打印不建目录 — agent 自己 Write). 防约定漂移."""
    reg = lib.load_registry()
    if a.doc not in reg:
        sys.exit("unknown doc: %s" % a.doc)
    p = reg[a.doc]["path"]
    slug = ("-" + a.slug) if a.slug else ""
    print(os.path.join(os.path.dirname(p),
                       os.path.basename(p) + ".answers",
                       "%s%s.md" % (a.cid, slug)))


def cmd_rounds(a):
    """回合状态: 他提交了什么批次 / 答完没 / 批准没 (等他拍板的顶到脸上)."""
    reg = lib.load_registry()
    docs = [a.doc] if a.doc else list(reg)
    for docid in docs:
        if docid not in reg:
            sys.exit("unknown doc: %s" % docid)
        for r in lib.fold_rounds(reg[docid]["path"]):
            mark = {"open": "Pending", "answered": "⏳ Awaiting approval", "approved": "✓ Approved"}
            print("%-14s round %-2s %-8s comments=%d answered=%d edits=%d %s" % (
                docid, r["batch"], mark.get(r["status"], r["status"]),
                len(r["ids"]), len(r["answered_ids"]), len(r["edited_ids"]),
                ("· " + r["approve_note"]) if r["approve_note"] else ""))


def cmd_drafts(a):
    """未提交草稿的**计数**. trap #6: 草稿正文对 agent 不可见, 只报数用于提醒."""
    now = lib.now_ts()
    rows = lib.draft_counts()
    for r in rows:
        print("%-14s %d drafts (oldest %.1f days ago)" %
              (r["doc"], r["n"], (now - r["oldest_ts"]) / 86400))
    if not rows:
        print("-- No unsent drafts")


def main():
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(prog="boardctl")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("register", help="Register a Markdown document")
    p.add_argument("path")
    p.add_argument("--id")
    p.add_argument("--title")
    p.add_argument("--parent", help="Parent document ID (shows this as a child document)")
    p.add_argument("--born-from", help="Origin comment ID (requires --parent)")
    p.add_argument("--question", help="The original question this document answers")
    p.add_argument("--emoji", help="Document emoji used in its favicon")
    p.add_argument("--draft", action="store_true", help="Label this document as a draft")
    p.set_defaults(fn=cmd_register)

    p = sub.add_parser("draft", help="Set the document draft label (on|off)")
    p.add_argument("doc")
    p.add_argument("state", nargs="?", default="on", choices=["on", "off"])
    p.set_defaults(fn=cmd_draft)

    p = sub.add_parser("emoji", help="Set the document emoji and favicon")
    p.add_argument("doc")
    p.add_argument("emoji")
    p.set_defaults(fn=cmd_emoji)

    p = sub.add_parser("archive", help="Archive a document (reversible; excluded from wait/pending)")
    p.add_argument("doc")
    p.set_defaults(fn=cmd_archive)

    p = sub.add_parser("unarchive", help="Unarchive a document")
    p.add_argument("doc")
    p.set_defaults(fn=cmd_archive)

    p = sub.add_parser("graduate", help="Record where an answered decision was saved")
    p.add_argument("doc")
    p.add_argument("cid")
    p.add_argument("--dest", required=True, help="Decision destination (for example docs/decisions.md#sampling)")
    p.add_argument("--note", help="One-line decision")
    p.set_defaults(fn=cmd_graduate)

    p = sub.add_parser("move", help="Change the path without losing review history (move the Markdown first)")
    p.add_argument("doc")
    p.add_argument("newpath")
    p.set_defaults(fn=cmd_move)

    p = sub.add_parser("list", help="List registered documents and status counts")
    p.set_defaults(fn=cmd_list)

    p = sub.add_parser("pending", help="List delivered, unanswered comments")
    p.add_argument("--json", action="store_true")
    p.add_argument("--doc", help="Only these document IDs (comma-separated)")
    p.set_defaults(fn=cmd_pending)

    p = sub.add_parser("wait", help="Wait for delivered work in the active process")
    p.add_argument("--timeout", type=float, default=86400)
    p.add_argument("--interval", type=float, default=2)
    p.add_argument("--doc", help="Watch and claim these document IDs (comma-separated)")
    p.add_argument("--takeover", action="store_true", help="Explicitly take over another live session claim")
    p.set_defaults(fn=cmd_wait)

    p = sub.add_parser("claims", help="Show local document watchers")
    p.set_defaults(fn=cmd_claims)

    p = sub.add_parser("release", help="Release a document claim, or all claims owned by this session")
    p.add_argument("doc", nargs="?")
    p.set_defaults(fn=cmd_release)

    p = sub.add_parser("unregister", help="Unregister without deleting sidecars or snapshots")
    p.add_argument("doc")
    p.set_defaults(fn=cmd_unregister)

    p = sub.add_parser("rounds", help="List review rounds and acceptance status")
    p.add_argument("doc", nargs="?")
    p.set_defaults(fn=cmd_rounds)

    p = sub.add_parser("drafts", help="Count unsent drafts without exposing their contents")
    p.set_defaults(fn=cmd_drafts)

    p = sub.add_parser("reply", help="Answer a comment with text")
    p.add_argument("doc")
    p.add_argument("cid")
    p.add_argument("--text")
    p.add_argument("--file")
    p.add_argument("--summary", required=True)
    p.add_argument("--doc-ref", help="Linked Markdown answer (convention: <doc>.answers/<cid>-<slug>.md)")
    p.set_defaults(fn=cmd_reply)

    p = sub.add_parser("answer-edit", help="Associate a document edit with a comment and commit")
    p.add_argument("doc")
    p.add_argument("cid")
    p.add_argument("--commit")
    p.add_argument("--note")
    p.add_argument("--summary", required=True)
    p.add_argument("--doc-ref", help="Linked Markdown answer (convention: <doc>.answers/<cid>-<slug>.md)")
    p.set_defaults(fn=cmd_answer_edit)

    p = sub.add_parser("answer-path", help="Print the suggested answer path without creating directories")
    p.add_argument("doc")
    p.add_argument("cid")
    p.add_argument("slug", nargs="?")
    p.set_defaults(fn=cmd_answer_path)

    a = ap.parse_args()
    rc = a.fn(a)
    sys.exit(rc or 0)


if __name__ == "__main__":
    main()
