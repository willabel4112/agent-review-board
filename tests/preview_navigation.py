#!/usr/bin/env python3
"""Disposable UI fixture with separate registry, sidecars and snapshots.

Run: python3 -B tests/preview_navigation.py
Open the printed URL. Ctrl-C removes the temporary data.
"""
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import board_lib as lib
from server import Handler, ThreadingHTTPServer


def seed(root):
    lib.REGISTRY = str(root / "registry.json")
    lib._LEGACY_REGISTRY = str(root / "no-legacy.json")
    lib.CLAIMS = str(root / "claims.json")
    lib.PEERS = str(root / "peers.json")
    lib.SNAPDIR = str(root / "snapshots")
    lib.SESSIONS_DIR = str(root / "no-sessions")
    doc = root / "demo.md"
    items = ["第一条：范围只含这份文档。", "第二条：折叠章节后仍可回跳。",
             "第三条：同块不同引文需要区分。", "第四条：已解决也能留追问草稿。",
             "第五条：看完了按钮应始终可见。", "第六条：结论需要写回正文。"]
    md = "# 长文评审：同块六条评论\n\n## 六个改进点\n\n"
    md += "\n".join(f"{i + 1}. {s}" for i, s in enumerate(items))
    md += "\n\n### 子章节\n\n这里有 **跨格式 引文** 可以定位。\n\n"
    md += "## 后续章节\n\n这部分可以单独折叠，目录仍然保留。\n\n"
    md += "### 子章节\n\n相同标题仍有独立的目录和折叠身份。\n\n"
    for i in range(12):
        md += f"## 长文分支 {i + 1}\n\n" + "用于检查长页滚动、固定工具栏与回跳。" * 16 + "\n\n"
    doc.write_text(md)
    lib.save_registry({"demo": {"path": str(doc), "title": "长文评审验证 · 标题很长时阅读操作仍保持可见",
                              "emoji": "🧪", "question": "如何在长文和多个分叉之间保持注意力？"}})
    rev = lib.snapshot_put("demo", md)
    blocks = lib.split_blocks(md)
    block = next(b for b in blocks if b["md"].startswith("1. "))
    cp, rp = lib.sidecar_paths(str(doc))
    for i, s in enumerate(items):
        cid = f"c-{i + 1:04d}"
        start = lib.normalize(block["md"]).index(s)
        lib.append_event(cp, {"type": "comment", "id": cid, "ts": 10 + i, "author": "测试读者",
            "kind": "block", "sent": True, "text": f"请处理第 {i + 1} 点",
            "anchor": {"hash": block["hash"], "slug": block["slug"], "quote": s, "rev": rev,
                       "sel": {"text": s, "start": start, "end": start + len(s)}}})
        if i in (1, 3):
            lib.append_event(rp, {"type": "answer", "re": cid, "ts": 30 + i,
                "summary": "原问题已答", "mode": "reply"})
            if i == 3:
                lib.append_event(cp, {"type": "resolve", "id": cid, "ts": 40 + i})
            lib.append_event(cp, {"type": "followup", "id": f"f-{i:04d}", "re": cid, "ts": 50 + i,
                "author": "测试读者", "text": f"第 {i + 1} 点的新追问草稿，请在侧栏明确标出。"})
    rich = next(b for b in blocks if "跨格式" in b["md"])
    lib.append_event(cp, {"type": "comment", "id": "c-0007", "ts": 70, "sent": True,
        "text": "跨 Markdown 格式的选区也能定位", "kind": "block",
        "anchor": {"hash": rich["hash"], "slug": rich["slug"], "quote": "有 跨格式 引文 可以",
                   "rev": rev}})
    child = root / "child.md"
    child.write_text("# 为什么要保留主线？\n\n探索分支可以独立深入，读完后带着结论返回原来的段落。\n\n## 如何返回\n\n使用阅读区顶部的返回按钮；原页面滚动位置和输入框都会保留。\n")
    deeper = root / "deeper.md"
    deeper.write_text("# 一个更深的例子\n\n从问题深入，再回到上一层，所有阅读位置都留在原处。\n")
    reg = lib.load_registry()
    reg["child"] = {"path": str(child), "title": "为什么要保留主线？", "parent": "demo", "born": {"doc": "demo", "cid": "c-0002"}, "emoji": "🌱"}
    reg["deeper"] = {"path": str(deeper), "title": "一个更深的例子", "parent": "child", "emoji": "🔎"}
    lib.save_registry(reg)
    child.write_text(child.read_text() + "\n[继续深入例子](/d/deeper)\n")
    answer = root / "reference.md"
    answer.write_text("# 只读参考\n\n这里保留资料原文，不提供评论。\n")
    lib.append_event(rp, {"type": "answer", "re": "c-0002", "ts": 71,
                         "summary": "整理成了独立正文", "doc_ref": "child.md", "mode": "reply"})
    lib.append_event(rp, {"type": "answer", "re": "c-0007", "ts": 72,
                         "summary": "补充一份参考", "doc_ref": "reference.md", "mode": "reply"})


if __name__ == "__main__":
    with tempfile.TemporaryDirectory(prefix="board-navigation-") as temp:
        seed(Path(temp))
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        print(f"http://127.0.0.1:{server.server_port}/d/demo", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()
