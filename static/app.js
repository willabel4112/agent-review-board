/* review-board viewer v2 — 飞书式行内评论 + 划词锚 + thread 追问 + 增量渲染
   渲染架构: 四个独立门控面 (正文块 / 行内卡层 / 侧栏 / 回合横幅), patchList keyed
   逐卡 reconcile — sig 不变 DOM 不动 ⇒ 选区/焦点/scrollTop 天然存活 (痛点⑤根治)。 */
"use strict";

const _m = location.pathname.match(/^\/(d|x)\/([^/]+)(?:\/(.*))?$/) || [0, "d", ""];
const MODE = _m[1];                                   // d=评审页 | x=外链答案只读页
const docid = decodeURIComponent(_m[2]);
const XREL = _m[3] || "";                             // /x/ 模式下的相对路径 (仍编码)
const FOCUS_THREAD = MODE === "d" && /^c-\d+$/.test(new URLSearchParams(location.search).get("thread") || "")
  ? new URLSearchParams(location.search).get("thread") : null;
const $ = (id) => document.getElementById(id);
const DARK = matchMedia("(prefers-color-scheme: dark)").matches;
const LBL = { open: "待答", draft: "草稿", answered: "已答", resolved: "已解决" };
const RANK = { open: 0, draft: 1, answered: 2, resolved: 3 };
const ASTAT = {
  modified: "✎ 这块已按你的意见改写",
  deleted: "⚠ 这块在新版里没了 (下面是你评论时的原文)",
  unknown: "⚠ 未锚定 · 原文引用",
};

let DOC = null, THREADS = [], ROUND = null, mtime = 0, mmdN = 0;
let mmdPending = 0, mmdCbs = [];
let composeBlock = null, composeMode = "comment", composeSel = null, composeQuote = null;
let composeRev = null;
let expanded = new Set(), openOld = new Set(), inlineClosed = new Set();
let OLDMD = {};              // 当前块 index -> 上一版的 md (装饰点开看旧版)
let VIEW = location.hash === "#diff" ? "diff" : "doc", DIFF = null;
const MARKS = new Map();     // tid -> {bi, status} 已应用的划词高亮
const UNSENT_FOLLOWUPS = new Map(); // In-memory only: survives document rerenders and exploration hops.

function lsGet(k, dflt) { try { return localStorage.getItem(k) ?? dflt; } catch (e) { return dflt; } }
function lsSet(k, v) { try { localStorage.setItem(k, v); } catch (e) { } }
let FILTER = lsGet("rb.filter", "todo");
let activeThread = null, HEADINGS = [], BLOCKIDS = new Map(), tocSig = "";
const foldKey = `rb.sections:${docid}:${XREL}`;
const tocOnKey = new URLSearchParams(location.search).get("embedded") === "1" ? "rb.tocOn:embedded" : "rb.tocOn";
function savedSet(key) {
  try { const v = JSON.parse(lsGet(key, "[]")); return new Set(Array.isArray(v) ? v : []); }
  catch (_) { return new Set(); }
}
const foldedSections = savedSet(foldKey), foldedToc = savedSet(foldKey + ":toc");
function anchorText(t) { return ((t.anchor || {}).sel || {}).text || (t.anchor || {}).quote || ""; }
function blockId(b) {
  return BLOCKIDS.get(b.i) || "b-" + b.hash;
}

/* ---------- sections and outline (view state only; never implies seen) ---------- */
function setSectionFold(h) {
  foldedSections.has(h.key) ? foldedSections.delete(h.key) : foldedSections.add(h.key);
  lsSet(foldKey, JSON.stringify([...foldedSections]));
  syncSections();
}
function revealBlock(i) {
  let changed = false;
  for (const h of RBNav.ancestors(HEADINGS, i))
    if (foldedSections.delete(h.key)) changed = true;
  if (changed) { lsSet(foldKey, JSON.stringify([...foldedSections])); syncSections(); }
}
function jumpBlock(i) {
  if (VIEW !== "doc") setView("doc");
  revealBlock(i);
  const sec = document.querySelector(`.block[data-i="${i}"]`);
  if (sec) { scrollSettle(sec); flash(sec); history.replaceState(null, "", "#" + sec.id); }
  if (matchMedia("(max-width: 1200px)").matches) setToc(false);
}
function setToc(on) {
  document.querySelector("main").classList.toggle("toc-off", !on);
  $("tocToggle").setAttribute("aria-expanded", String(on));
  lsSet(tocOnKey, on ? "1" : "0");
}
function renderOutline() {
  const sig = JSON.stringify(HEADINGS);
  if (sig === tocSig) return;
  tocSig = sig;
  const root = $("toc"); root.replaceChildren();
  const containers = new Map();
  for (const h of HEADINGS) {
    const row = document.createElement("div");
    row.className = "toc-entry";
    const line = document.createElement("div"); line.className = "toc-line";
    const children = document.createElement("div"); children.className = "toc-children";
    const hasChildren = HEADINGS.some(c => c.parent === h.key);
    if (hasChildren) {
      const toggle = document.createElement("button");
      toggle.className = "toc-branch";
      const sync = () => {
        const closed = foldedToc.has(h.key);
        children.hidden = closed; toggle.textContent = closed ? "▸" : "▾";
        toggle.setAttribute("aria-label", `${closed ? "展开" : "折叠"}目录 ${h.text}`);
        toggle.setAttribute("aria-expanded", String(!closed));
      };
      toggle.onclick = () => {
        foldedToc.has(h.key) ? foldedToc.delete(h.key) : foldedToc.add(h.key);
        lsSet(foldKey + ":toc", JSON.stringify([...foldedToc])); sync();
      };
      sync(); line.appendChild(toggle);
    }
    const a = document.createElement("a");
    a.href = "#" + blockId(DOC.blocks[h.i]); a.textContent = h.text; a.title = h.text;
    a.dataset.i = h.i;
    a.onclick = e => { e.preventDefault(); jumpBlock(h.i); };
    line.appendChild(a); row.append(line, children);
    (containers.get(h.parent) || root).appendChild(row);
    containers.set(h.key, children);
  }
}
function syncSections() {
  if (!DOC || VIEW !== "doc") return;
  const hidden = RBNav.hiddenBlocks(HEADINGS, foldedSections);
  for (const el of $("doc").children) {
    const i = el.classList.contains("block") ? +el.dataset.i : +el.dataset.for;
    el.classList.toggle("section-hidden", hidden.has(i));
  }
  for (const h of HEADINGS) {
    const sec = document.querySelector(`.block[data-i="${h.i}"]`);
    if (!sec) continue;
    const closed = foldedSections.has(h.key), btn = sec.querySelector(".section-toggle");
    if (btn) {
      btn.textContent = closed ? "▸" : "▾";
      btn.setAttribute("aria-expanded", String(!closed));
      btn.setAttribute("aria-label", `${closed ? "展开" : "折叠"}章节 ${h.text}`);
    }
    let hint = sec.querySelector(".section-summary");
    if (!hint) { hint = document.createElement("div"); hint.className = "section-summary"; sec.appendChild(hint); }
    hint.hidden = !closed;
    if (closed) {
      const ts = THREADS.filter(t => t.block > h.i && t.block < h.end);
      const drafts = ts.reduce((n, t) => n + (t.status === "draft" && t.kind !== "note" ? 1 : 0) + RBNav.followups(t).drafts, 0);
      hint.textContent = `已折叠 ${h.end - h.i - 1} 块 · ${ts.length} 条评论${drafts ? ` · ${drafts} 条未提交` : ""}`;
    }
  }
}
function syncActiveThread() {
  document.querySelectorAll(".thread-active, .source-active").forEach(el =>
    el.classList.remove("thread-active", "source-active"));
  if (!activeThread) return;
  for (const id of [activeThread, "s-" + activeThread])
    document.getElementById(id)?.classList.add("thread-active");
  document.querySelectorAll(`mark.selmark[data-tid="${activeThread}"]`).forEach(el => el.classList.add("source-active"));
}
function initNavigation() {
  setToc(lsGet(tocOnKey, innerWidth > 1200 ? "1" : "0") === "1");
  $("tocToggle").onclick = () => setToc(document.querySelector("main").classList.contains("toc-off"));
  $("expandSections").onclick = () => {
    foldedSections.clear(); lsSet(foldKey, "[]"); syncSections();
  };
  const measure = () => {
    const top = $("topbar").getBoundingClientRect().height;
    document.documentElement.style.setProperty("--topbar-h", top + "px");
    document.documentElement.style.setProperty("--sticky-h", (top + $("roundbar").getBoundingClientRect().height + 12) + "px");
  };
  const observer = new ResizeObserver(measure);
  observer.observe($("topbar")); observer.observe($("roundbar")); measure();
}

marked.use({ gfm: true });
mermaid.initialize({ startOnLoad: false, theme: DARK ? "dark" : "default", securityLevel: "loose" });

function esc(s) {
  return String(s).replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
}
function norm(s) { return String(s).split(/\s+/).filter(Boolean).join(" "); }
function toast(msg) {
  const t = $("toast");
  t.textContent = msg; t.hidden = false;
  clearTimeout(t._h); t._h = setTimeout(() => { t.hidden = true; }, 2800);
}
async function api(path, body) {
  const r = await fetch(path, body ? {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  } : undefined);
  const j = await r.json().catch(() => ({}));
  if (!r.ok) toast("⚠ " + (j.err || r.status));
  return j;
}

/* ---------- doc rendering ---------- */

let ASSET_BASE = `/api/asset/${encodeURIComponent(docid)}/`;

function fixRel(root) {
  root.querySelectorAll("img").forEach(img => {
    const src = img.getAttribute("src") || "";
    if (!/^(https?:|data:|\/)/.test(src)) img.src = ASSET_BASE + src;
    img.loading = "lazy";
  });
  root.querySelectorAll("a").forEach(a => {
    const href = a.getAttribute("href") || "";
    if (/^(https?:)/.test(href)) { a.target = "_blank"; return; }
    if (!/^(mailto:|#|\/)/.test(href)) a.href = ASSET_BASE + href;
  });
}

async function renderMermaid(holder, code) {
  mmdPending++;
  try {
    const { svg } = await mermaid.render("mmd" + (++mmdN), code);
    holder.innerHTML = svg;
  } catch (e) {
    holder.innerHTML = `<div class="mmderr">mermaid 渲染失败: ${esc(e.message || e)}</div>`;
    const pre = document.createElement("pre");
    pre.textContent = code;
    holder.appendChild(pre);
  } finally {
    if (--mmdPending === 0) { const cbs = mmdCbs; mmdCbs = []; cbs.forEach(f => f()); }
  }
}

/* 一块 md → DOM (mermaid / frontmatter / 普通 markdown 三路) */
function blockBody(b, plain) {
  const body = document.createElement("div");
  body.className = "bbody";
  const mmd = b.md.match(/^```mermaid\s*\n([\s\S]*?)\n?```\s*$/);
  if (mmd && !plain) {
    const holder = document.createElement("div");
    holder.className = "mmd";
    holder.textContent = "渲染中…";
    body.appendChild(holder);
    renderMermaid(holder, mmd[1]);
  } else if (!plain && b.i === 0 && b.md.startsWith("---")) {
    const det = document.createElement("details");
    det.innerHTML = "<summary>frontmatter</summary>";
    const pre = document.createElement("pre");
    pre.textContent = b.md;
    det.appendChild(pre);
    body.appendChild(det);
  } else {
    body.innerHTML = marked.parse(b.md);
    fixRel(body);
    addCopyButtons(body);
  }
  return body;
}

/* 代码块右上角 copy 按钮 (v2.5, 2026-09-06): 命令速查台 (hw-loop CHEATSHEET) 从独立 http.server 页迁到板上,
   无摩擦复制是它唯一要保住的功能。stopPropagation: 别把点击当成「按块评论」。 */
function addCopyButtons(root) {
  root.querySelectorAll("pre").forEach((pre) => {
    if (pre.querySelector(".cpbtn")) return;
    const code = pre.querySelector("code") || pre;
    const btn = document.createElement("button");
    btn.className = "cpbtn"; btn.type = "button"; btn.textContent = "copy"; btn.title = "复制整块";
    const done = () => { btn.textContent = "✓"; btn.classList.add("done");
      setTimeout(() => { btn.textContent = "copy"; btn.classList.remove("done"); }, 1200); };
    const fallback = (txt) => {
      const ta = document.createElement("textarea"); ta.value = txt;
      ta.style.position = "fixed"; ta.style.opacity = "0"; document.body.appendChild(ta); ta.select();
      try { document.execCommand("copy"); done(); } catch (e) { btn.textContent = "select+⌘C"; }
      ta.remove();
    };
    btn.onclick = (ev) => {
      ev.preventDefault(); ev.stopPropagation();
      const txt = code.textContent.replace(/\n$/, "");
      if (navigator.clipboard) navigator.clipboard.writeText(txt).then(done, () => fallback(txt));
      else fallback(txt);
    };
    pre.appendChild(btn);
  });
}

function chgMark(b) {
  if (!b.chg) return null;
  const m = document.createElement("button");
  m.className = "chgmark c-" + b.chg;
  m.textContent = b.chg === "new" ? "+新" : "●改";
  const cz = (DOC.causes || []).map(c => "#" + c.slice(2)).join(" ");
  m.title = `回合${ROUND ? ROUND.batch : "?"} 改动${cz ? " · 回应 " + cz : ""}` +
    (b.chg === "mod" ? " · 点击看旧版" : " · 整块新增");
  m.onclick = (ev) => { ev.stopPropagation(); toggleOld(b); };
  return m;
}

/* 展开状态必须住在模型里 — 轮询重建 DOM, 临时面板会被抹掉 (实测踩到) */
function toggleOld(b) {
  openOld.has(b.i) ? openOld.delete(b.i) : openOld.add(b.i);
  rerender();
}

function oldPanel(b) {
  const old = OLDMD[b.i];
  const d = document.createElement("div");
  d.className = "oldver";
  if (old === undefined) {
    d.innerHTML = `<div class="oldhead">${b.chg === "new" ? "这块是新增的 — 上一版没有它" : "旧版取不到 (快照不在了)"}</div>`;
  } else {
    d.innerHTML = `<div class="oldhead">上一版 ↓</div>`;
    d.appendChild(blockBody({ i: -1, md: old }, true));
  }
  return d;
}

/* 只画正文块 (v2: 行内卡层从这里拆出去了 — patchInline 独立维护) */
function renderBlocks() {
  const art = $("doc");
  const scroll = window.scrollY;
  art.innerHTML = "";
  MARKS.clear();               // 全重建 ⇒ 高亮全部重新应用 (syncMarks 会补)
  HEADINGS = RBNav.outline(DOC.blocks);
  BLOCKIDS = RBNav.blockIds(DOC.blocks);
  renderOutline();
  for (const b of DOC.blocks) {
    const sec = document.createElement("section");
    sec.className = "block" + (b.chg ? " chg-" + b.chg : "");
    sec.dataset.i = b.i;
    sec.id = blockId(b);       // Identical blocks get occurrence suffixes; legacy first-match links still work.
    if (MODE !== "x") {
      const btn = document.createElement("button");
      btn.className = "cbtn";
      btn.textContent = "✚";
      btn.title = "评论此块";
      btn.onclick = (ev) => { ev.stopPropagation(); openComposer(b, sec); };
      sec.appendChild(btn);
    }
    const mk = chgMark(b);
    if (mk) sec.appendChild(mk);
    const body = blockBody(b);
    const heading = HEADINGS.find(h => h.i === b.i);
    if (heading) {
      const toggle = document.createElement("button");
      toggle.className = "section-toggle";
      toggle.onclick = () => setSectionFold(heading);
      const title = body.querySelector("h1,h2,h3,h4,h5,h6");
      if (title) title.prepend(toggle);
    }
    sec.appendChild(body);
    if (openOld.has(b.i)) sec.appendChild(oldPanel(b));
    art.appendChild(sec);
    const inl = document.createElement("div");
    inl.className = "inline";
    inl.dataset.for = b.i;
    inl.hidden = true;
    art.appendChild(inl);
  }
  syncSections();
  window.scrollTo(0, scroll);
}

/* ---------- diff 视图 (v1 原样) ---------- */

function renderDiff() {
  const art = $("doc");
  art.innerHTML = "";
  if (!DIFF) { art.innerHTML = '<p class="hint">加载改动…</p>'; return; }
  if (DIFF.err) { art.innerHTML = `<p class="hint">${esc(DIFF.err)}</p>`; return; }
  let run = 0;
  const flushRun = () => {
    if (!run) return;
    const d = document.createElement("div");
    d.className = "samerun";
    d.textContent = `${run} 块未变`;
    art.appendChild(d);
    run = 0;
  };
  for (const op of DIFF.ops) {
    if (op.op === "same") { run++; continue; }
    flushRun();
    const d = document.createElement("div");
    d.className = "dop d-" + op.op;
    const lbl = { mod: "改写", add: "新增", del: "删除" }[op.op];
    d.innerHTML = `<div class="dhead"><span class="dtag t-${op.op}">${lbl}</span>` +
      `<span class="hint">@${esc(op.slug || "")}${op.op === "mod" ? " · 相似度 " + op.sim : ""}</span></div>`;
    if (op.old_md !== undefined) {
      const o = document.createElement("div");
      o.className = "dside dold";
      if (op.op === "mod") o.appendChild(Object.assign(document.createElement("div"),
        { className: "oldhead", textContent: "旧" }));
      o.appendChild(blockBody({ i: -1, md: op.old_md }, true));
      d.appendChild(o);
    }
    if (op.new_md !== undefined) {
      const n = document.createElement("div");
      n.className = "dside dnew";
      if (op.op === "mod") n.appendChild(Object.assign(document.createElement("div"),
        { className: "oldhead", textContent: "新" }));
      n.appendChild(blockBody({ i: -1, md: op.new_md }, true));
      d.appendChild(n);
    }
    art.appendChild(d);
  }
  flushRun();
}

async function loadDiff() {
  // v2.3 B9: 基线泛化 — 无回合也有改动页 (a=baseline = 你上次发言那版);
  // 已批准的回合保留「上回合改动」入口 (a=round:N → 批准版)。静默取: 没基线不是错误。
  const a = ROUND && ROUND.status === "approved" ? `round:${ROUND.batch}` : "baseline";
  try {
    const r = await fetch(`/api/diff/${encodeURIComponent(docid)}?a=${a}`);
    DIFF = await r.json();
  } catch (e) { DIFF = { err: String(e) }; }
  OLDMD = {};
  if (DIFF && DIFF.ops)
    for (const op of DIFF.ops)
      if (op.new !== null && op.new !== undefined && op.old_md !== undefined)
        OLDMD[op.new] = op.old_md;
}

function setView(v) {
  VIEW = v;
  document.querySelector("main").classList.toggle("diff-view", v === "diff");
  // 视图可链接; 深链 hash (#c-NN / #b-xx) 不许被视图切换清掉
  const h = location.hash;
  if (h === "" || h === "#diff")
    history.replaceState(null, "", location.pathname + location.search + (v === "diff" ? "#diff" : ""));
  $("viewDoc").classList.toggle("on", v === "doc");
  $("viewDiff").classList.toggle("on", v === "diff");
  lastSig = null;
  rerender(true);
}

/* ---------- 回合横幅 (板上唯一的版本级决定; v2 加 sig 门控) ---------- */

let lastRoundSig = null;
function renderRound(force) {
  const bar = $("roundbar");
  const sig = JSON.stringify([ROUND, DOC && DOC.chgstats, DOC && DOC.causes,
                              DIFF && DIFF.ops ? DIFF.ops.length : -1]);
  if (!force && sig === lastRoundSig) return;
  lastRoundSig = sig;
  // 有改动才给 diff 视图 (无改动的纯 Q&A 别塞一个空页给他); v2.3: 无回合也可能有 (基线泛化)
  const hasDiff = !!(DIFF && DIFF.ops && DIFF.ops.some(o => o.op !== "same"));
  $("views").hidden = !hasDiff;
  if (!hasDiff && VIEW === "diff") setView("doc");
  // v2.3「批准」与「看完了」合一: 同一按钮, 名字随上下文 (c-0009 拍板)
  const sb = $("seenBtn");
  const approving = !!(ROUND && ROUND.status === "answered");
  sb.textContent = approving ? "批准这版" : "看完了";
  sb.className = approving ? "" : "ghost";
  sb.title = approving ? "批准 = 这版我认了 (同时记为全看过; 改动标记清场)"
    : "看完了 = 这版每一块都记为看过 (沉默不再算没看); 改写过的块会自动回到未看";
  if (!ROUND) { bar.hidden = true; return; }
  bar.hidden = false;
  bar.className = "r-" + ROUND.status;
  const st = DOC && DOC.chgstats;
  const nchg = st ? (st.mod + st.add + st.del) : null;
  const cz = (DOC && DOC.causes || []).map(c => "#" + c.slice(2)).join(" ");
  let head, actions = [];
  if (ROUND.status === "open") {
    head = `回合 ${ROUND.batch} · 已提交 ${ROUND.n_ids} 条, 已答 ${ROUND.n_answered} 条 — 等 agent`;
  } else if (ROUND.status === "answered") {
    // nchg===null 是"不知道"不是"没改" (v0 老回合没有版本记录) — 别把未知说成事实
    const chgTxt = nchg === null ? " · 改动未知 (这轮没有版本记录)"
      : nchg === 0 ? " · 未改文档" : ` · 文档改了 ${nchg} 块`;
    head = `回合 ${ROUND.batch} 已回应${chgTxt}` + (cz ? ` (${cz})` : "") + " — 批准在右上 ↗";
    actions = [["打回", () => openComposer(null, null, "reject"), "ghost"]];
  } else if (ROUND.status === "approved") {
    head = `回合 ${ROUND.batch} ✓ 已批准${ROUND.approve_note ? " · " + ROUND.approve_note : ""}`;
  } else if (ROUND.status === "rejected") {
    head = `回合 ${ROUND.batch} 已打回 — 等 agent 重做`;
  }
  bar.innerHTML = `<span class="rhead">${esc(head)}</span>`;
  for (const [txt, fn, cls] of actions) {
    const b = document.createElement("button");
    if (cls) b.className = cls;
    b.textContent = txt;
    b.onclick = fn;
    bar.appendChild(b);
  }
}

/* ---------- composer (评论 / 批准 / 打回) ---------- */

function openComposer(b, sec, mode, extra) {
  composeMode = mode || "comment";
  composeBlock = b;
  composeRev = DOC && DOC.sha;
  composeSel = (extra && extra.sel) || null;
  composeQuote = (extra && extra.quoteOverride) || null;
  document.querySelectorAll(".block.sel").forEach(x => x.classList.remove("sel"));
  if (sec) sec.classList.add("sel");
  const q = $("cquote"), ta = $("ctext"), save = $("csave"), send = $("csend");
  send.hidden = !["comment", "explore"].includes(composeMode);
  $("cexplore").hidden = composeMode !== "comment";
  send.textContent = composeMode === "explore" ? "发起探索" : "立即发";
  if (["comment", "explore"].includes(composeMode)) {
    q.textContent = composeSel ? `〝${composeSel.text.slice(0, 90)}〞 @${b.slug}`
      : composeQuote ? `〝${composeQuote.slice(0, 90)}〞 @${b.slug}`
      : b ? "@" + b.slug + " · " + norm(b.md).slice(0, 90) : "整篇";
    ta.placeholder = composeMode === "explore" ? "你想从这里弄清什么？发出后成为独立探索分支。" : "评论… (⌘Enter 保存草稿)";
    save.textContent = "保存草稿";
  } else if (composeMode === "note") {
    q.textContent = "📝 私有笔记 — agent 永远看不见, 不进「提交这批」, 只存在 sidecar (git) 里";
    ta.placeholder = "笔记… (session 书签 / 待办 / 随想; ⌘Enter 存)";
    save.textContent = "存笔记";
  } else if (composeMode === "approve") {
    q.textContent = `批准回合 ${ROUND.batch} — 这版可以按它干了`;
    ta.placeholder = "可选: 一行批语 (⌘Enter 批准)";
    save.textContent = "批准这版";
  } else {
    q.textContent = `打回回合 ${ROUND.batch} — 会作为新一批发给 agent`;
    ta.placeholder = "这版哪里不行? (必填, ⌘Enter 打回)";
    save.textContent = "打回";
  }
  $("composer").hidden = false;
  ta.focus();
}
function closeComposer() {
  $("composer").hidden = true;
  $("ctext").value = "";
  composeBlock = null; composeSel = null; composeQuote = null;
  composeRev = null;
  composeMode = "comment";
  $("csave").onclick = () => submitComposer(false);  // editDraft 会临时改写它, 关闭必还原
  document.querySelectorAll(".block.sel").forEach(x => x.classList.remove("sel"));
}
$("ccancel").onclick = closeComposer;
$("csave").onclick = () => submitComposer(false);
$("csend").onclick = () => submitComposer(true);
$("cexplore").onclick = () => {
  if (!$("ctext").value.trim()) { $("ctext").focus(); return; }
  composeMode = "explore"; $("csend").textContent = "发起探索"; $("cexplore").hidden = true;
  submitComposer(true);
};
$("ctext").addEventListener("keydown", (e) => {
  if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) submitComposer(false);  // 肌肉记忆=存草稿
  if (e.key === "Escape") closeComposer();
});

let composerSaving = false;
async function submitComposer(sendNow) {
  if (composerSaving) return;
  composerSaving = true;
  for (const id of ["csave", "csend", "cexplore"]) $(id).disabled = true;
  try { return await saveComposer(sendNow); }
  finally {
    composerSaving = false;
    for (const id of ["csave", "csend", "cexplore"]) $(id).disabled = false;
  }
}
async function saveComposer(sendNow) {
  const text = $("ctext").value.trim();
  if (composeMode === "approve") {
    const r = await api("/api/approve", { doc: docid, batch: ROUND.batch, note: text });
    if (r.ok) { toast(`回合 ${r.batch} 已批准 — 改动标记清场`); closeComposer(); refresh(true); }
    return;
  }
  if (composeMode === "reject") {
    if (!text) { toast("打回要写一行理由"); return; }
    const r = await api("/api/reject", { doc: docid, batch: ROUND.batch, note: text });
    if (r.ok) { toast(`已打回 — 作为批次 ${r.batch} 发给 agent`); closeComposer(); refresh(true); }
    return;
  }
  if (!text) return;
  if (composeMode === "note") {
    const r = await api("/api/comment", { doc: docid, text, kind: "note", anchor: null });
    if (r.ok) { toast(`笔记已存 #${r.id.slice(2)} (只有你看得见)`); closeComposer(); refresh(); }
    return;
  }
  const b = composeBlock;
  const anchor = b ? {
    slug: b.slug, hash: b.hash,
    quote: composeQuote || (composeSel ? composeSel.text.slice(0, 120) : norm(b.md).slice(0, 120)),
    rev: composeRev || DOC.sha,
  } : { rev: composeRev || DOC.sha };
  if (composeSel && b) anchor.sel = composeSel;
  const exploring = composeMode === "explore";
  const r = await api("/api/comment", { doc: docid, text, anchor, send: !!sendNow,
    kind: b ? "block" : "doc",
    ...(exploring ? { intent: "explore" } : {}) });
  if (r.ok) {
    toast(sendNow ? `已发送 #${r.id.slice(2)}，等待回应` :
      `草稿已存 #${r.id.slice(2)} — 记得「提交这批」`);
    closeComposer(); await refresh();
    if (exploring && sendNow) { RBExplore.refresh(true); RBExplore.openThread(docid, r.id, text.split("\n")[0]); }
  }
}

/* ---------- 划词 (痛点③): mouseup → 浮动按钮 → composer 带 sel ---------- */

function closestBlockOf(node) {
  const el = node.nodeType === 3 ? node.parentElement : node;
  return el && el.closest ? el.closest("section.block") : null;
}

/* 选区文本 → normalize(块 md) 内偏移; 失配 → null (调用侧降级块锚+quote=选中文) */
function mdOffsets(b, selText, occurrence = 0) {
  let nSel = norm(selText);
  if (nSel.length < 2) return null;
  if (nSel.length > 200) nSel = nSel.slice(0, 200);
  const nMd = norm(b.md);
  let i = nMd.indexOf(nSel);
  for (let n = 0; n < occurrence && i >= 0; n++) i = nMd.indexOf(nSel, i + 1);
  if (i < 0) return null;   // 跨格式边界 (**加粗**等) 的选区会失配 — 降级
  return { text: nSel, start: i, end: i + nSel.length };
}

function updateSelBtn() {
  const btn = $("selectionActions");
  if (MODE === "x" || VIEW !== "doc") { btn.hidden = true; return; }
  const s = getSelection();
  if (!s || s.isCollapsed || !s.rangeCount) { btn.hidden = true; return; }
  const r = s.getRangeAt(0);
  // B7 (v2.3): 选区沾到评论卡 / 旧版对照 / 跨了块 → 裁剪到起点块的正文, 不再整个放弃 (静默不出 💬 = 「选不中」)
  const sec = closestBlockOf(r.startContainer) || closestBlockOf(r.endContainer);
  if (!sec) { btn.hidden = true; return; }
  const bb = sec.querySelector(".bbody");
  if (!bb) { btn.hidden = true; return; }
  const inOld = (n) => { const el = n.nodeType === 3 ? n.parentElement : n;
                         return !!(el && el.closest && el.closest(".oldver, .mmd")); };
  const rr = r.cloneRange();
  const box = document.createRange();
  box.selectNodeContents(bb);
  if (rr.compareBoundaryPoints(Range.START_TO_START, box) < 0) rr.setStart(box.startContainer, box.startOffset);
  if (rr.compareBoundaryPoints(Range.END_TO_END, box) > 0) rr.setEnd(box.endContainer, box.endOffset);
  if (rr.collapsed || inOld(rr.startContainer) || inOld(rr.endContainer)) { btn.hidden = true; return; }
  const text = rr.toString();
  if (norm(text).length < 2) { btn.hidden = true; return; }
  const rect = rr.getBoundingClientRect();
  btn.style.left = Math.max(8, Math.min(innerWidth - 205, rect.right + 6)) + "px";
  btn.style.top = Math.min(innerHeight - 40, rect.bottom + 6) + "px";
  btn.hidden = false;
  const prefix = document.createRange();
  prefix.selectNodeContents(bb); prefix.setEnd(rr.startContainer, rr.startOffset);
  const selected = norm(text).slice(0, 200);
  const before = norm(prefix.toString());
  let occurrence = 0, at = before.indexOf(selected);
  while (at !== -1) { occurrence++; at = before.indexOf(selected, at + 1); }
  btn._ctx = { bi: +sec.dataset.i, text, occurrence };
}

function initSelCapture() {
  const btn = $("selectionActions");
  btn.addEventListener("mousedown", e => e.preventDefault());  // 点按钮不塌选区
  const start = mode => {
    const c = btn._ctx;
    btn.hidden = true;
    if (!c || !DOC || !DOC.blocks[c.bi]) return;
    const b = DOC.blocks[c.bi];
    const sel = mdOffsets(b, c.text, c.occurrence);
    openComposer(b, document.querySelector(`.block[data-i="${c.bi}"]`), mode,
      sel ? { sel } : { quoteOverride: norm(c.text).slice(0, 120) });
  };
  $("selbtn").onclick = () => start("comment");
  $("exploreSelBtn").onclick = () => start("explore");
  document.addEventListener("mouseup", (e) => {
    if (btn.contains(e.target)) return;
    setTimeout(updateSelBtn, 10);
  });
  document.addEventListener("selectionchange", () => {
    const s = getSelection();
    if (!s || s.isCollapsed) $("selectionActions").hidden = true;
  });
}

/* ---------- 划词高亮: TreeWalker 文本映射 + <mark> 按节点包裹 ---------- */

function textWalk(bb) {
  const walker = document.createTreeWalker(bb, NodeFilter.SHOW_TEXT, {
    acceptNode(n) {
      const p = n.parentElement;
      if (p && p.closest(".oldver, .mmd, button")) return NodeFilter.FILTER_REJECT;
      return NodeFilter.FILTER_ACCEPT;
    },
  });
  let str = "", prevSpace = true;
  const map = [];
  while (walker.nextNode()) {
    const n = walker.currentNode, s = n.nodeValue;
    for (let i = 0; i < s.length; i++) {
      if (/\s/.test(s[i])) {
        if (!prevSpace) { str += " "; map.push({ node: n, off: i }); prevSpace = true; }
      } else { str += s[i]; map.push({ node: n, off: i }); prevSpace = false; }
    }
  }
  return { str, map };
}

function applyMark(sec, t) {
  const bb = sec.querySelector(".bbody");
  if (!bb) return false;
  const selText = norm(anchorText(t));
  if (selText.length < 2) return false;
  const { str, map } = textWalk(bb);
  let idx = str.indexOf(selText);
  if (idx < 0) return false;
  if (t.sel_pos && DOC.blocks[t.block]) {
    // Markdown offsets and rendered offsets differ. Map by occurrence, not distance.
    const md = norm(DOC.blocks[t.block].md);
    let occurrence = 0, at = md.indexOf(selText);
    while (at !== -1 && at < t.sel_pos.start) { occurrence++; at = md.indexOf(selText, at + 1); }
    while (occurrence-- > 0) {
      const next = str.indexOf(selText, idx + 1);
      if (next < 0) return false;   // Ambiguous markup: do not highlight a different occurrence.
      idx = next;
    }
  }
  const end = Math.min(idx + selText.length, map.length);
  const segs = [];
  for (let k = idx; k < end; k++) {
    const m = map[k];
    const last = segs[segs.length - 1];
    if (last && last.node === m.node && m.off >= last.a && m.off <= last.b) last.b = m.off + 1;
    else segs.push({ node: m.node, a: m.off, b: m.off + 1 });
  }
  // 倒序包裹: 前段 surroundContents 会切开文本节点, 后段的偏移就失效了
  for (let k = segs.length - 1; k >= 0; k--) {
    const g = segs[k];
    try {
      const r = document.createRange();
      r.setStart(g.node, g.a); r.setEnd(g.node, Math.min(g.b, g.node.nodeValue.length));
      const mk = document.createElement("mark");
      mk.className = "selmark s-" + t.status;
      mk.dataset.tid = t.id;
      mk.title = `#${t.id.slice(2)} · ${t.text || ""}`;
      if (k === 0) { mk.classList.add("source-start"); mk.dataset.label = "#" + t.id.slice(2); }
      r.surroundContents(mk);
    } catch (e) { unwrapMark(t.id); return false; }
  }
  return true;
}

function unwrapMark(tid) {
  document.querySelectorAll(`mark.selmark[data-tid="${tid}"]`).forEach(mk => {
    const p = mk.parentNode;
    while (mk.firstChild) p.insertBefore(mk.firstChild, mk);
    mk.remove();
    p.normalize();
  });
}

function syncMarks() {
  if (VIEW !== "doc" || MODE === "x") return;
  const want = new Map();
  for (const t of THREADS)
    if (t.kind === "block" && t.block !== null && t.block !== undefined &&
        anchorText(t))
      want.set(t.id, t);
  for (const [tid, info] of [...MARKS]) {
    const t = want.get(tid);
    if (!t || t.block !== info.bi || t.status !== info.status) {
      unwrapMark(tid);
      MARKS.delete(tid);
    }
  }
  for (const [tid, t] of want) {
    if (MARKS.has(tid)) continue;
    const sec = document.querySelector(`.block[data-i="${t.block}"]`);
    if (sec && applyMark(sec, t)) MARKS.set(tid, { bi: t.block, status: t.status });
  }
}

/* ---------- patchList: keyed 逐卡 reconcile (痛点⑤核心) ---------- */

function holdsUserState(el) {
  const s = getSelection();
  if (s && !s.isCollapsed && s.anchorNode && el.contains(s.anchorNode)) return true;
  const active = document.activeElement;
  // Focused edits defer patching. Blurred inputs are restored from UNSENT_FOLLOWUPS,
  // so a branch reply can update the background card without swallowing its text.
  return !!(active && el.contains(active) && /^(TEXTAREA|INPUT)$/.test(active.tagName) && active.value);
}

function patchList(box, items, keyOf, sigOf, build) {
  const have = new Map();
  for (const el of [...box.children])
    if (el.dataset && el.dataset.key) have.set(el.dataset.key, el);
  let cur = null;
  for (const it of items) {
    const k = keyOf(it), sig = sigOf(it);
    let el = have.get(k);
    if (!el) {
      el = build(it);
      el.dataset.key = k; el.dataset.sig = sig;
    } else if (el.dataset.sig !== sig && !holdsUserState(el)) {
      const f = build(it);
      f.dataset.key = k; f.dataset.sig = sig;
      el.replaceWith(f);
      el = f;
    }   // sig 变了但持有用户状态 → 延后 (sig 未更新, 下一拍自愈)
    const want = cur ? cur.nextSibling : box.firstChild;
    if (want !== el) box.insertBefore(el, want);
    have.delete(k);
    cur = el;
  }
  for (const el of have.values()) if (!holdsUserState(el)) el.remove();
}

/* ---------- thread 卡 (行内为主) 与侧栏行 ---------- */

function oneLine(t) {
  const s = t.summary || (t.text.split("\n")[0]);
  return s.length > 90 ? s.slice(0, 90) + "…" : s;
}

function cardSig(t) {
  return JSON.stringify([t.id, t.status, t.intent, t.block, t.block_hash, t.anchor_status,
    t.sel_status, (t.text || "").length, t.summary,
    (t.turns || []).map(u => [u.kind, u.id, u.ts, u.draft ? 1 : 0,
                              (u.text || "").length, u.doc_ref_url || 0, u.summary, u.session_label || 0]),
    t.answer && t.answer.commit, expanded.has(t.id), (t.spawned || []).length,
    t.graduated && t.graduated.dest]);
}
function rowSig(t) {
  return JSON.stringify([t.id, t.status, t.block, oneLine(t), anchorText(t),
    RBNav.followups(t), !!t.graduated]);
}

function sessChip(u) {
  // v2.3 F2: 谁答的 — 本机活 session 给 name@host, 否则 sid 前 8 位
  return u.session_label
    ? ` <span class="tchip s-sess" title="session ${esc(u.session || "")}">${esc(u.session_label)}</span>` : "";
}
function turnRow(who, cls) {
  const d = document.createElement("div");
  d.className = "tr " + (cls || "");
  d.innerHTML = `<div class="who">${who}</div><div class="what"></div>`;
  return d;
}
/* B8 (v2.3): 每轮一行, 两栏 (64px 作者 | 正文), chips 挂行尾; 根评论只在多行/超长时才重复 */
function turnEl(t, u) {
  let d, w;
  if (u.kind === "comment") {
    const txt = u.text || "";
    // B13 (v2.5): 标题行是 ellipsis, 41+ 字就可能被视觉截断 — 阈值从 110 收到 48, 宁可重复别吞字
    if (!FOCUS_THREAD && !(txt.includes("\n") || txt.length > 48)) return null;
    d = turnRow(esc(u.author || "Reviewer"), "m-me");
    d.querySelector(".what").innerHTML = marked.parse(txt);
  } else if (u.kind === "followup") {
    d = turnRow(esc(u.author || "Reviewer") + " 追问", "m-me" + (u.draft ? " m-draft" : ""));
    w = d.querySelector(".what");
    w.innerHTML = marked.parse(u.text || "") +
      (u.draft ? '<span class="tchip s-draft">随批次</span>' : "");
    if (u.draft) {
      const deliver = document.createElement("button");
      deliver.className = "tiny solid"; deliver.textContent = "发"; deliver.title = "只发送这条追问";
      deliver.onclick = () => api("/api/followup/send", { doc: docid, id: u.id })
        .then(r => { if (r.ok) { toast("这条追问已发送"); refresh(); RBExplore.refresh(true); } });
      w.appendChild(deliver);
      const undo = document.createElement("button");
      undo.className = "ghost tiny";
      undo.textContent = "撤";
      undo.onclick = () => api("/api/comment/drop", { doc: docid, id: u.id })
        .then(r => { if (r.ok) { toast("追问草稿已撤"); refresh(); } });
      w.appendChild(undo);
    }
  } else if (u.kind === "reply") {
    d = turnRow("↳ " + esc(u.author || "agent"), "m-agent");
    w = d.querySelector(".what");
    w.innerHTML = (u.doc_ref_url
      ? `${esc((u.text || "").split("\n")[0].slice(0, 160))} <a class="tchip s-ref" href="${esc(u.doc_ref_url)}" target="_blank" rel="noopener">→ 全文</a>`
      : marked.parse(u.text || ""));           // session chip 只挂结论行 (每条 reply 必配 answer)
  } else if (u.kind === "graduate") {
    d = turnRow("⤤ 毕业", "m-grad");
    d.querySelector(".what").innerHTML = `→ ${esc(u.dest || "")}${u.note ? " · " + esc(u.note) : ""}`;
  } else if (u.kind === "answer") {
    d = turnRow("↳ 结论", "m-agent m-answer");
    let h = `<span class="tlsum">${esc(u.summary || "")}</span>`;
    if (u.mode === "edit")
      h += ` <span class="tchip s-edit" title="已按此评论改了文档${u.commit ? " · commit " + esc(u.commit) : ""}">✎ ${u.commit ? esc(u.commit).slice(0, 8) : "已改文档"}</span>`;
    if (u.doc_ref_url)
      h += ` <a class="tchip s-ref" href="${esc(u.doc_ref_url)}" target="_blank" rel="noopener">→ 全文</a>`;
    h += sessChip(u);
    d.querySelector(".what").innerHTML = h;
  }
  return d;
}

function buildCard(t) {
  const d = document.createElement("div");
  const isNote = t.kind === "note";
  d.className = "card s-" + t.status + (t.kind === "reject" ? " k-reject" : "") + (isNote ? " k-note" : "");
  d.id = t.id;                               // 深链 #c-NNNN
  const open = expanded.has(t.id);
  const head = document.createElement("div");
  head.className = "chead";
  const first = (t.text || "").split("\n")[0];
  const one = first.length > 110 ? first.slice(0, 110) + "…" : first;
  const lost = t.kind === "block" && (t.block === null || t.block === undefined);
  const where = t.kind === "block" && !lost && t.anchor && t.anchor.slug
    ? `<a class="locate" title="定位原文：${esc(anchorText(t))} · @${esc(t.anchor.slug)}">↗ ${esc(anchorText(t).slice(0, 32) || t.anchor.slug.slice(0, 22))}</a>` : "";
  const warn = lost ? `<span class="hint" title="${esc(ASTAT[t.anchor_status] || ASTAT.unknown)}">⚠</span>`
    : t.anchor_status === "modified" ? `<span class="amod" title="${ASTAT.modified}">✎</span>` : "";
  head.innerHTML = `<span class="caret">${open ? "▾" : "▸"}</span>` +
    `<span class="chip s-${t.status}">${isNote ? "笔记" : LBL[t.status]}</span>` +
    (t.graduated ? `<span class="gmark" title="已毕业 → ${esc((t.graduated || {}).dest || "")}">⤤</span>` : "") +
    `<b>#${t.id.slice(2)}</b> <span class="one">${esc(one)}` +
    (!open && t.summary ? ` <span class="hsum">↳ ${esc(t.summary)}</span>` : "") +
    `</span><span class="hact">${warn}${where}</span>`;
  const act = head.querySelector(".hact");
  const add = (txt, fn, cls, title) => {   // B8 规则 1: 动作进标题行右端
    const b = document.createElement("button");
    b.className = "tiny " + (cls || "ghost");
    b.textContent = txt;
    if (title) b.title = title;
    b.onclick = (ev) => { ev.stopPropagation(); fn(); };
    act.appendChild(b);
  };
  if (t.status === "draft") {
    add("改", () => editDraft(t));
    add("撤", () => api("/api/comment/drop", { doc: docid, id: t.id }).then(() => {
      expanded.delete(t.id); toast((isNote ? "笔记" : "草稿") + "已撤 #" + t.id.slice(2)); refresh();
    }));
  }
  if (t.status === "answered" || t.status === "open")
    add("✓", () => api("/api/resolve", { doc: docid, id: t.id }).then(() => refresh()), "solid", "解决");
  if (t.status === "resolved" || t.status === "answered")
    add("↺", () => api("/api/reopen", { doc: docid, id: t.id }).then(() => refresh()), "ghost", "重开");
  if (t.status !== "draft" && !isNote && !FOCUS_THREAD)
    add("↗ 探索", () => RBExplore.promote(t), "ghost", "把这一问放进探索树，独立深入");
  const lk = head.querySelector(".locate");
  if (lk) lk.onclick = (ev) => { ev.stopPropagation(); locateThread(t); };
  head.onclick = () => {
    if (FOCUS_THREAD) return;
    open ? expanded.delete(t.id) : expanded.add(t.id);
    activeThread = t.id; patchInline(); patchSide(); syncActiveThread();
  };
  d.appendChild(head);
  if (!open) return d;

  const body = document.createElement("div");
  body.className = "cbody";
  const st = t.anchor_status;
  let note = "";
  if (t.kind === "reject") note = `打回第 ${t.re_batch} 回合`;
  else if (lost) note = `${ASTAT[st] || ASTAT.unknown}: ${esc(((t.anchor || {}).quote || "").slice(0, 80))}`;
  else if (anchorText(t)) note = `<span class="selq">引用原文：〝${esc(anchorText(t))}〞</span>` +
    (t.sel_status === "gone" ? ' <span class="hint">原文有变化；若无法匹配完整引文则定位到整块</span>' : "");
  if (note) {
    const m = document.createElement("div");
    m.className = "meta";
    m.innerHTML = note;
    body.appendChild(m);
  }

  const tl = document.createElement("div");
  tl.className = "tl";
  for (const u of (t.turns || [])) { const el = turnEl(t, u); if (el) tl.appendChild(el); }
  tl.querySelectorAll(".what").forEach(x => fixRel(x));

  if ((t.spawned && t.spawned.length) || t.graduated) {   // 卫星 / 毕业 = 行尾 chips
    const sp = turnRow("", "m-meta");
    sp.querySelector(".what").innerHTML =
      (t.spawned || []).map(s => `<a class="tchip s-ref" href="/d/${encodeURIComponent(s.doc)}">⤵ ${esc(s.title)}</a>`).join(" ") +
      (t.graduated ? ` <span class="tchip s-grad">⤤ ${t.graduated.url
        ? `<a href="${esc(t.graduated.url)}">${esc(t.graduated.dest || "")}</a>` : esc(t.graduated.dest || "")}</span>` : "");
    tl.appendChild(sp);
  }

  if (t.status !== "draft") {               // thread 追问: 单行同排「发 / 存」(B8 规则 5)
    const fup = turnRow("↳ 追问", "m-fup");
    const w = fup.querySelector(".what");
    const ta = document.createElement("textarea");
    ta.rows = 1;
    ta.placeholder = "追问… (⌘Enter 存草稿)";
    ta.value = UNSENT_FOLLOWUPS.get(t.id) || "";
    ta.addEventListener("input", () => {
      if (ta.value) UNSENT_FOLLOWUPS.set(t.id, ta.value); else UNSENT_FOLLOWUPS.delete(t.id);
    });
    const btns = document.createElement("span");
    btns.className = "fbtns";
    btns.hidden = !ta.value;
    const send = async (now) => {
      const v = ta.value.trim();
      if (!v) return;
      const r = await api("/api/followup", { doc: docid, id: t.id, text: v, send: now });
      if (r.ok) {
        toast(now ? "追问已发送，等待回应" : "追问已存草稿 — 随下一批送达");
        ta.value = "";
        UNSENT_FOLLOWUPS.delete(t.id);
        refresh();
      }
    };
    const b1 = document.createElement("button");
    b1.className = "tiny solid";
    b1.textContent = "发";
    b1.title = "立即发送这条追问";
    b1.onclick = () => send(true);
    const b2 = document.createElement("button");
    b2.className = "tiny ghost";
    b2.textContent = "存";
    b2.title = "存草稿, 随下一批提交";
    b2.onclick = () => send(false);
    btns.appendChild(b1); btns.appendChild(b2);
    ta.onfocus = () => { btns.hidden = false; };
    ta.onblur = () => { if (!ta.value) setTimeout(() => { btns.hidden = true; }, 150); };
    ta.addEventListener("keydown", (e) => {
      if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) send(false);
    });
    w.appendChild(ta); w.appendChild(btns);
    tl.appendChild(fup);
  }
  body.appendChild(tl);
  d.appendChild(body);
  return d;
}

function buildRow(t) {
  const d = document.createElement("div");
  d.className = "trow s-" + t.status + (t.kind === "reject" ? " k-reject" : "");
  d.id = "s-" + t.id;
  d.setAttribute("role", "button"); d.tabIndex = 0;
  const f = RBNav.followups(t);
  d.innerHTML = `<div class="row-main"><span class="chip s-${t.status}">${LBL[t.status]}</span>` +
    `<b>#${t.id.slice(2)}</b> <span class="one">${esc(oneLine(t))}</span>` +
    `</div>` +
    (anchorText(t) ? `<div class="row-quote" title="${esc(anchorText(t))}">〝${esc(anchorText(t))}〞</div>` : "") +
    (f.latest ? `<div class="row-followup${f.drafts ? " has-draft" : ""}" title="${esc(f.latest.text || "")}"><b>${f.drafts ? `追问草稿 ${f.drafts}` : "↳ 追问"}</b> ${esc(f.latest.text || "")}</div>` : "");
  d.onclick = () => { expanded.add(t.id); locateThread(t); };
  d.onkeydown = e => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); d.click(); } };
  return d;
}

/* 草稿就地改: 复用 composer 面板, 不用 window.prompt (浏览器弹窗会卡死自动化扩展) */
function editDraft(t) {
  openComposer(null, null, "comment");
  $("csend").hidden = true;
  $("cexplore").hidden = true;
  $("cquote").textContent = "改草稿 #" + t.id.slice(2) + " (只有草稿能改)";
  $("ctext").value = t.text;
  $("csave").textContent = "存改动";
  $("csave").onclick = async () => {
    const nt = $("ctext").value.trim();
    if (!nt) return;
    const r = await api("/api/comment/edit", { doc: docid, id: t.id, text: nt });
    if (r.ok) { toast("草稿已改 #" + t.id.slice(2)); closeComposer(); refresh(); }
  };
}

/* ---------- 行内卡层 (评论的主场 — 飞书式) ---------- */

function updateBadge(sec, ts) {
  let badge = sec.querySelector(".badge");
  if (!ts.length) {
    if (badge) { badge.remove(); sec.classList.remove("hasb"); }
    return;
  }
  const worst = ts.reduce((w, t) => Math.min(w, RANK[t.status]), 9);
  const cls = "badge s-" + Object.keys(RANK).find(k => RANK[k] === worst);
  const txt = "💬" + ts.length;
  if (!badge) {
    badge = document.createElement("button");
    badge.title = "展开/收起这块的评论";
    badge.onclick = (ev) => {
      ev.stopPropagation();
      const bi = +sec.dataset.i;
      inlineClosed.has(bi) ? inlineClosed.delete(bi) : inlineClosed.add(bi);
      patchInline();
    };
    sec.appendChild(badge);
    sec.classList.add("hasb");
  }
  if (badge.className !== cls) badge.className = cls;
  if (badge.textContent !== txt) badge.textContent = txt;
}

function patchInline() {
  if (VIEW !== "doc" || !DOC || MODE === "x") return;
  const byBlock = {};
  for (const t of THREADS)
    if (t.kind === "block" && t.block !== null && t.block !== undefined)
      (byBlock[t.block] = byBlock[t.block] || []).push(t);
  const art = $("doc");
  for (const el of art.children) {
    if (!el.classList.contains("inline")) continue;
    const bi = +el.dataset.for;
    const sec = el.previousElementSibling;
    const ts = byBlock[bi] || [];
    if (sec && sec.classList.contains("block")) updateBadge(sec, ts);
    if (!ts.length) {
      el.hidden = true;
      if (el.children.length) el.innerHTML = "";
      continue;
    }
    el.hidden = inlineClosed.has(bi);
    if (!el.querySelector(".ilive")) {
      el.innerHTML = `<div class="ilive"></div>` +
        `<details class="donefold"><summary></summary><div class="idone"></div></details>`;
    }
    const live = ts.filter(t => t.status !== "resolved" || RBNav.hasDraft(t));
    const done = ts.filter(t => t.status === "resolved" && !RBNav.hasDraft(t));
    patchList(el.querySelector(".ilive"), live, t => t.id, cardSig, buildCard);
    const fold = el.querySelector(".donefold");
    fold.hidden = done.length === 0;
    fold.querySelector("summary").textContent = `✓ ${done.length} 条已解决`;
    patchList(el.querySelector(".idone"), done, t => t.id, cardSig, buildCard);
  }
}

/* ---------- 侧栏: 可拖宽可隐藏的待办队列 (一行式 + 过滤 chips) ---------- */

function patchSide() {
  if (MODE === "x") return;
  const side = $("side");
  const sTop = side.scrollTop;
  const filt = t => RBNav.matches(t, FILTER);
  // B11 (v2.5): 托盘跟随过滤 — resolved 的失锚 thread 原来被滤掉且行内无处渲染, 哪都点不到
  const lost = THREADS.filter(t => t.kind === "block" &&
    (t.block === null || t.block === undefined) && filt(t));
  $("tray").hidden = lost.length === 0;
  patchList($("traylist"), lost, t => t.id, cardSig, buildCard);
  // doc 级/打回线程置顶 (全篇性关切在块序之上)
  const docLevel = THREADS.filter(t => t.kind !== "block" && filt(t));
  patchList($("docthreads"), docLevel, t => t.id, cardSig, buildCard);
  const rows = THREADS
    .filter(t => t.kind === "block" && t.block !== null && t.block !== undefined && filt(t))
    .sort((a, b) => ((a.block ?? 1e9) - (b.block ?? 1e9)) || (a.ts - b.ts));
  patchList($("threads"), rows, t => "s-" + t.id, rowSig, buildRow);
  side.scrollTop = sTop;

  const counts = {};
  for (const t of THREADS) counts[t.status] = (counts[t.status] || 0) + 1;
  const chipN = Object.fromEntries(["todo", "drafts", "followups", "all", "resolved"]
    .map(f => [f, THREADS.filter(t => RBNav.matches(t, f)).length]));
  document.querySelectorAll("#chips .fchip").forEach(c => {
    const f = c.dataset.f;
    c.classList.toggle("on", f === FILTER);
    c.textContent = ({ todo: "未解决", drafts: "未提交", followups: "追问", all: "全部", resolved: "已解决" })[f] +
      (chipN[f] ? " " + chipN[f] : "");
  });
  $("counts").textContent = ["open", "answered", "resolved"]
    .filter(s => counts[s]).map(s => `${LBL[s]} ${counts[s]}`).join(" · ");

  const fdrafts = [];
  for (const t of THREADS)
    for (const u of (t.turns || []))
      if (u.kind === "followup" && u.draft) fdrafts.push({ t, u });
  const notes = THREADS.filter(t => t.kind === "note").length;   // v2.3: 笔记永远 draft 但不算「未提交」
  const drafts = (counts.draft || 0) - notes + fdrafts.length;
  $("draftchip").hidden = drafts === 0;
  $("draftchip").textContent = `${drafts} 条未提交`;
  const sb = $("submitBtn");
  sb.disabled = drafts === 0;
  sb.textContent = drafts ? `提交这批 (${drafts})` : "提交这批";
  syncSections(); syncActiveThread();
}

document.querySelectorAll("#chips .fchip").forEach(c => c.onclick = () => {
  FILTER = c.dataset.f;
  lsSet("rb.filter", FILTER);
  patchSide();
});

function initSideLayout() {
  const main = document.querySelector("main");
  const drag = $("sideDrag");
  const saved = parseInt(lsGet("rb.sideW", ""), 10);
  if (saved) main.style.setProperty("--side-w", saved + "px");
  if (lsGet("rb.sideOn", "1") === "0") main.classList.add("side-off");
  const narrow = matchMedia("(max-width: 1000px)");
  drag.addEventListener("pointerdown", (e) => {
    if (narrow.matches) return;
    e.preventDefault();
    drag.setPointerCapture(e.pointerId);
    document.body.classList.add("sdrag");
    const move = (ev) => {
      // --side-w 恒为 px 长度 ⇒ 侧栏列宽自顶向下确定 (trap #12 安全线)
      const w = Math.max(240, Math.min(Math.min(640, innerWidth * 0.5),
        innerWidth - ev.clientX - 24));
      main.style.setProperty("--side-w", w + "px");
    };
    const up = () => {
      document.body.classList.remove("sdrag");
      drag.removeEventListener("pointermove", move);
      drag.removeEventListener("pointerup", up);
      const w = parseInt(main.style.getPropertyValue("--side-w"), 10) || 380;
      lsSet("rb.sideW", w);
    };
    drag.addEventListener("pointermove", move);
    drag.addEventListener("pointerup", up);
  });
  drag.addEventListener("dblclick", () => {
    main.style.setProperty("--side-w", "380px");
    lsSet("rb.sideW", 380);
  });
  $("sideToggle").onclick = () => {
    const off = main.classList.toggle("side-off");
    lsSet("rb.sideOn", off ? "0" : "1");
  };
}

/* ---------- 渲染调度 (trap #13 细化: 稳态 tick 零 DOM 触碰) ---------- */

/* v2: docSig 不含 THREADS — 线程变化只动卡层, 不再逼整篇重渲 (复制失败主因) */
function docSig() {
  // 必须含装饰状态: 批准后文档字节没变但标记要清场 (实测漏了 → 标记不消失)
  return JSON.stringify([DOC && DOC.sha, DOC && DOC.blocks.map(b => b.chg || 0).join(""),
    DOC && DOC.round && DOC.round.status, VIEW, [...openOld].sort()]);
}
let lastSig = null;
function rerender(force) {
  if (!DOC) return;
  if (FOCUS_THREAD) {
    const t = THREADS.find(item => item.id === FOCUS_THREAD && item.kind !== "note");
    if (!t) { $("doc").textContent = "这个分支暂时不可用，回到原文查看。"; return; }
    expanded.add(t.id);
    patchList($("doc"), [t], item => item.id, cardSig, buildCard);
    $("title").textContent = t.text.split("\n")[0];
    document.title = t.text.split("\n")[0] + " · review-board";
    $("branchStatus").textContent = t.status === "open" ? "问题已送达 · 等待回应，你可以继续追问" : t.status === "draft" ? "尚未提交" : "沿着这一问继续探索，或带着结论回到原文";
    return;
  }
  renderRound(force);
  const sig = docSig();
  if (force || sig !== lastSig) {
    const s = getSelection();
    const selInDoc = s && !s.isCollapsed && s.anchorNode && $("doc").contains(s.anchorNode);
    if (selInDoc && !force) {
      // 选区在正文里 → 这拍不重排 (lastSig 不更新, 选区塌了自然补渲)
    } else {
      lastSig = sig;
      if (VIEW === "diff") renderDiff(); else renderBlocks();
    }
  }
  if (VIEW === "doc") { patchInline(); syncMarks(); syncUnseen(); syncSections(); }
  patchSide();
}
let selNudge = null;
document.addEventListener("selectionchange", () => {
  clearTimeout(selNudge);
  selNudge = setTimeout(() => {
    const s = getSelection();
    if (s && s.isCollapsed && MODE !== "x") rerender();  // 选区塌了 → 补上延过的渲染
  }, 400);
});

/* ---------- 定位 (痛点④): 先验 hash 再滚, 失败 toast 永不静默 ---------- */

function flash(el) {
  el.classList.add("flash");
  setTimeout(() => el.classList.remove("flash"), 1600);
}

function scrollSettle(el) {
  el.scrollIntoView({ behavior: "smooth", block: "center" });
  if (mmdPending > 0) {          // mermaid 渲完页面高度会变 — 补滚一次, 用户手滚即取消
    let cancelled = false;
    const cancel = () => { cancelled = true; };
    addEventListener("wheel", cancel, { once: true });
    addEventListener("touchmove", cancel, { once: true });
    mmdCbs.push(() => {
      if (!cancelled) requestAnimationFrame(() =>
        el.scrollIntoView({ block: "center" }));
    });
  }
}

async function locateThread(t, retried) {
  if (!t) { toast("⚠ 定位失败 — 线程不在了"); return; }
  if (FOCUS_THREAD) { RBExplore.source(t.id); return; }
  if (VIEW !== "doc") setView("doc");
  activeThread = t.id;
  if (t.kind !== "block") {
    expanded.add(t.id); patchSide(); syncActiveThread();
    const el = document.getElementById(t.id);
    if (el) { scrollSettle(el); flash(el); }
    return;
  }
  const ok = t.block !== null && t.block !== undefined && DOC &&
    DOC.blocks[t.block] && t.block_hash && DOC.blocks[t.block].hash === t.block_hash;
  if (!ok) {
    if (retried) { toast("⚠ 定位失败 — 文档刚变了, 已重试过一次"); return; }
    await loadDoc();                       // 客户端块表可能滞后 — 强制取齐再试一次
    const r = await api(`/api/threads/${encodeURIComponent(docid)}`);
    if (r.threads) THREADS = r.threads;
    rerender(true);
    return locateThread(THREADS.find(x => x.id === t.id) || t, true);
  }
  revealBlock(t.block);
  expanded.add(t.id);
  inlineClosed.delete(t.block);
  patchInline(); syncMarks(); patchSide();
  const card = document.getElementById(t.id);
  const fold = card && card.closest("details.donefold");
  if (fold) fold.open = true;               // 已解决的卡住在折叠里 — 翻开再滚
  const mark = document.querySelector(`mark.selmark[data-tid="${t.id}"]`);
  const target = mark || document.querySelector(`.block[data-i="${t.block}"]`) || card;
  if (!target) { toast("⚠ 定位不到卡片 (块在但卡没渲)"); return; }
  scrollSettle(target);
  syncActiveThread();
  flash(target);
  if (!mark && (t.anchor || {}).sel) toast(`未找到 #${t.id.slice(2)} 的完整划词，已定位到原块；评论卡保留当时引文`);
  history.replaceState(null, "", "#" + t.id);
}

/* 深链: #c-NNNN 直达评论卡, #b-<hash8> 直达块 */
function gotoHash() {
  const h = decodeURIComponent(location.hash.slice(1));
  if (!h || h === "diff") return;
  if (/^c-\d+$/.test(h)) {
    const t = THREADS.find(x => x.id === h);
    t ? locateThread(t) : toast("⚠ 没有评论 #" + h.slice(2));
  } else if (h.startsWith("b-") && DOC) {
    const b = DOC.blocks.find(x => blockId(x) === h);
    if (b) {
      if (VIEW !== "doc") setView("doc");
      revealBlock(b.i);
      const sec = document.querySelector(`.block[data-i="${b.i}"]`);
      if (sec) { scrollSettle(sec); flash(sec); }
    } else toast("⚠ 没有这个块 (可能已被改写)");
  }
}
addEventListener("hashchange", gotoHash);

$("viewDoc").onclick = () => setView("doc");
$("viewDiff").onclick = () => setView("diff");

/* ---------- 提交批次 (staging 含追问草稿) ---------- */

$("submitBtn").onclick = async () => {
  const ds = THREADS.filter(t => t.status === "draft" && t.kind !== "note");
  const fds = [];
  for (const t of THREADS)
    for (const u of (t.turns || []))
      if (u.kind === "followup" && u.draft) fds.push({ t, u });
  const stage = $("stage");
  if (stage.hidden) {                       // 一段 staging: 先看清这批要发什么
    stage.hidden = false;
    stage.innerHTML = `<div class="sthead">要提交这 ${ds.length + fds.length} 条 (提交后不可撤不可改):</div>` +
      ds.map(t => `<div class="sitem"><b>#${t.id.slice(2)}</b> ${esc(oneLine(t))}</div>`).join("") +
      fds.map(({ t, u }) => `<div class="sitem"><b>#${t.id.slice(2)}↳</b> ${esc((u.text || "").split("\n")[0].slice(0, 90))}</div>`).join("");
    const row = document.createElement("div");
    row.className = "crow";
    const ok = document.createElement("button");
    ok.textContent = "确认提交";
    ok.onclick = async () => {
      const r = await api("/api/submit", { doc: docid });
      if (r.ok) {
        const parts = [];
        if (r.batch) parts.push(`批次 ${r.batch} (${r.ids.length} 条)`);
        if (r.fids && r.fids.length) parts.push(`${r.fids.length} 条追问`);
        toast(`已提交 ${parts.join(" + ")} — agent 侧会收到`);
      }
      stage.hidden = true;
      refresh(true);
    };
    const no = document.createElement("button");
    no.className = "ghost";
    no.textContent = "再想想";
    no.onclick = () => { stage.hidden = true; };
    row.appendChild(ok); row.appendChild(no);
    stage.appendChild(row);
  } else stage.hidden = true;
};

/* ---------- data loop ---------- */

async function loadDoc() {
  const d = await api(`/api/doc/${encodeURIComponent(docid)}`);
  if (!d.blocks) return;
  DOC = d;
  mtime = d.mtime;
  ROUND = d.round;
  document.title = d.title + " · review-board";
  $("title").textContent = (d.emoji ? d.emoji + " " : "") + d.title;
  if (FOCUS_THREAD) $("branchSource").textContent = "↩ 出自《" + d.title + "》";
  // 卫星出身反链 (registry born — 板侧元数据, 不进正文)
  const bornEl = $("born");
  if (d.born && d.born.doc) {
    bornEl.hidden = false;
    bornEl.innerHTML = `⤴ born from <a href="/d/${encodeURIComponent(d.born.doc)}#${encodeURIComponent(d.born.cid || "")}">` +
      `#${esc((d.born.cid || "").slice(2))} @ ${esc(d.born.title || d.born.doc)}</a>`;
  } else bornEl.hidden = true;
  // question-first 出生记录 (registry question — 本文回答的是什么)
  const qEl = $("question");
  if (d.question) {
    qEl.hidden = false;
    qEl.textContent = "❓ 本文回答的是: " +
      (d.question.length > 60 ? d.question.slice(0, 60) + "…" : d.question);
    qEl.title = d.question;
  } else qEl.hidden = true;
  $("archchip").hidden = !d.archived;
  $("draftdocchip").hidden = !d.draft;   // v2.5 F18③: 草稿也上板, 注明
  setClaim(d.claim);
  setCoverage(d.coverage);
  await loadDiff();
}

/* ---------- v2.3 F2: 值守 chip (谁在 wait 这份文档) ---------- */
function setClaim(c) {
  const el = $("claimchip");
  el.hidden = false;
  if (!c) {
    el.className = "claim none";
    el.textContent = "○ 无人值守";
    el.title = `没有 session 在 wait 这份文档 — 评论会等到有人跑 boardctl wait --doc ${docid}`;
    return;
  }
  el.className = "claim " + (c.alive ? "alive" : "dead");
  el.textContent = `${c.alive ? "●" : "○"} 值守 ${c.name} · ${(c.sid || "").slice(0, 8)} · ${c.host} · ${c.cwd}` +
    (c.alive ? ` · 心跳 ${c.beat_ago}s 前` : " · 失联");
  el.title = c.alive ? `resume: claude --resume ${c.sid}   (SendMessage ${c.name})`
    : `值守进程不在了 (最后心跳 ${c.beat_ago}s 前) — 要人重新 wait`;
}

/* ---------- v2.3 F3: 覆盖率 chip + 未看块标记 (沉默 = 没看) ---------- */
let COV = null;
function setCoverage(cov) {
  COV = cov;
  const el = $("covchip");
  if (!cov || !cov.n_total) { el.hidden = true; return; }
  el.hidden = false;
  const all = cov.n_seen >= cov.n_total;
  el.textContent = `看过 ${cov.n_seen}/${cov.n_total}${all ? " ✓" : ""}`;
  el.className = "cov" + (all ? " all" : "");
  el.title = all ? "这版每一块都看过了 (评论过 / 看完了 / 批准过 = 看过)"
    : "点一下跳到第一块没看的 · 评论过 / 看完了 / 批准过 = 看过; 我改写过的块会回到未看";
  syncUnseen();
}
function syncUnseen() {
  if (!DOC || VIEW !== "doc") return;
  const un = new Set(((COV && COV.unseen) || []).map(u => u.hash));
  document.querySelectorAll("#doc section.block").forEach(sec => {
    sec.classList.toggle("unseen", un.has(DOC.blocks[+sec.dataset.i]?.hash));
  });
}

async function refresh(force) {
  if (RBExplore.isPaused()) return;
  const r = await api(`/api/threads/${encodeURIComponent(docid)}`);
  if (!r.threads) return;
  THREADS = r.threads;
  const roundChanged = JSON.stringify(r.round) !== JSON.stringify(ROUND);
  const shaChanged = r.sha && DOC && r.sha !== DOC.sha;
  const covChanged = !!(r.coverage && DOC && DOC.coverage &&
    (r.coverage.n_seen !== DOC.coverage.n_seen || r.coverage.n_total !== DOC.coverage.n_total));
  if (force || roundChanged || shaChanged || covChanged || (r.mtime && r.mtime !== mtime)) await loadDoc();
  else if (r.claim !== undefined) setClaim(r.claim);   // 心跳秒数每拍刷新, 不重渲
  rerender(force);
  RBExplore.refresh();
}

/* ---------- /x/ 只读模式 (外链答案页): 只渲染块, 无评论无轮询 ---------- */

async function bootReadonly() {
  document.body.classList.add("readonly");
  const d = await api(`/api/answerdoc/${encodeURIComponent(docid)}/${XREL}`);
  if (!d.blocks) { $("title").textContent = "取不到外链答案"; return; }
  const rel = decodeURIComponent(XREL);
  const dir = rel.includes("/") ? rel.slice(0, rel.lastIndexOf("/") + 1) : "";
  ASSET_BASE = `/api/asset/${encodeURIComponent(docid)}/` + dir;
  document.title = rel + " · review-board";
  $("title").innerHTML = `<a href="/d/${encodeURIComponent(docid)}">◀ 回 ${esc(docid)}</a> · ${esc(rel)} <span class="hint">(外链答案 · 只读)</span>`;
  DOC = { blocks: d.blocks, causes: [] };
  ROUND = null;
  renderBlocks();
}

/* ---------- v2.4 F14: 文档内搜索 (折叠卡+外链答案吃掉 ⌘F) ---------- */

function renderHits(box, hits, q) {
  box.innerHTML = hits.length ? "" : '<div class="hint">没搜到</div>';
  const KL = { block: "正文", comment: "评论", answer: "答案", question: "❓" };
  for (const h of hits) {
    const a = document.createElement("a");
    a.className = "srow";
    a.href = h.url;
    a.innerHTML = `<span class="chip s-draft">${KL[h.kind] || esc(h.kind)}</span>` +
      (h.archived ? "🗄 " : "") + `<span class="sdoc"></span><span class="sex"></span>`;
    a.querySelector(".sdoc").textContent =
      (typeof docid !== "undefined" && h.doc !== docid) ? (h.title || h.doc) + " · " : "";
    const ex = a.querySelector(".sex");
    const txt = h.excerpt || "";
    const i = txt.toLowerCase().indexOf(q.toLowerCase());
    if (i >= 0) {
      ex.append(txt.slice(0, i));
      const m = document.createElement("mark");
      m.textContent = txt.slice(i, i + q.length);
      ex.append(m, txt.slice(i + q.length));
    } else ex.textContent = txt;
    box.appendChild(a);
  }
}

/* ---------- v2.5 F17: 页面直改 emoji / 画框色 ---------- */
function initMeta() {
  $("metaBtn").onclick = () => {
    const box = $("metaedit");
    box.hidden = !box.hidden;
    if (!box.hidden) {
      $("me_emoji").value = (DOC && DOC.emoji) || "";
      $("me_color").value = (DOC && DOC.color) || "#60a5fa";
      $("me_emoji").focus();
    }
  };
  $("me_save").onclick = async () => {
    const r = await api("/api/meta", { doc: docid, emoji: $("me_emoji").value.trim(),
                                       color: $("me_color").value });
    if (r.ok) {
      toast("已改 — favicon 一起换了");
      $("metaedit").hidden = true;
      $("fav").href = "/favicon.svg?doc=" + encodeURIComponent(docid) + "&t=" + Date.now();
      refresh(true);
    }
  };
}

function initSearch() {
  const bar = $("searchbar"), inp = $("sq"), res = $("sres");
  $("searchBtn").onclick = () => { bar.hidden = !bar.hidden; if (!bar.hidden) inp.focus(); };
  let t = null;
  inp.oninput = () => { clearTimeout(t); t = setTimeout(run, 300); };
  inp.addEventListener("keydown", (e) => { if (e.key === "Escape") bar.hidden = true; });
  async function run() {
    const q = inp.value.trim();
    if (norm(q).length < 2) { res.innerHTML = ""; return; }
    try {
      const r = await fetch(`/api/search?q=${encodeURIComponent(q)}&doc=${encodeURIComponent(docid)}`);
      renderHits(res, (await r.json()).hits || [], q);
    } catch (e) { res.innerHTML = '<div class="hint">搜索失败</div>'; }
  }
}

/* ---------- boot ---------- */

(async () => {
  if (FOCUS_THREAD) {
    document.body.classList.add("thread-view");
    const status = document.createElement("div"); status.id = "branchStatus";
    const source = document.createElement("button"); source.id = "branchSource"; source.className = "ghost";
    source.onclick = () => RBExplore.source(FOCUS_THREAD);
    document.querySelector("main").before(source, status);
  }
  RBExplore.init({ docid, mode: MODE, thread: FOCUS_THREAD, request: api, refreshThreads: () => MODE === "d" && refresh(),
    onQuestion: () => openComposer(null, null, "explore"),
    onSource: hash => { history.replaceState(null, "", location.pathname + hash); gotoHash(); } });
  initNavigation();
  if (MODE === "x") { await bootReadonly(); return; }
  initSideLayout();
  initSelCapture();
  document.addEventListener("click", (e) => {   // 划词高亮点击 → 展开对应卡
    const mk = e.target.closest && e.target.closest("mark.selmark");
    if (!mk) return;
    const t = THREADS.find(x => x.id === mk.dataset.tid);
    if (t) { activeThread = t.id; expanded.add(t.id); inlineClosed.delete(t.block); patchInline(); patchSide(); syncActiveThread();
      const el = document.getElementById(t.id);
      const fold = el && el.closest("details.donefold");
      if (fold) fold.open = true;
      if (el) { el.scrollIntoView({ behavior: "smooth", block: "nearest" }); flash(el); } }
  });
  $("fav").href = "/favicon.svg?doc=" + encodeURIComponent(docid);   // v2.4 F12: favicon 即身份
  initSearch();
  initMeta();
  $("seenBtn").onclick = async () => {        // v2.3: 批准/看完了 合一
    if (ROUND && ROUND.status === "answered") { openComposer(null, null, "approve"); return; }
    if (!DOC) return;
    const r = await api("/api/seen", { doc: docid, rev: DOC.sha });
    if (r.ok) toast(`看完了 — ${r.n_seen}/${r.n_total} 块记为看过`);
    refresh(true);
  };
  $("noteBtn").onclick = () => openComposer(null, null, "note");
  $("covchip").onclick = () => {
    const u = COV && COV.unseen && COV.unseen[0];
    if (!u) return;
    if (VIEW !== "doc") setView("doc");
    revealBlock(u.i);
    const sec = document.querySelector(`.block[data-i="${u.i}"]`);
    if (sec) { scrollSettle(sec); flash(sec); }
  };
  await loadDoc();
  await refresh();
  setView(VIEW);
  if (!FOCUS_THREAD) gotoHash();
  setInterval(refresh, 3000);
})();
