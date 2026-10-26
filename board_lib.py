#!/usr/bin/env python3
"""review-board 共享库: 块解析 / 块级 diff / 版本快照 / 事件折叠 / sidecar IO / 注册表.

契约 = contracts/comments.md (v1). 不变量: 本库只读 doc 源文件,
只 append sidecar + 写内容寻址快照缓存 — 渲染层永不改源 md 字节.
"""
import difflib
import hashlib
import json
import os
import re
import sys
import time
import tempfile

ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
from board_platform import DEFAULT_LOCK_TIMEOUT, file_lock, pid_alive, replace_file

DATA_ROOT = os.path.abspath(os.path.expanduser(os.environ.get("RB_DATA_DIR") or ROOT))
# Registry, snapshots and process claims are local to this installation.
REGISTRY = os.path.join(DATA_ROOT, "registry.local.json")
_LEGACY_REGISTRY = os.path.join(DATA_ROOT, "registry.json")
SNAPDIR = os.path.join(DATA_ROOT, ".snapshots")
# Claims are live local state, separate from review events.
CLAIMS = os.path.join(DATA_ROOT, "claims.local.json")
SESSIONS_DIR = ""  # No private harness registry is consulted.
CLAIM_TTL = 90.0      # 心跳超过它且 pid 不在 = 认领作废

# ---------- v2.5 B12: 热路径 memo (3s 轮询 × 每 thread 全量块 diff = CPU 吃满, 09-07 实测 8.5s/req) ----------
# 键控原则: 内容寻址 (sha/块序列) 或 (mtime_ns, size) — 键即有效性, 无 TTL 无失效逻辑。
_MEMO_CAP = 256


def _memo_put(m, k, v):
    if len(m) >= _MEMO_CAP:
        for old in list(m)[:_MEMO_CAP // 4]:
            m.pop(old, None)
    m[k] = v
    return v


_EV_MEMO, _SNAP_MEMO, _DIFF_MEMO, _ANCH_MEMO, _SEEN_MEMO = {}, {}, {}, {}, {}


def _fstat(path):
    try:
        st = os.stat(path)
        return (st.st_mtime_ns, st.st_size)
    except OSError:
        return None


def _blockseq(blocks):
    return tuple(b["hash"] for b in blocks)

SIM_THRESHOLD = 0.5   # 块配对相似度门槛 (低于此视为不同块, 落 add/del)
PAIR_MAX_GAP = 60     # 空隙两侧都超过它就放弃配对 (防 O(n*m) 爆炸)


# ---------- registry ----------

def load_registry():
    if not os.path.exists(REGISTRY) and os.path.exists(_LEGACY_REGISTRY):
        import shutil
        shutil.copy(_LEGACY_REGISTRY, REGISTRY)   # 一次性迁移, 旧条目原样带过来
    if not os.path.exists(REGISTRY):
        return {}
    with open(REGISTRY, encoding="utf-8") as f:
        return json.load(f)


def save_registry(reg):
    _save_json(REGISTRY, reg)


# ---------- block parsing ----------

def slugify(text):
    text = re.sub(r"[`*_~\[\]()#]", "", text.strip().lower())
    text = re.sub(r"\s+", "-", text)
    text = re.sub(r"[^\w\-一-鿿]", "", text)
    return text[:60] or "sec"


def normalize(text):
    return " ".join(text.split())


def block_hash(text):
    return hashlib.sha1(normalize(text).encode("utf-8")).hexdigest()[:8]


def split_blocks(md_text):
    """切块: frontmatter / 标题 / 围栏代码块 / 空行分隔段.

    保守切法 (列表内空行会拆成多块 — 可接受, 锚点仍稳定).
    返回 [{i, slug, hash, md}]; slug = 所在节标题 slug (+~n 去重)。
    """
    lines = md_text.split("\n")
    raw = []  # (start, end) 半开行区间
    cur_start = None
    in_fence = False
    fence = ""
    i = 0
    n = len(lines)
    if lines and lines[0].strip() == "---":
        for j in range(1, n):
            if lines[j].strip() == "---":
                raw.append((0, j + 1))
                i = j + 1
                break
    while i < n:
        line = lines[i]
        s = line.strip()
        if in_fence:
            if s.startswith(fence):
                raw.append((cur_start, i + 1))
                cur_start = None
                in_fence = False
            i += 1
            continue
        if s.startswith("```") or s.startswith("~~~"):
            if cur_start is not None:
                raw.append((cur_start, i))
            cur_start = i
            in_fence = True
            fence = s[:3]
            i += 1
            continue
        if not s:
            if cur_start is not None:
                raw.append((cur_start, i))
                cur_start = None
            i += 1
            continue
        if line.startswith("#"):
            if cur_start is not None:
                raw.append((cur_start, i))
            raw.append((i, i + 1))
            cur_start = None
            i += 1
            continue
        if cur_start is None:
            cur_start = i
        i += 1
    if cur_start is not None:
        raw.append((cur_start, n))

    blocks = []
    heading = ""
    sec_count = {}
    for (a, b) in raw:
        md = "\n".join(lines[a:b])
        if lines[a].startswith("#") and b == a + 1:
            heading = slugify(lines[a].lstrip("#"))
            slug = heading
        else:
            slug = heading or "top"
        k = sec_count.get(slug, 0)
        sec_count[slug] = k + 1
        blocks.append({
            "i": len(blocks),
            "slug": slug if k == 0 else "%s~%d" % (slug, k),
            "hash": block_hash(md),
            "md": md,
        })
    return blocks


def resolve_anchor(anchor, blocks):
    """anchor -> block index 或 None. 一档 hash 精确, 二档 quote 包含; 不猜三档
    (失配进未锚定托盘, 永不硬改 — 契约 trap #1)."""
    if not anchor:
        return None
    for b in blocks:
        if b["hash"] == anchor.get("hash"):
            return b["i"]
    q = normalize(anchor.get("quote") or "")
    if len(q) >= 12:
        for b in blocks:
            if q in normalize(b["md"]):
                return b["i"]
    return None


# ---------- 块级 diff (契约 v1: 比较单位是块不是行) ----------

def _sim(a_md, b_md):
    a, b = normalize(a_md), normalize(b_md)
    sm = difflib.SequenceMatcher(a=a, b=b, autojunk=False)
    if sm.real_quick_ratio() < SIM_THRESHOLD or sm.quick_ratio() < SIM_THRESHOLD:
        return 0.0  # 便宜的上界先筛掉, 避免对每对块跑完整 ratio
    return sm.ratio()


def _pair_gap(old_blocks, new_blocks, oi, nj):
    """空隙内贪心配对 -> {new_idx: (old_idx, score)}; 同 slug 加权."""
    if not oi or not nj or (len(oi) > PAIR_MAX_GAP and len(nj) > PAIR_MAX_GAP):
        return {}
    cand = []
    for j in nj:
        for i in oi:
            raw = _sim(old_blocks[i]["md"], new_blocks[j]["md"])
            score = raw
            if old_blocks[i]["slug"] == new_blocks[j]["slug"]:
                score = min(1.0, raw + 0.15)   # 同 slug 加权只用于配对判定
            if score >= SIM_THRESHOLD:
                cand.append((score, j, i, raw))
    cand.sort(reverse=True)
    used_o, used_n, out = set(), set(), {}
    for score, j, i, raw in cand:
        if i in used_o or j in used_n:
            continue
        used_o.add(i)
        used_n.add(j)
        out[j] = (i, raw)                      # 对外报**真实**相似度, 不报加权分
    return out


def diff_blocks(old_blocks, new_blocks):
    """-> [{op: same|mod|add|del, old: idx|None, new: idx|None, sim}] (大致按新文档顺序).

    先在**块 hash 序列**上取匹配段 (= same), 段间空隙按 slug+相似度配对为 mod,
    余下落 add/del。行 diff 对散文是错粒度 (改一段方案 = 整段替换)。
    """
    a = [b["hash"] for b in old_blocks]
    b = [x["hash"] for x in new_blocks]
    ops = []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(
            a=a, b=b, autojunk=False).get_opcodes():
        if tag == "equal":
            for k in range(i2 - i1):
                ops.append({"op": "same", "old": i1 + k, "new": j1 + k, "sim": 1.0})
            continue
        oi, nj = list(range(i1, i2)), list(range(j1, j2))
        pairs = _pair_gap(old_blocks, new_blocks, oi, nj)
        for j in nj:
            if j in pairs:
                i, s = pairs[j]
                ops.append({"op": "mod", "old": i, "new": j, "sim": round(s, 3)})
            else:
                ops.append({"op": "add", "old": None, "new": j, "sim": 0.0})
        for i in oi:
            if not any(p[0] == i for p in pairs.values()):
                ops.append({"op": "del", "old": i, "new": None, "sim": 0.0})
    return ops


# ---------- 版本 (rev) 与快照缓存 ----------

def doc_sha(text):
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:12]


def read_doc(path):
    try:
        with open(path, encoding="utf-8") as f:
            return f.read()
    except OSError:
        return None


def valid_docid(docid):
    return (isinstance(docid, str) and 0 < len(docid) <= 255
            and docid not in (".", "..") and docid == docid.rstrip(" .")
            and not re.search(r'[\x00-\x1f<>:"/\\|?*]', docid)
            and not re.match(r'(?i)^(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\.|$)', docid))



def _snapshot_path(docid, sha):
    if not valid_docid(docid) or not isinstance(sha, str) or not re.fullmatch(r"[0-9a-f]{12}", sha):
        return None
    root = os.path.realpath(SNAPDIR)
    path = os.path.realpath(os.path.join(root, docid, sha + ".md"))
    try:
        return path if os.path.normcase(os.path.commonpath([root, path])) == os.path.normcase(root) else None
    except ValueError:
        return None



def snapshot_put(docid, text):
    """内容寻址快照 (缓存, 非第二真源): 按自身哈希命名 ⇒ 不可能与真源分歧."""
    sha = doc_sha(text)
    p = _snapshot_path(docid, sha)
    if p is None:
        raise ValueError("invalid or escaping snapshot path")
    if not os.path.exists(p):
        os.makedirs(os.path.dirname(p), exist_ok=True)
        _atomic_text(p, text)
    return sha


def snapshot_get(docid, sha):
    p = _snapshot_path(docid, sha)
    return read_doc(p) if p is not None and os.path.isfile(p) else None


def snapshot_blocks(docid, sha):
    if _snapshot_path(docid, sha) is None:
        return None
    hit = _SNAP_MEMO.get((docid, sha))
    if hit is not None:
        return hit
    md = snapshot_get(docid, sha)
    if md is None:
        return None          # 只缓存命中 (快照可能稍后才落盘); 内容寻址 ⇒ 命中永远有效
    return _memo_put(_SNAP_MEMO, (docid, sha), split_blocks(md))


def all_events(doc_path):
    cpath, rpath = sidecar_paths(doc_path)
    evs = read_events(cpath) + read_events(rpath)
    evs.sort(key=lambda e: e.get("ts", 0))
    return evs


def last_rev(evs):
    for e in reversed(evs):
        if e.get("type") == "rev":
            return e
    return None


def ensure_rev(doc_path, docid, sidecar_path, note, by, commit=None):
    """文档当前版本与最后一条 rev 不同则追加 rev 事件 (幂等); 总是落快照. -> sha|None.

    rev 只在回合边界产生 (提交/批准/答复), 不追每次保存 — 回合粒度正是要的粒度。
    写者必须传自己那半 sidecar (single-writer-per-file)。
    """
    md = read_doc(doc_path)
    if md is None:
        return None
    sha = snapshot_put(docid, md)
    lr = last_rev(all_events(doc_path))
    if lr and lr.get("sha") == sha:
        return sha
    append_event(sidecar_path, {"type": "rev", "sha": sha, "ts": now_ts(),
                                "commit": commit, "by": by, "note": note})
    return sha


def resolve_anchor_v1(anchor, blocks, docid):
    """-> (block_idx|None, status) with status ∈ ok|modified|deleted|unknown.

    契约 v1 §锚点: 失配不再一律进托盘 —— 借 anchor.rev 的快照做前向映射,
    把「你的意见被采纳所以块被改写」(modified) 从「文档漂移/块没了」里摘出来。
    """
    if not anchor:
        return None, "unknown"
    i = resolve_anchor(anchor, blocks)
    if i is not None:
        return i, "ok"
    # v2.5 B12: 锚解析 memo — 同一 (锚, 当前块序列) 的失配解析只算一次 (3s 轮询的大头)
    akey = (docid, anchor.get("rev"), anchor.get("hash"),
            (anchor.get("quote") or "")[:80], _blockseq(blocks))
    hit = _ANCH_MEMO.get(akey)
    if hit is not None:
        return hit
    old_blocks = snapshot_blocks(docid, anchor.get("rev"))
    if not old_blocks:
        return None, "unknown"          # 不缓存: 快照可能稍后落盘
    oi = resolve_anchor(anchor, old_blocks)
    if oi is None:
        return _memo_put(_ANCH_MEMO, akey, (None, "unknown"))
    dkey = (docid, anchor.get("rev"), _blockseq(blocks))
    ops = _DIFF_MEMO.get(dkey)
    if ops is None:
        # 一份文档同一 rev 的几十条评论共享同一个 diff (todos-decisions: 62 锚只有 4 个 rev)
        ops = _memo_put(_DIFF_MEMO, dkey, diff_blocks(old_blocks, blocks))
    out = (None, "deleted")
    for op in ops:
        if op["old"] == oi:
            if op["op"] == "mod":
                out = (op["new"], "modified")
            elif op["op"] == "same":
                out = (op["new"], "ok")  # 兜底: 理论上第一档已命中
            break
    return _memo_put(_ANCH_MEMO, akey, out)


# ---------- sidecar IO (append-only, single-writer-per-file) ----------

def sidecar_paths(doc_path):
    """comments = server/人写; replies = agent/session 写."""
    return doc_path + ".comments.jsonl", doc_path + ".replies.jsonl"


def read_events(path):
    """v2.5: (mtime_ns, size) 键控 memo — 消费方义务**只读**返回值 (fold 只读它, 别改)."""
    st = _fstat(path)
    if st is None:
        return []
    hit = _EV_MEMO.get(path)
    if hit and hit[0] == st:
        return hit[1]
    out = []
    try:
        f = open(path, encoding="utf-8")
    except OSError as e:
        # 不可读的 sidecar (macOS TCC 拒读 ~/Documents 等) 视为无事件 —
        # 与"坏行跳过"同一容错原则: 一篇文档不该炸掉全板 fold (pending/wait 全体阵亡)
        print(f"[board_lib] warn: unreadable sidecar {path}: {e}", file=sys.stderr)
        return []
    with f, file_lock(f, exclusive=False):
        for line in f:
            line = line.strip()
            if line:
                try:
                    out.append(json.loads(line))
                except ValueError:
                    pass  # 容错: 坏行跳过, 不炸
    _memo_put(_EV_MEMO, path, (st, out))
    return out


def append_event(path, ev, *, timeout=DEFAULT_LOCK_TIMEOUT):
    line = (json.dumps(ev, ensure_ascii=False) + "\n").encode("utf-8")
    # Unbuffered IO avoids a close-time retry flushing bytes after lock release
    # if a write fails. Short writes are completed while the lock is still held.
    with open(path, "ab", buffering=0) as f:
        with file_lock(f, timeout=timeout):
            rest = memoryview(line)
            while rest:
                written = f.write(rest)
                if not written:
                    raise OSError("event-file write made no progress")
                rest = rest[written:]


def next_id(evs, prefix):
    mx = 0
    for e in evs:
        m = re.match(r"^%s-(\d+)$" % prefix, e.get("id") or "")
        if m:
            mx = max(mx, int(m.group(1)))
    return "%s-%04d" % (prefix, mx + 1)


# ---------- thread folding (状态机: draft→open→answered→resolved, reopen→open) ----------

def fold_threads(doc_path):
    """v2: thread 带有序 turns (followup/reply/answer 各一轮; 根 comment 在返回前置顶).

    followup(sent)/deliver 把 thread 翻回 open (追问=隐式 reopen) — pending/wait
    唤醒链零改动复用。summary/answer 头 last-writer-wins (卡片一行结论)。
    """
    evs = all_events(doc_path)
    threads = {}
    order = []
    nbatch = 0
    dropped = set()
    fturn = {}     # f-id -> (turn, thread) — followup 的 edit/drop/deliver 寻址
    for e in evs:
        t = e.get("type")
        if t == "comment":
            threads[e["id"]] = {
                "id": e["id"], "ts": e["ts"],
                "author": e.get("author") or "Reviewer",
                "kind": e.get("kind") or "block", "re_batch": e.get("re_batch"),
                "intent": e.get("intent") if e.get("kind") != "note" else None,
                "anchor": e.get("anchor"), "text": e.get("text", ""),
                # v2: sent=true 即发单条 — 生而 open, 永不入回合 (batch 恒 null)
                "status": "open" if e.get("sent") else "draft",
                "batch": None, "replies": [],
                "summary": None, "answer": None, "turns": [], "graduated": None,
            }
            order.append(e["id"])
        elif t in ("edit", "drop"):
            # 只在目标仍是 draft 时生效 (契约): 已送达的发言不可撤不可改
            re_ = e.get("re") or ""
            if re_.startswith("f-"):
                pair = fturn.get(re_)
                if pair and pair[0].get("draft"):
                    if t == "drop":
                        pair[0]["dropped"] = True
                    else:
                        pair[0]["text"] = e.get("text", pair[0]["text"])
                continue
            th = threads.get(re_)
            if th and th["status"] == "draft":
                if t == "drop":
                    dropped.add(th["id"])
                else:
                    th["text"] = e.get("text", th["text"])
        elif t == "submit":
            nbatch = max(nbatch, e.get("batch", 0))
            for cid in e.get("ids", []):
                th = threads.get(cid)
                if th and th["status"] == "draft" and th["kind"] != "note":
                    th["status"] = "open"           # v2.3: note 永远 draft, 永不 submit
                    th["batch"] = e.get("batch")
        elif t == "followup":
            th = threads.get(e.get("re"))
            if th:
                turn = {"kind": "followup", "id": e.get("id"), "ts": e["ts"],
                        "author": e.get("author") or "Reviewer",
                        "text": e.get("text", ""), "draft": not e.get("sent")}
                th["turns"].append(turn)
                fturn[e.get("id")] = (turn, th)
                if e.get("sent"):
                    th["status"] = "open"
        elif t == "deliver":
            for fid in e.get("fids") or []:
                pair = fturn.get(fid)
                if pair and pair[0].get("draft") and not pair[0].get("dropped"):
                    pair[0]["draft"] = False
                    pair[1]["status"] = "open"
        elif t == "reply":
            th = threads.get(e.get("re"))
            if th:
                r = {"id": e.get("id"), "ts": e["ts"],
                     "author": e.get("author") or "agent",
                     "text": e.get("text", ""), "session": e.get("session")}
                if e.get("doc_ref"):
                    r["doc_ref"] = e["doc_ref"]
                th["replies"].append(r)
                th["turns"].append(dict(r, kind="reply"))
        elif t == "answer":
            th = threads.get(e.get("re"))
            if th:
                th["summary"] = e.get("summary") or th["summary"]
                th["answer"] = {"mode": e.get("mode", "reply"),
                                "commit": e.get("commit"), "ts": e["ts"],
                                "doc_ref": e.get("doc_ref")}
                th["turns"].append({
                    "kind": "answer", "ts": e["ts"], "mode": e.get("mode", "reply"),
                    "commit": e.get("commit"), "rev": e.get("rev"),
                    "summary": e.get("summary"), "doc_ref": e.get("doc_ref"),
                    "session": e.get("session")})
                if th["status"] == "open":
                    th["status"] = "answered"
        elif t == "explore":
            th = threads.get(e.get("re"))
            if th and th["status"] != "draft" and th["kind"] != "note":
                th["intent"] = "explore"
        elif t == "graduate":
            # v2.1: 毕业指针 — 标记不是状态 (resolve 仍归他, 状态机不动)
            th = threads.get(e.get("re"))
            if th:
                th["graduated"] = {"dest": e.get("dest"), "note": e.get("note"),
                                   "ts": e["ts"]}
                th["turns"].append({"kind": "graduate", "ts": e["ts"],
                                    "dest": e.get("dest"), "note": e.get("note")})
        elif t == "resolve":
            th = threads.get(e.get("id"))
            if th:
                th["status"] = "resolved"
        elif t == "reopen":
            th = threads.get(e.get("id"))
            if th and th["status"] in ("resolved", "answered"):
                th["status"] = "open"
    out = []
    for cid in order:
        if cid in dropped:
            continue
        th = threads[cid]
        # 根 comment 置顶成 turns[0] (读文本引用同一份 — draft edit 已在上面生效)
        th["turns"] = [{"kind": "comment", "id": th["id"], "ts": th["ts"],
                        "author": th["author"], "text": th["text"]}] + \
                      [t for t in th["turns"] if not t.get("dropped")]
        out.append(th)
    return out, nbatch


# ---------- 回合 (round): submit → answers → approve, 折叠推导不落盘 ----------

def fold_rounds(doc_path):
    """-> [{batch, submit_ts, rev_before, ids, answered_ids, rev_after,
            status: open|answered|approved, approve_rev, approve_note}]"""
    evs = all_events(doc_path)
    rounds = []
    by_batch = {}
    cur_sha = None
    for e in evs:
        t = e.get("type")
        if t == "rev":
            cur_sha = e.get("sha")
        elif t == "comment" and e.get("kind") == "reject":
            r = by_batch.get(e.get("re_batch"))
            if r and r["status"] != "approved":
                r["status"] = "rejected"       # 被打回的回合不再等他批准
        elif t == "submit":
            if not e.get("ids"):
                continue        # 防御: 空批不开回合 (server 不会写, 手写事件兜底)
            r = {"batch": e.get("batch"), "submit_ts": e.get("ts", 0),
                 "rev_before": cur_sha, "ids": list(e.get("ids") or []),
                 "answered_ids": [], "edited_ids": [], "rev_after": None,
                 "status": "open", "approve_rev": None, "approve_note": None,
                 "approve_ts": None}
            rounds.append(r)
            by_batch[r["batch"]] = r
        elif t == "answer":
            cid = e.get("re")
            for r in reversed(rounds):
                if cid in r["ids"]:
                    if cid not in r["answered_ids"]:
                        r["answered_ids"].append(cid)
                    if e.get("mode") == "edit" and cid not in r["edited_ids"]:
                        r["edited_ids"].append(cid)
                    r["rev_after"] = e.get("rev") or cur_sha or r["rev_after"]
                    break
        elif t == "approve":
            r = by_batch.get(e.get("batch"))
            if r:
                r["status"] = "approved"
                r["approve_rev"] = e.get("rev") or cur_sha
                r["approve_note"] = e.get("note")
                r["approve_ts"] = e.get("ts")
    for r in rounds:
        if r["status"] == "open" and r["ids"] and \
                len(r["answered_ids"]) >= len(r["ids"]):
            r["status"] = "answered"
    return rounds


def decoration(doc_path, docid, blocks):
    """常驻改动装饰: 每块相对 baseline 的 chg + 本回合归因 causes.

    v2.3 基线泛化 (契约): baseline = **最后一次人类发言时的 rev** (submit / approve / 即发 /
    追问 / 看完了) = 「自我上次发言以来变了什么」— 无回合的纯即发文档从此也有基线 (B9 ③);
    有回合未批准时与 v1 等价 (submit 的 rev_before); approve 后基线 = approve_rev ⇒ 清场。
    -> ({block_idx: "new"|"mod"}, causes[list of cid], round|None, stats|None, base|None)
    """
    rounds = fold_rounds(doc_path)
    r = rounds[-1] if rounds else None
    base = baseline_rev(all_events(doc_path))
    if not base:
        return {}, [], r, None, None
    old_blocks = snapshot_blocks(docid, base)
    if not old_blocks:
        return {}, [], r, None, base
    chg, stats = {}, {"same": 0, "mod": 0, "add": 0, "del": 0}
    for op in diff_blocks(old_blocks, blocks):
        stats[op["op"]] = stats.get(op["op"], 0) + 1
        if op["op"] == "mod":
            chg[op["new"]] = "mod"
        elif op["op"] == "add":
            chg[op["new"]] = "new"
    causes = r["edited_ids"] if r and r["status"] != "approved" else []
    return chg, causes, r, stats, base


def baseline_rev(evs):
    """装饰基线 = 最后一次人类发言时的 rev (契约 v2.3). 发言 = sent comment (anchor.rev) /
    sent followup (rev) / submit / deliver / approve / seen. 草稿不算发言 (submit 才算)。"""
    cur, base = None, None
    for e in evs:
        t = e.get("type")
        if t == "rev":
            cur = e.get("sha")
        elif t == "comment" and e.get("sent") and e.get("kind") != "note":
            base = (e.get("anchor") or {}).get("rev") or cur
        elif t == "followup" and e.get("sent"):
            base = e.get("rev") or cur
        elif t in ("submit", "deliver"):
            base = cur
        elif t in ("approve", "seen"):
            base = e.get("rev") or cur
    return base


# ---------- v2.3: 看过集合 / 覆盖率 (沉默 = 没看; 看完了 = 一键) ----------

def seen_hashes(doc_path, docid):
    """看过集合 = seen.hashes ∪ approve.rev 快照的块 hash ∪ block 评论 (含笔记) 的 anchor.hash.
    块改写 = 新 hash = 自动回到未看 — 零簿记 (契约 v2.3)。v2.5: sidecar stat 键控 memo。"""
    cpath, rpath = sidecar_paths(doc_path)
    skey = (docid, _fstat(cpath), _fstat(rpath))
    hit = _SEEN_MEMO.get(skey)
    if hit is not None:
        return hit
    out = set()
    for e in all_events(doc_path):
        t = e.get("type")
        if t == "seen":
            out.update(e.get("hashes") or [])
        elif t == "approve":
            bl = snapshot_blocks(docid, e.get("rev"))
            if bl:
                out.update(b["hash"] for b in bl)
        elif t == "comment" and e.get("kind", "block") in ("block", "note"):
            h = (e.get("anchor") or {}).get("hash")
            if h:
                out.add(h)
    return _memo_put(_SEEN_MEMO, skey, out)


def coverage(doc_path, docid, blocks):
    """-> {n_seen, n_total, unseen: [{i, slug, hash}]} (frontmatter 块不计)."""
    seen = seen_hashes(doc_path, docid)
    unseen, total = [], 0
    for b in blocks:
        if b["i"] == 0 and b["md"].startswith("---"):
            continue
        total += 1
        if b["hash"] not in seen:
            unseen.append({"i": b["i"], "slug": b.get("slug"), "hash": b["hash"]})
    return {"n_seen": total - len(unseen), "n_total": total, "unseen": unseen}


# ---------- v2.3: 认领 (机器本地活状态) + session 身份 ----------

def _load_json(p, dflt):
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return dflt


def _atomic_text(p, text):
    parent = os.path.dirname(os.path.abspath(p))
    os.makedirs(parent, exist_ok=True)
    tmp = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="\n",
                                         dir=parent, suffix=".tmp", delete=False) as f:
            tmp = f.name
            f.write(text)
        replace_file(tmp, p)
    finally:
        if tmp is not None and os.path.exists(tmp):
            os.unlink(tmp)



def _save_json(p, obj):
    _atomic_text(p, json.dumps(obj, ensure_ascii=False, indent=2))


def load_claims():
    return _load_json(CLAIMS, {})


def save_claims(c):
    _save_json(CLAIMS, c)


def machine_name():
    """Optional display alias, otherwise the local short hostname."""
    import platform
    return os.environ.get("RB_HOST") or platform.node().split(".")[0]


def claim_alive(c, now=None):
    """活着 ⇔ 心跳 < TTL, 或同机 pid 还在 (卡住的值守不算死 — 别抢)."""
    now = now if now is not None else time.time()
    if now - (c.get("beat") or 0) < CLAIM_TTL:
        return True
    return c.get("host") == machine_name() and pid_alive(c.get("pid"))


def agent_identity():
    """Keep native harness and session identities distinct; never invent a model."""
    if os.environ.get("RB_AGENT_SESSION"):
        sid, harness = os.environ["RB_AGENT_SESSION"], os.environ.get("RB_AGENT_HARNESS", "unknown")
    elif os.environ.get("CODEX_THREAD_ID"):
        sid, harness = os.environ["CODEX_THREAD_ID"], "codex"
    elif os.environ.get("CLAUDE_CODE_SESSION_ID"):
        sid, harness = os.environ["CLAUDE_CODE_SESSION_ID"], "claude-code"
    else:
        sid, harness = "", "unknown"
    return {"session": sid, "harness": harness,
            "author": os.environ.get("RB_AGENT_NAME") or (harness if harness != "unknown" else "agent")}


def session_self():
    identity = agent_identity()
    cwd = os.getcwd()
    home = os.path.expanduser("~")
    if cwd == home or cwd.startswith(home + os.sep):
        cwd = "~" + cwd[len(home):]
    return {"sid": identity["session"], "harness": identity["harness"],
            "name": identity["author"], "cwd": cwd, "pid": os.getpid(), "host": machine_name()}


def sessions_index():
    """Compatibility hook. No ambient private session files are read."""
    return {}


def session_label(sid, idx=None, host=None):
    """`name@host` (本机活着且有名) / `sid8` (死了或别机) / None (无 sid)."""
    if not sid:
        return None
    idx = sessions_index() if idx is None else idx
    s = idx.get(sid)
    if s and s.get("alive") and s.get("name"):
        return "%s@%s" % (s["name"], host or machine_name())
    return sid[:8]


def exploration_family(reg, docid):
    """Read one document family; only delivered public branch roots leave this projection."""
    root, chain = docid, []
    while True:
        if root in chain:
            root = min(chain[chain.index(root):])  # malformed legacy cycles: deterministic, bounded
            break
        chain.append(root)
        parent = reg[root].get("parent")
        if parent not in reg:
            break
        root = parent
    children = {}
    for key, meta in reg.items():
        children.setdefault(meta.get("parent"), []).append(key)
    family, stack = [], [root]
    while stack:
        key = stack.pop()
        if key in family:
            continue
        family.append(key)
        stack.extend(reversed(children.get(key, [])))
    docs = []
    linked = set()
    for key in family:
        meta = reg[key]
        born = meta.get("born") or {}
        if born.get("doc") and born.get("cid"):
            linked.add((born["doc"], born["cid"]))
        docs.append({"id": key, "parent": meta.get("parent"),
                     "title": meta.get("title") or key, "emoji": meta.get("emoji"),
                     "question": meta.get("question"), "born": born or None,
                     "exists": os.path.isfile(meta["path"]), "archived": bool(meta.get("archived"))})
    branches = []
    for key in family:
        ths, _ = fold_threads(reg[key]["path"])
        for th in ths:
            if th["kind"] == "note" or th["status"] == "draft":
                continue
            if th.get("intent") != "explore" and (key, th["id"]) not in linked:
                continue
            anchor = th.get("anchor") or {}
            branches.append({"doc": key, "id": th["id"], "title": th["text"].split("\n")[0][:120],
                             "quote": (anchor.get("sel") or {}).get("text") or anchor.get("quote") or "",
                             "status": th["status"], "summary": th.get("summary"), "ts": th["ts"]})
    return {"root": root, "current": docid, "docs": docs, "branches": branches}


def pending(reg=None):
    """已提交未回答的评论, 跨全部注册文档 — agent 消费面.

    trap #6 延伸 (v2): draft followup 的正文在这里**单点剥除** —
    boardctl 全部经由本函数, 别在别处再开豁口。
    """
    reg = reg if reg is not None else load_registry()
    out = []
    for docid, meta in reg.items():
        if meta.get("archived"):
            continue        # v2.1: 封存文档不进消费面, wait 不被它吵醒
        ths, _ = fold_threads(meta["path"])
        for th in ths:
            if th["status"] == "open":
                item = dict(th)
                item["doc"] = docid
                item["turns"] = [t for t in th["turns"] if not t.get("draft")]
                out.append(item)
    return out


def draft_counts(reg=None):
    """每份文档的未提交草稿 **计数与最早时间**, 永不返回正文.

    trap #6: 草稿对 agent 不可见 (submit = 他的发言边界)。agent 可以提醒
    "有 N 条没提交" (实测有过一条躺 3.9 天), 但不许读内容。
    v2: draft followup 一并计入。
    """
    reg = reg if reg is not None else load_registry()
    out = []
    for docid, meta in reg.items():
        if meta.get("archived"):
            continue
        ths, _ = fold_threads(meta["path"])
        stamps = [t["ts"] for t in ths if t["status"] == "draft" and t["kind"] != "note"]
        stamps += [turn["ts"] for t in ths for turn in t["turns"]
                   if turn.get("draft")]
        if stamps:
            out.append({"doc": docid, "n": len(stamps),
                        "oldest_ts": min(stamps)})
    return out


def resolve_sel(sel, block_md):
    """划词锚 (anchor.sel) 在**已命中的块**内重定位 -> (pos|None, status).

    sel 永不影响选块 (块级四档在先); 找不到就诚实降级整块 ("gone"), 不猜。
    多处命中取距写入时偏移最近的一处。
    """
    if not isinstance(sel, dict):
        return None, "gone"
    t = normalize(sel.get("text") or "")
    if not t:
        return None, "gone"
    hay = normalize(block_md)
    hits = []
    i = hay.find(t)
    while i != -1:
        hits.append(i)
        i = hay.find(t, i + 1)
    if not hits:
        return None, "gone"
    want = sel.get("start") or 0
    best = min(hits, key=lambda h: abs(h - want))
    return {"start": best, "end": best + len(t)}, "ok"


def now_ts():
    return time.time()


# ---------- v2.4: 精确搜索 (F14 — 折叠卡+外链答案吃掉 ⌘F) ----------

_FW = {i: i - 0xFEE0 for i in range(0xFF01, 0xFF5F)}
_FW[0x3000] = 0x20    # 全角空格


def search_norm(s):
    """搜索归一: 全角→半角, 小写, 空白折叠."""
    return " ".join(str(s).translate(_FW).lower().split())


def _excerpt(text, qn, width=40):
    """在原文里找归一命中行, 回 ±width 字摘录; 找不到回文首."""
    for line in text.splitlines():
        ln = search_norm(line)
        i = ln.find(qn)
        if i >= 0:
            line = " ".join(line.split())
            j = max(0, i - width)
            return ("…" if j else "") + line[j:i + len(qn) + width] +                    ("…" if i + len(qn) + width < len(line) else "")
    t = " ".join(text.split())
    return t[:width * 2] + ("…" if len(t) > width * 2 else "")


def search(q, doc=None, limit=100):
    """精确子串搜索, 扫: 正文逐块 / question / 两 sidecar 的人机文本 / 外链答案 md.
    含 archived (挖坟), 结果标 archived。-> [{doc,title,archived,kind,excerpt,url}]"""
    qn = search_norm(q)
    if len(qn) < 2:
        return []
    reg = load_registry()
    hits = []

    def push(**kw):
        hits.append(kw)
        return len(hits) >= limit

    for docid, meta in reg.items():
        if doc and docid != doc:
            continue
        base = {"doc": docid, "title": meta.get("title") or docid,
                "archived": bool(meta.get("archived"))}
        du = "/d/" + _q(docid)
        if meta.get("question") and qn in search_norm(meta["question"]):
            if push(kind="question", excerpt=_excerpt(meta["question"], qn), url=du, **base):
                return hits
        md = read_doc(meta["path"])
        if md is not None:
            for b in split_blocks(md):
                if qn in search_norm(b["md"]):
                    if push(kind="block", excerpt=_excerpt(b["md"], qn),
                            url="%s#b-%s" % (du, b["hash"]), **base):
                        return hits
        for ev in all_events(meta["path"]):
            t = ev.get("type")
            txt = ev.get("text") if t in ("comment", "followup", "reply") else                   ev.get("summary") if t == "answer" else None
            if txt and qn in search_norm(txt):
                cid = ev.get("id") if t == "comment" else ev.get("re")
                if push(kind="comment" if t in ("comment", "followup") else "answer",
                        excerpt=_excerpt(txt, qn),
                        url="%s#%s" % (du, cid or ""), **base):
                    return hits
        adir = meta["path"] + ".answers"
        if os.path.isdir(adir):
            for fn in sorted(os.listdir(adir)):
                if not fn.endswith(".md"):
                    continue
                t = read_doc(os.path.join(adir, fn))
                if t and qn in search_norm(t):
                    rel = os.path.basename(meta["path"]) + ".answers/" + fn
                    if push(kind="answer", excerpt=_excerpt(t, qn),
                            url="/x/%s/%s" % (_q(docid), _q(rel)), **base):
                        return hits
    return hits


def _q(s):
    import urllib.parse
    return urllib.parse.quote(str(s), safe="")
