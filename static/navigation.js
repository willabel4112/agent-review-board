/* Pure view helpers. No document writes: folding is a per-browser preference. */
(function (root) {
  "use strict";
  function outline(blocks) {
    const headings = [], stack = [];
    for (const b of blocks) {
      const match = b.md.match(/^(#{1,6})[ \t]+(.+?)[ \t]*#*[ \t]*(?:\n|$)/);
      if (!match) continue;
      const level = match[1].length;
      while (stack.length && stack[stack.length - 1].level >= level)
        stack.pop().end = b.i;
      const h = { key: b.slug, i: b.i, hash: b.hash, level,
        text: match[2].replace(/[*_`]/g, ""), end: blocks.length,
        parent: stack.length ? stack[stack.length - 1].key : null };
      headings.push(h); stack.push(h);
    }
    return headings;
  }
  function hiddenBlocks(headings, collapsed) {
    const hidden = new Set();
    for (const h of headings)
      if (collapsed.has(h.key))
        for (let i = h.i + 1; i < h.end; i++) hidden.add(i);
    return hidden;
  }
  function ancestors(headings, i) {
    return headings.filter(h => h.i <= i && i < h.end);
  }
  function blockIds(blocks) {
    const counts = new Map(), ids = new Map();
    for (const b of blocks) {
      const n = counts.get(b.hash) || 0;
      ids.set(b.i, "b-" + b.hash + (n ? "~" + n : ""));
      counts.set(b.hash, n + 1);
    }
    return ids;
  }
  function followups(t) {
    const turns = (t.turns || []).filter(u => u.kind === "followup");
    const drafts = turns.filter(u => u.draft);
    return { count: turns.length, drafts: drafts.length,
      latest: drafts[drafts.length - 1] || turns[turns.length - 1] || null };
  }
  function hasDraft(t) {
    return t.kind !== "note" && (t.status === "draft" || followups(t).drafts > 0);
  }
  function matches(t, filter) {
    if (filter === "drafts") return hasDraft(t);
    if (filter === "followups") return followups(t).count > 0;
    if (filter === "resolved") return t.status === "resolved";
    return filter === "all" || t.status !== "resolved" || hasDraft(t);
  }
  const api = { outline, hiddenBlocks, ancestors, blockIds, followups, hasDraft, matches };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.RBNav = api;
})(typeof window === "undefined" ? {} : window);
