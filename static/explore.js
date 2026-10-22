/* Exploration is a projection of delivered threads + existing subdocs, not another data store. */
"use strict";
window.RBExplore = (() => {
  const $ = id => document.getElementById(id);
  const embedded = new URLSearchParams(location.search).get("embedded") === "1" && window.parent !== window;
  let opts, family, signature = "", lastFetch = 0, fetching = null, paused = false, returnFocus = null, returnScroll = null;
  const frames = new Map(), trail = [], folds = new Map();
  const labels = { open: "Pending", answered: "Answered", resolved: "Resolved" };

  function safeURL(value) {
    try {
      const u = new URL(value, location.href);
      if (u.origin !== location.origin || !/^\/(d\/[^/]+|x\/[^/]+\/.+)$/.test(u.pathname)) return null;
      u.searchParams.delete("embedded");
      return u.pathname + u.search + u.hash;
    } catch (_) { return null; }
  }
  function tellParent(action, payload = {}) {
    window.parent.postMessage({ channel: "rb-explore", action, ...payload }, location.origin);
  }
  function openThread(doc, cid, title) {
    openURL(`/d/${encodeURIComponent(doc)}?thread=${encodeURIComponent(cid)}`, title || "Question branch");
  }
  function openURL(value, title) {
    const href = safeURL(value);
    if (!href) return;
    if (embedded) { tellParent("open", { href, title }); return; }
    const u = new URL(href, location.origin);
    if (u.pathname === location.pathname && !u.searchParams.has("thread") && opts.mode === "d") {
      if (opts.thread) { location.assign(href); return; }
      close();
      if (u.hash) opts.onSource(u.hash);
      return;
    }
    if (!trail.length) {
      returnFocus = document.activeElement;
      returnScroll = { top: scrollY, left: scrollX };
      window.scrollTo({ ...returnScroll, behavior: "instant" });
    }
    const previous = trail.findIndex(item => item.href === href);
    if (previous >= 0) trail.splice(previous + 1);
    else trail.push({ href, title: title || "Explore further" });
    if (!frames.has(href)) {
      const frame = document.createElement("iframe");
      frame.className = "explore-frame"; frame.title = title || "Exploration content";
      const src = new URL(href, location.origin); src.searchParams.set("embedded", "1");
      frame.src = src.href;
      frame.onload = () => updateFrames();
      frames.set(href, frame); $("exploreFrames").appendChild(frame);
    }
    renderDrawer();
  }
  function updateFrames() {
    const active = trail.at(-1)?.href;
    for (const [href, frame] of frames) {
      frame.hidden = href !== active;
      frame.contentWindow?.postMessage({ channel: "rb-explore", action: "active", active: href === active }, location.origin);
    }
    syncTreeActive();
  }
  function syncTreeActive() {
    const active = trail.at(-1)?.href;
    document.querySelectorAll(".explore-item").forEach(button => button.classList.toggle("active", button.dataset.href === active));
  }
  function close() {
    trail.length = 0; renderDrawer();
    returnFocus?.focus?.({ preventScroll: true }); returnFocus = null;
    if (returnScroll) window.scrollTo({ ...returnScroll, behavior: "instant" });
    returnScroll = null;
  }
  function renderDrawer() {
    const active = trail.at(-1);
    $("exploreDrawer").hidden = !active; $("exploreShade").hidden = !active;
    document.body.classList.toggle("exploring", !!active);
    for (const el of [$("topbar"), document.querySelector("main"), $("roundbar")]) if (el) el.inert = !!active;
    updateFrames();
    if (!active) return;
    $("exploreBack").textContent = trail.length > 1 ? "← Back one level" : "← Back to source";
    $("exploreExternal").href = active.href;
    const crumbs = $("exploreTrail"); crumbs.replaceChildren();
    const root = document.createElement("button"); root.textContent = "Source"; root.onclick = close; crumbs.appendChild(root);
    trail.forEach((item, i) => {
      const button = document.createElement("button"); button.textContent = item.title; button.title = item.title;
      if (i === trail.length - 1) button.setAttribute("aria-current", "page");
      button.onclick = () => { trail.splice(i + 1); renderDrawer(); };
      crumbs.append(" › ", button);
    });
    $("exploreBack").focus({ preventScroll: true });
  }
  function treeNode(node, children, seen) {
    if (seen.has(node.key)) return document.createElement("span");
    const path = new Set(seen); path.add(node.key);
    const wrap = document.createElement("div"); wrap.className = "explore-node";
    const row = document.createElement("div"); row.className = "explore-row";
    const descendants = (children.get(node.key) || []).filter(n => !path.has(n.key));
    const sub = document.createElement("div"); sub.className = "explore-children";
    if (descendants.length) {
      const toggle = document.createElement("button"); toggle.className = "explore-fold";
      const sync = () => {
        sub.hidden = folds.get(node.key) === true;
        toggle.textContent = sub.hidden ? "▸" : "▾";
        toggle.setAttribute("aria-label", `${sub.hidden ? "Expand" : "Collapse"} branch ${node.title}`);
        toggle.setAttribute("aria-expanded", String(!sub.hidden));
      };
      toggle.onclick = () => { folds.set(node.key, !sub.hidden); sync(); }; sync(); row.appendChild(toggle);
    }
    const button = document.createElement("button"); button.className = "explore-item";
    button.dataset.key = node.key;
    button.dataset.href = node.href;
    if (node.key === "d:" + opts.docid) button.classList.add("current");
    button.disabled = node.exists === false;
    button.title = [node.question || node.title, node.quote, node.summary].filter(Boolean).join("\n");
    const title = document.createElement("span"); title.className = "explore-name";
    title.textContent = `${node.icon || "↗"} ${node.title}`;
    const status = document.createElement("small"); status.className = "explore-status";
    status.textContent = node.exists === false ? "File not on this machine" : node.archived ? "Archived" : node.status || "Document";
    button.append(title, status); button.onclick = () => openURL(node.href, node.title);
    row.appendChild(button); wrap.append(row, sub);
    for (const child of descendants) sub.appendChild(treeNode(child, children, path));
    return wrap;
  }
  function renderTree(data) {
    const sig = JSON.stringify(data);
    if (sig === signature) return;
    signature = sig; family = data;
    const nodes = new Map(), children = new Map();
    for (const d of data.docs) nodes.set("d:" + d.id, { ...d, key: "d:" + d.id,
      parentKey: d.parent ? "d:" + d.parent : null, href: "/d/" + encodeURIComponent(d.id), icon: d.emoji || "◇" });
    for (const b of data.branches) nodes.set(`t:${b.doc}:${b.id}`, { ...b, key: `t:${b.doc}:${b.id}`,
      parentKey: "d:" + b.doc, status: labels[b.status],
      href: `/d/${encodeURIComponent(b.doc)}?thread=${encodeURIComponent(b.id)}` });
    for (const d of data.docs) {
      const born = d.born, key = born && `t:${born.doc}:${born.cid}`;
      if (key && nodes.has(key)) nodes.get("d:" + d.id).parentKey = key;
    }
    for (const node of nodes.values()) {
      if (!children.has(node.parentKey)) children.set(node.parentKey, []);
      children.get(node.parentKey).push(node);
    }
    const tree = $("exploreTree"); tree.replaceChildren();
    const root = nodes.get("d:" + data.root);
    if (root) tree.appendChild(treeNode(root, children, new Set()));
    syncTreeActive();
    $("exploreHint").hidden = data.docs.length > 1 || data.branches.length > 0;
  }
  async function refresh(force = false) {
    if (!opts) return;
    if (embedded) { if (force) tellParent("changed"); return; }
    if (fetching || (!force && Date.now() - lastFetch < 3000)) return fetching;
    lastFetch = Date.now();
    fetching = (async () => {
      try {
        const response = await fetch(`/api/explore/${encodeURIComponent(opts.docid)}`);
        if (!response.ok) throw new Error("Exploration tree unavailable");
        const data = await response.json();
        if (Array.isArray(data.docs) && Array.isArray(data.branches)) renderTree(data);
      } catch (_) { if (!family) $("exploreHint").textContent = "Exploration tree unavailable; try again shortly"; }
      finally { fetching = null; }
    })();
    return fetching;
  }
  async function promote(t) {
    const r = await opts.request("/api/explore", { doc: opts.docid, id: t.id });
    if (!r.ok) return;
    await refresh(true); openThread(opts.docid, t.id, t.text.split("\n")[0]);
    opts.refreshThreads();
  }
  function source(cid) {
    openURL(`/d/${encodeURIComponent(opts.docid)}#${cid}`, "Back to the source question");
  }
  function init(options) {
    opts = options;
    document.body.classList.toggle("embedded", embedded);
    $("exploreAsk").onclick = () => opts.onQuestion();
    $("exploreClose").onclick = close; $("exploreShade").onclick = close;
    $("exploreBack").onclick = () => { if (trail.length > 1) { trail.pop(); renderDrawer(); } else close(); };
    document.addEventListener("keydown", e => {
      if (e.key === "Escape" && trail.length) { e.preventDefault(); close(); }
    });
    document.addEventListener("click", e => {
      if (e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
      const a = e.target.closest?.("a[href]");
      if (!a || a.id === "exploreExternal" || a.hasAttribute("download")) return;
      const href = safeURL(a.href);
      if (!href || a.getAttribute("href").startsWith("#")) return;
      e.preventDefault(); openURL(href, a.textContent.trim() || "Explore further");
    });
    window.addEventListener("message", e => {
      if (e.origin !== location.origin || e.data?.channel !== "rb-explore") return;
      if (embedded && e.source === window.parent) {
        if (e.data.action === "active") { paused = !e.data.active; if (!paused) opts.refreshThreads(); }
        return;
      }
      if (![...frames.values()].some(frame => frame.contentWindow === e.source)) return;
      if (e.data.action === "open") openURL(e.data.href, String(e.data.title || "Explore further").slice(0, 160));
      if (e.data.action === "changed") { refresh(true); opts.refreshThreads(); }
    });
    refresh(true);
  }
  return { init, refresh, openURL, openThread, promote, source, isPaused: () => paused };
})();
