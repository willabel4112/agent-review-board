#!/usr/bin/env python3
"""零依赖冒烟测试: python3 tests/test_lib.py — 全绿退出码 0."""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import board_lib as lib  # noqa: E402

MD = """---
title: demo
---

# 标题一

第一段文字, 有内容。
第二行同段。

## 接线

| a | b |
|---|---|
| 1 | 2 |

```mermaid
graph TD; A-->B;
```

## 接线

同名标题去重测试。
"""


def test_blocks():
    blocks = lib.split_blocks(MD)
    # 覆盖: 每个非空行恰好落在一个块里 (渲染层永不改字节的前提)
    src_lines = [l for l in MD.split("\n") if l.strip()]
    blk_lines = []
    for b in blocks:
        blk_lines += [l for l in b["md"].split("\n") if l.strip()]
    assert blk_lines == src_lines, "line coverage broken"
    # frontmatter 独立成块
    assert blocks[0]["md"].startswith("---")
    # 围栏保持整块
    fences = [b for b in blocks if b["md"].startswith("```")]
    assert len(fences) == 1 and "graph TD" in fences[0]["md"]
    # 同名标题 slug 去重 (两个 "## 接线" 的段各有独立 slug)
    slugs = [b["slug"] for b in blocks]
    assert len(slugs) == len(set(slugs)), "slug collision: %s" % slugs
    # hash 稳定
    assert lib.split_blocks(MD)[3]["hash"] == blocks[3]["hash"]


def test_anchor():
    blocks = lib.split_blocks(MD)
    tgt = [b for b in blocks if "第一段" in b["md"]][0]
    a = {"slug": tgt["slug"], "hash": tgt["hash"],
         "quote": lib.normalize(tgt["md"])[:120]}
    assert lib.resolve_anchor(a, blocks) == tgt["i"]
    # hash 失配 → quote 兜底
    a2 = dict(a, hash="deadbeef")
    assert lib.resolve_anchor(a2, blocks) == tgt["i"]
    # 全失配 → None (未锚定, 不猜)
    a3 = {"slug": "x", "hash": "deadbeef", "quote": "这段文字不存在于文档任何地方对吧"}
    assert lib.resolve_anchor(a3, blocks) is None


def test_fold():
    with tempfile.TemporaryDirectory() as d:
        doc = os.path.join(d, "x.md")
        open(doc, "w").write(MD)
        cpath, rpath = lib.sidecar_paths(doc)
        lib.append_event(cpath, {"type": "comment", "id": "c-0001", "ts": 1.0,
                                 "text": "问题一", "anchor": None})
        ths, nb = lib.fold_threads(doc)
        assert ths[0]["status"] == "draft" and nb == 0
        lib.append_event(cpath, {"type": "submit", "batch": 1, "ts": 2.0, "ids": ["c-0001"]})
        ths, nb = lib.fold_threads(doc)
        assert ths[0]["status"] == "open" and nb == 1
        assert len(lib.pending({"x": {"path": doc}})) == 1
        lib.append_event(rpath, {"type": "reply", "id": "r-0001", "re": "c-0001",
                                 "ts": 3.0, "text": "答"})
        lib.append_event(rpath, {"type": "answer", "re": "c-0001", "ts": 3.1,
                                 "mode": "reply", "summary": "一行结论"})
        ths, _ = lib.fold_threads(doc)
        assert ths[0]["status"] == "answered" and ths[0]["summary"] == "一行结论"
        assert len(ths[0]["replies"]) == 1
        assert lib.pending({"x": {"path": doc}}) == []
        lib.append_event(cpath, {"type": "resolve", "id": "c-0001", "ts": 4.0})
        assert lib.fold_threads(doc)[0][0]["status"] == "resolved"
        lib.append_event(cpath, {"type": "reopen", "id": "c-0001", "ts": 5.0})
        assert lib.fold_threads(doc)[0][0]["status"] == "open"
        # next_id 递增
        assert lib.next_id(lib.read_events(cpath), "c") == "c-0002"


FIX = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")


def test_diff_golden():
    """Synthetic regression fixture: a reviewed migration plan.

    判据不是"某个数字", 是**不能把改写误判成 删+增** —— 那正是 v0 让三条有效
    评论变孤儿的机制。
    """
    o = lib.split_blocks(open(os.path.join(FIX, "plan_old.md")).read())
    n = lib.split_blocks(open(os.path.join(FIX, "plan_new.md")).read())
    ops = lib.diff_blocks(o, n)
    cnt = {}
    for x in ops:
        cnt[x["op"]] = cnt.get(x["op"], 0) + 1
    assert cnt == {"same": 5, "mod": 1, "add": 2, "del": 2}, cnt
    # 每个 new 块恰好被覆盖一次, 每个 old 块恰好被覆盖一次 (不漏不重)
    assert sorted(x["new"] for x in ops if x["new"] is not None) == list(range(len(n)))
    assert sorted(x["old"] for x in ops if x["old"] is not None) == list(range(len(o)))
    # §2.1 的 ACL jsonc 块 = 改写 (v1 → v2), 不是 add+del
    acl = [x for x in ops if x["new"] is not None
           and n[x["new"]]["md"].startswith("```jsonc")]
    assert len(acl) == 1 and acl[0]["op"] == "mod", acl
    assert 0.5 <= acl[0]["sim"] < 1.0, acl[0]["sim"]   # 报真实相似度, 不报加权分
    # 整块新增的 §2.6 (答 c-0008 那节) 判 add
    assert any(x["op"] == "add" and "Appendix: acceptance evidence" in n[x["new"]]["md"]
               for x in ops)


def test_anchor_v1_forward():
    """v0 的病: 意见被采纳 ⇒ 块被改写 ⇒ 锚点失配 ⇒ 有效反馈进未锚定托盘.
    v1: 借 anchor.rev 快照前向映射, 判 modified 并给出新块."""
    old = open(os.path.join(FIX, "plan_old.md")).read()
    new = open(os.path.join(FIX, "plan_new.md")).read()
    docid = "_test_aclrev"
    rev = lib.snapshot_put(docid, old)
    ob, nb = lib.split_blocks(old), lib.split_blocks(new)
    # c-0008 型: 锚在旧 ACL jsonc 块上, 该块被改写成 v2
    tgt = [b for b in ob if b["md"].startswith("```jsonc")][0]
    a = {"slug": tgt["slug"], "hash": tgt["hash"],
         "quote": lib.normalize(tgt["md"])[:120], "rev": rev}
    assert lib.resolve_anchor(a, nb) is None            # v0 会掉托盘
    i, st = lib.resolve_anchor_v1(a, nb, docid)
    assert st == "modified" and nb[i]["md"].startswith("```jsonc"), (st, i)
    assert "v2" in nb[i]["md"]
    # 未变的块仍是 ok
    same = [b for b in ob if b["md"].startswith("## 1.")][0]
    a2 = {"slug": same["slug"], "hash": same["hash"],
          "quote": lib.normalize(same["md"])[:120], "rev": rev}
    assert lib.resolve_anchor_v1(a2, nb, docid)[1] == "ok"
    # 被整块删掉的 §3 待拍板 → deleted (进托盘, 但带得出旧原文)
    gone = [b for b in ob if b["md"].startswith("## 3. Pending decisions")][0]
    a3 = {"slug": gone["slug"], "hash": gone["hash"],
          "quote": lib.normalize(gone["md"])[:120], "rev": rev}
    assert lib.resolve_anchor_v1(a3, nb, docid) == (None, "deleted")
    # v0 legacy 评论 (无 rev) → unknown, 行为与今天一致
    a4 = dict(a3)
    a4.pop("rev")
    assert lib.resolve_anchor_v1(a4, nb, docid) == (None, "unknown")
    import shutil
    shutil.rmtree(os.path.join(lib.SNAPDIR, docid), ignore_errors=True)


def test_rounds_and_drafts():
    """回合/装饰/draft 可改可撤. 用假时钟 — fold 按 ts 排序合并两个 sidecar,
    真假时间戳混用会把事件排错顺序 (调试实录)."""
    real_now = lib.now_ts
    clock = [1000.0]

    def tick(step=1.0):
        clock[0] += step
        return clock[0]

    lib.now_ts = lambda: clock[0]
    try:
        with tempfile.TemporaryDirectory() as d:
            doc = os.path.join(d, "y.md")
            open(doc, "w").write(MD)
            docid = "_test_rounds"
            cpath, rpath = lib.sidecar_paths(doc)
            tick()
            r0 = lib.ensure_rev(doc, docid, cpath, "first", "server")
            assert lib.ensure_rev(doc, docid, cpath, "again", "server") == r0
            assert len([e for e in lib.all_events(doc)
                        if e["type"] == "rev"]) == 1          # 幂等: 同 sha 不重复写
            t_c1 = tick()
            lib.append_event(cpath, {"type": "comment", "id": "c-0001", "ts": t_c1,
                                     "text": "原文", "anchor": None})
            # draft 可改可撤; 计数对 agent 可见但正文不出 (trap #6)
            lib.append_event(cpath, {"type": "edit", "re": "c-0001", "ts": tick(),
                                     "text": "改过的"})
            assert lib.fold_threads(doc)[0][0]["text"] == "改过的"
            dc = lib.draft_counts({docid: {"path": doc}})
            assert dc == [{"doc": docid, "n": 1, "oldest_ts": t_c1}]
            assert "改过的" not in json.dumps(dc, ensure_ascii=False)
            lib.append_event(cpath, {"type": "comment", "id": "c-0002", "ts": tick(),
                                     "text": "撤掉我", "anchor": None})
            lib.append_event(cpath, {"type": "drop", "re": "c-0002", "ts": tick()})
            assert [t["id"] for t in lib.fold_threads(doc)[0]] == ["c-0001"]
            # 提交 → 回合开张, rev_before = 提交时的文档版本
            lib.append_event(cpath, {"type": "submit", "batch": 1, "ts": tick(),
                                     "ids": ["c-0001"]})
            rd = lib.fold_rounds(doc)
            assert len(rd) == 1 and rd[0]["status"] == "open", rd
            assert rd[0]["rev_before"] == r0, rd
            # 提交后 edit/drop 一律无效 (已提交的发言不可撤不可改)
            lib.append_event(cpath, {"type": "edit", "re": "c-0001", "ts": tick(),
                                     "text": "偷改"})
            lib.append_event(cpath, {"type": "drop", "re": "c-0001", "ts": tick()})
            ths, _ = lib.fold_threads(doc)
            assert len(ths) == 1 and ths[0]["text"] == "改过的"
            # agent 改文档 + answer-edit
            open(doc, "w").write(MD + "\n新增一段。\n")
            tick()
            r1 = lib.ensure_rev(doc, docid, rpath, "answered batch 1",
                                "agent", "abc1234")
            assert r1 != r0
            lib.append_event(rpath, {"type": "answer", "re": "c-0001", "ts": tick(),
                                     "mode": "edit", "commit": "abc1234",
                                     "summary": "改了", "rev": r1})
            rd = lib.fold_rounds(doc)[0]
            assert rd["status"] == "answered" and rd["rev_after"] == r1, rd
            assert rd["edited_ids"] == ["c-0001"]
            # 装饰: 新增块打 new, 归因到 c-0001
            blocks = lib.split_blocks(lib.read_doc(doc))
            chg, causes, r, stats, base = lib.decoration(doc, docid, blocks)
            assert list(chg.values()) == ["new"] and causes == ["c-0001"], (chg, causes)
            assert base == r0
            assert stats["add"] == 1
            # 批准 → 装饰清场 (baseline 推到批准版)
            lib.append_event(cpath, {"type": "approve", "batch": 1, "ts": tick(),
                                     "rev": r1, "note": "行"})
            assert lib.fold_rounds(doc)[0]["status"] == "approved"
            chg, causes, r, stats, base = lib.decoration(doc, docid, blocks)
            assert chg == {} and causes == [] and base == r1
            import shutil
            shutil.rmtree(os.path.join(lib.SNAPDIR, docid), ignore_errors=True)
    finally:
        lib.now_ts = real_now


# ---------- v2: followup / sent / deliver / sel ----------

def _mkdoc(d):
    doc = os.path.join(d, "z.md")
    open(doc, "w").write(MD)
    return doc


def test_followup_multiturn():
    """契约 v2 状态机: followup(sent) = 隐式 reopen ⇒ pending 唤醒; 再 answer 收口."""
    with tempfile.TemporaryDirectory() as d:
        doc = _mkdoc(d)
        cpath, rpath = lib.sidecar_paths(doc)
        lib.append_event(cpath, {"type": "comment", "id": "c-0001", "ts": 1.0,
                                 "text": "问", "anchor": None})
        lib.append_event(cpath, {"type": "submit", "batch": 1, "ts": 2.0, "ids": ["c-0001"]})
        lib.append_event(rpath, {"type": "answer", "re": "c-0001", "ts": 3.0,
                                 "mode": "reply", "summary": "答一"})
        lib.append_event(cpath, {"type": "followup", "id": "f-0001", "re": "c-0001",
                                 "ts": 4.0, "text": "追问", "sent": True})
        ths, _ = lib.fold_threads(doc)
        assert ths[0]["status"] == "open"
        items = lib.pending({"z": {"path": doc}})
        assert len(items) == 1
        kinds = [t["kind"] for t in items[0]["turns"]]
        assert kinds == ["comment", "answer", "followup"], kinds
        # 再答 → answered, summary 换新头 (last-writer-wins)
        lib.append_event(rpath, {"type": "answer", "re": "c-0001", "ts": 5.0,
                                 "mode": "reply", "summary": "答二"})
        ths, _ = lib.fold_threads(doc)
        assert ths[0]["status"] == "answered" and ths[0]["summary"] == "答二"
        assert len(ths[0]["turns"]) == 4
        # resolved 之后追问 = 隐式 reopen
        lib.append_event(cpath, {"type": "resolve", "id": "c-0001", "ts": 6.0})
        lib.append_event(cpath, {"type": "followup", "id": "f-0002", "re": "c-0001",
                                 "ts": 7.0, "text": "又想到", "sent": True})
        assert lib.fold_threads(doc)[0][0]["status"] == "open"


def test_followup_draft_invisible():
    """trap #6 延伸: draft followup 正文对 agent 不可见; deliver 后可见且不可改."""
    with tempfile.TemporaryDirectory() as d:
        doc = _mkdoc(d)
        cpath, rpath = lib.sidecar_paths(doc)
        lib.append_event(cpath, {"type": "comment", "id": "c-0001", "ts": 1.0,
                                 "text": "问", "anchor": None})
        lib.append_event(cpath, {"type": "submit", "batch": 1, "ts": 2.0, "ids": ["c-0001"]})
        lib.append_event(rpath, {"type": "answer", "re": "c-0001", "ts": 3.0,
                                 "mode": "reply", "summary": "答"})
        lib.append_event(cpath, {"type": "followup", "id": "f-0001", "re": "c-0001",
                                 "ts": 4.0, "text": "偷偷写的草稿追问"})
        ths, _ = lib.fold_threads(doc)
        assert ths[0]["status"] == "answered"            # draft 不翻状态
        reg = {"z": {"path": doc}}
        assert "偷偷写的" not in json.dumps(lib.pending(reg), ensure_ascii=False)
        dc = lib.draft_counts(reg)
        assert dc and dc[0]["n"] == 1 and dc[0]["oldest_ts"] == 4.0
        # draft 可改; deliver 释放 → open + 可见; 之后 edit/drop 无效
        lib.append_event(cpath, {"type": "edit", "re": "f-0001", "ts": 5.0,
                                 "text": "改过的追问"})
        lib.append_event(cpath, {"type": "deliver", "ts": 6.0, "fids": ["f-0001"]})
        ths, _ = lib.fold_threads(doc)
        assert ths[0]["status"] == "open"
        assert "改过的追问" in json.dumps(lib.pending(reg), ensure_ascii=False)
        lib.append_event(cpath, {"type": "edit", "re": "f-0001", "ts": 7.0, "text": "偷改"})
        lib.append_event(cpath, {"type": "drop", "re": "f-0001", "ts": 8.0})
        ths, _ = lib.fold_threads(doc)
        fu = [t for t in ths[0]["turns"] if t["kind"] == "followup"]
        assert len(fu) == 1 and fu[0]["text"] == "改过的追问"
        # drop 掉的 draft followup 从 turns 消失
        lib.append_event(cpath, {"type": "followup", "id": "f-0002", "re": "c-0001",
                                 "ts": 9.0, "text": "撤我"})
        lib.append_event(cpath, {"type": "drop", "re": "f-0002", "ts": 10.0})
        ths, _ = lib.fold_threads(doc)
        assert len([t for t in ths[0]["turns"] if t["kind"] == "followup"]) == 1


def test_sent_comment_no_round():
    """契约 v2: sent=true 即发单条 — 生而 open、batch=null、永不入回合."""
    with tempfile.TemporaryDirectory() as d:
        doc = _mkdoc(d)
        cpath, _ = lib.sidecar_paths(doc)
        lib.append_event(cpath, {"type": "comment", "id": "c-0001", "ts": 1.0,
                                 "text": "走批次的", "anchor": None})
        lib.append_event(cpath, {"type": "submit", "batch": 1, "ts": 2.0, "ids": ["c-0001"]})
        lib.append_event(cpath, {"type": "comment", "id": "c-0002", "ts": 3.0,
                                 "text": "即发的", "anchor": None, "sent": True})
        ths, _ = lib.fold_threads(doc)
        c2 = [t for t in ths if t["id"] == "c-0002"][0]
        assert c2["status"] == "open" and c2["batch"] is None
        rounds = lib.fold_rounds(doc)
        assert len(rounds) == 1 and "c-0002" not in rounds[0]["ids"]
        assert len(lib.pending({"z": {"path": doc}})) == 2


def test_deliver_no_round():
    """deliver 释放 draft followup 不开回合、不动 nbatch (submit 冻结)."""
    with tempfile.TemporaryDirectory() as d:
        doc = _mkdoc(d)
        cpath, rpath = lib.sidecar_paths(doc)
        lib.append_event(cpath, {"type": "comment", "id": "c-0001", "ts": 1.0,
                                 "text": "问", "anchor": None})
        lib.append_event(cpath, {"type": "submit", "batch": 1, "ts": 2.0, "ids": ["c-0001"]})
        lib.append_event(rpath, {"type": "answer", "re": "c-0001", "ts": 3.0,
                                 "mode": "reply", "summary": "答"})
        lib.append_event(cpath, {"type": "followup", "id": "f-0001", "re": "c-0001",
                                 "ts": 4.0, "text": "追"})
        lib.append_event(cpath, {"type": "deliver", "ts": 5.0, "fids": ["f-0001"]})
        assert len(lib.fold_rounds(doc)) == 1
        ths, nbatch = lib.fold_threads(doc)
        assert nbatch == 1 and ths[0]["status"] == "open"
        # 防御: 空批 submit 不开回合
        lib.append_event(cpath, {"type": "submit", "batch": 9, "ts": 6.0, "ids": []})
        assert len(lib.fold_rounds(doc)) == 1


def test_sel_resolve():
    """划词锚: 块内重找; 多处命中取近 start; 失配 gone (降级整块, 不猜)."""
    md = "甲乙丙丁 目标片段 后缀。再来一次 目标片段 尾巴。"
    pos, st = lib.resolve_sel({"text": "目标片段", "start": 5}, md)
    assert st == "ok" and pos["start"] == 5, (pos, st)
    pos2, _ = lib.resolve_sel({"text": "目标片段", "start": 18}, md)
    assert pos2["start"] > pos["start"]
    assert lib.resolve_sel({"text": "不存在的词", "start": 0}, md) == (None, "gone")
    assert lib.resolve_sel(None, md) == (None, "gone")
    # normalize 同族: 选中文带多余空白仍命中
    pos3, st3 = lib.resolve_sel({"text": " 目标   片段 ".replace("片", "片"), "start": 0},
                                "目标 片段 x")
    assert st3 == "ok"


def test_legacy_replay_turns():
    """现网 v0/v1 形状回放: 状态与 v1 一致, turns = comment+reply+answer 按 ts 序."""
    with tempfile.TemporaryDirectory() as d:
        doc = _mkdoc(d)
        cpath, rpath = lib.sidecar_paths(doc)
        # v0 形状: 无 rev / 无 sent / 无 session
        lib.append_event(cpath, {"type": "comment", "id": "c-0001", "ts": 1.0,
                                 "text": "旧问", "anchor": {"quote": "x"}})
        lib.append_event(cpath, {"type": "submit", "batch": 1, "ts": 2.0, "ids": ["c-0001"]})
        lib.append_event(rpath, {"type": "reply", "id": "r-0001", "re": "c-0001",
                                 "ts": 3.0, "text": "旧答"})
        lib.append_event(rpath, {"type": "answer", "re": "c-0001", "ts": 3.1,
                                 "mode": "reply", "summary": "旧结论"})
        lib.append_event(cpath, {"type": "resolve", "id": "c-0001", "ts": 4.0})
        ths, nb = lib.fold_threads(doc)
        th = ths[0]
        assert th["status"] == "resolved" and th["summary"] == "旧结论" and nb == 1
        assert len(th["replies"]) == 1                    # v1 字段原样
        assert [t["kind"] for t in th["turns"]] == ["comment", "reply", "answer"]
        assert th["turns"][0]["text"] == "旧问"


def test_graduate():
    """v2.1: graduate = 标记不是状态 — graduated 字段+turn 落上, status/resolve 不受影响."""
    with tempfile.TemporaryDirectory() as d:
        doc = _mkdoc(d)
        cpath, rpath = lib.sidecar_paths(doc)
        lib.append_event(cpath, {"type": "comment", "id": "c-0001", "ts": 1.0,
                                 "text": "定不定?", "anchor": None, "sent": True})
        lib.append_event(rpath, {"type": "answer", "re": "c-0001", "ts": 2.0,
                                 "mode": "reply", "summary": "定了: 方案甲"})
        lib.append_event(rpath, {"type": "graduate", "re": "c-0001", "ts": 3.0,
                                 "dest": "HANDOFF.md open#3", "note": "方案甲"})
        ths, _ = lib.fold_threads(doc)
        th = ths[0]
        assert th["graduated"] == {"dest": "HANDOFF.md open#3", "note": "方案甲", "ts": 3.0}
        assert th["status"] == "answered"              # 状态机不动
        assert th["turns"][-1]["kind"] == "graduate"
        lib.append_event(cpath, {"type": "resolve", "id": "c-0001", "ts": 4.0})
        assert lib.fold_threads(doc)[0][0]["status"] == "resolved"   # resolve 仍归他
        # legacy: 无 graduate 的 thread 字段为 None
        lib.append_event(cpath, {"type": "comment", "id": "c-0002", "ts": 5.0,
                                 "text": "另一条", "anchor": None})
        assert lib.fold_threads(doc)[0][1]["graduated"] is None


def test_archived_skipped():
    """v2.1: archived 文档不进 pending/draft_counts — wait 不被封存文档吵醒."""
    with tempfile.TemporaryDirectory() as d:
        doc = _mkdoc(d)
        cpath, _ = lib.sidecar_paths(doc)
        lib.append_event(cpath, {"type": "comment", "id": "c-0001", "ts": 1.0,
                                 "text": "问", "anchor": None, "sent": True})
        lib.append_event(cpath, {"type": "comment", "id": "c-0002", "ts": 2.0,
                                 "text": "草稿", "anchor": None})
        live = {"z": {"path": doc}}
        frozen = {"z": {"path": doc, "archived": True}}
        assert len(lib.pending(live)) == 1 and lib.pending(frozen) == []
        assert len(lib.draft_counts(live)) == 1 and lib.draft_counts(frozen) == []


# ---------- v2.3: seen / coverage / baseline 泛化 / note / claims ----------

def test_seen_coverage_baseline():
    """契约 v2.3: 沉默=没看; 评论过的块+看完了那版全部块=看过; 改写=自动未看;
    无回合的纯即发文档也有装饰基线 (=最后一次发言的 rev)."""
    real_now = lib.now_ts
    clock = [2000.0]
    lib.now_ts = lambda: clock[0]
    try:
        with tempfile.TemporaryDirectory() as d:
            doc = _mkdoc(d)
            docid = "_test_seen"
            cpath, rpath = lib.sidecar_paths(doc)
            blocks = lib.split_blocks(lib.read_doc(doc))
            cov = lib.coverage(doc, docid, blocks)
            assert cov["n_seen"] == 0 and cov["n_total"] == len(blocks) - 1, cov  # frontmatter 不计
            clock[0] += 1
            r0 = lib.ensure_rev(doc, docid, cpath, "commenting", "server")
            b2 = blocks[2]
            clock[0] += 1
            lib.append_event(cpath, {"type": "comment", "id": "c-0001", "ts": clock[0], "sent": True,
                                     "anchor": {"slug": b2["slug"], "hash": b2["hash"],
                                                "quote": "x", "rev": r0}, "text": "问"})
            cov = lib.coverage(doc, docid, blocks)
            assert cov["n_seen"] == 1 and all(u["i"] != 2 for u in cov["unseen"]), cov
            # 无回合: 基线 = 即发评论的 rev ⇒ 改文档后装饰能出来 (B9 ③)
            assert lib.baseline_rev(lib.all_events(doc)) == r0
            open(doc, "w").write(MD + "\n新增一段。\n")
            nb = lib.split_blocks(lib.read_doc(doc))
            chg, causes, r, stats, base = lib.decoration(doc, docid, nb)
            assert r is None and base == r0 and list(chg.values()) == ["new"], (r, base, chg)
            # 看完了: 记下这版全部块 ⇒ 全看过; 基线前移到这版
            clock[0] += 1
            r1 = lib.ensure_rev(doc, docid, cpath, "seen", "server")
            lib.append_event(cpath, {"type": "seen", "ts": clock[0], "rev": r1,
                                     "hashes": [b["hash"] for b in nb]})
            cov = lib.coverage(doc, docid, nb)
            assert cov["n_seen"] == cov["n_total"], cov
            assert lib.baseline_rev(lib.all_events(doc)) == r1
            chg, *_ = lib.decoration(doc, docid, nb)
            assert chg == {}
            # 改写一块 ⇒ 那块自动回到未看 (hash 变了), 其它仍看过
            cur = lib.read_doc(doc)          # 先读再写 (open("w") 先截断, 同一表达式里读会读到空)
            open(doc, "w").write(cur.replace("第一段文字, 有内容。", "第一段改了。"))
            nb2 = lib.split_blocks(lib.read_doc(doc))
            cov = lib.coverage(doc, docid, nb2)
            assert cov["n_total"] - cov["n_seen"] == 1 and cov["unseen"][0]["i"] == 2, cov
            # approve 隐含 seen (快照里的块全算看过)
            import shutil
            shutil.rmtree(os.path.join(lib.SNAPDIR, docid), ignore_errors=True)
    finally:
        lib.now_ts = real_now


def test_note_private():
    """kind:note = 私有笔记: 永远 draft, submit 跳过, draft_counts 不计, pending 不见."""
    with tempfile.TemporaryDirectory() as d:
        doc = _mkdoc(d)
        cpath, _ = lib.sidecar_paths(doc)
        lib.append_event(cpath, {"type": "comment", "id": "c-0001", "ts": 1.0, "kind": "note",
                                 "anchor": None, "text": "session demo-session 书签"})
        lib.append_event(cpath, {"type": "comment", "id": "c-0002", "ts": 2.0,
                                 "anchor": None, "text": "真草稿"})
        lib.append_event(cpath, {"type": "submit", "batch": 1, "ts": 3.0,
                                 "ids": ["c-0001", "c-0002"]})
        ths, _ = lib.fold_threads(doc)
        st = {t["id"]: t["status"] for t in ths}
        assert st == {"c-0001": "draft", "c-0002": "open"}, st
        reg = {"n": {"path": doc}}
        assert lib.draft_counts(reg) == []                       # 笔记不催
        assert [i["id"] for i in lib.pending(reg)] == ["c-0002"]  # 笔记永不进消费面
        assert "书签" not in json.dumps(lib.pending(reg), ensure_ascii=False)


def test_claims_alive():
    now = 1000.0
    assert lib.claim_alive({"beat": now - 10, "host": "x", "pid": 1}, now)
    assert not lib.claim_alive({"beat": now - 1000, "host": "nowhere", "pid": 999999}, now)
    me = lib.session_self()
    assert me["host"] and me["name"] and "pid" in me


def test_search_exact():
    """v2.4 F14: 全角/大小写归一命中块+评论+外链答案, 深链正确, note 也可被搜 (自己的笔记自己搜得到)."""
    with tempfile.TemporaryDirectory() as d:
        doc = os.path.join(d, "s.md")
        open(doc, "w").write("# 头\n\n量子 Bogo 排序器的复杂度。\n")
        cpath, rpath = lib.sidecar_paths(doc)
        lib.append_event(cpath, {"type": "comment", "id": "c-0001", "ts": 1.0, "sent": True,
                                 "anchor": None, "text": "ＢＯＧＯ 到底靠谱吗"})
        lib.append_event(rpath, {"type": "answer", "re": "c-0001", "ts": 2.0, "mode": "reply",
                                 "summary": "bogo 不靠谱"})
        os.makedirs(doc + ".answers")
        open(doc + ".answers/c-0001-x.md", "w").write("Bogo 全文论证……\n")
        reg = {"sdoc": {"path": doc, "title": "s"}}
        real = lib.load_registry
        lib.load_registry = lambda: reg
        try:
            hits = lib.search("bogo")
            kinds = sorted(h["kind"] for h in hits)
            assert kinds == ["answer", "answer", "block", "comment"], hits
            assert any(h["url"].startswith("/d/sdoc#b-") for h in hits)
            assert any(h["url"].startswith("/x/sdoc/") for h in hits)
            assert lib.search("BOGO 到底") and lib.search("量子")
            assert lib.search("不存在词汇") == []
        finally:
            lib.load_registry = real


if __name__ == "__main__":
    test_blocks()
    test_anchor()
    test_fold()
    test_diff_golden()
    test_anchor_v1_forward()
    test_rounds_and_drafts()
    test_followup_multiturn()
    test_followup_draft_invisible()
    test_sent_comment_no_round()
    test_deliver_no_round()
    test_sel_resolve()
    test_legacy_replay_turns()
    test_graduate()
    test_archived_skipped()
    test_seen_coverage_baseline()
    test_note_private()
    test_claims_alive()
    test_search_exact()
    print("ALL GREEN")
