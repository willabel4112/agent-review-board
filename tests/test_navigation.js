"use strict";
const assert = require("node:assert/strict");
const nav = require("../static/navigation.js");
const blocks = [
  "# Doc", "Intro", "## First", "A", "### Child", "B",
  "## Second", "C", "## Second", "D", "```md\n# not a heading\n```",
].map((md, i) => ({ md, i, hash: "hash" + i, slug: "heading-" + i }));
const hs = nav.outline(blocks);
assert.deepEqual(hs.map(h => [h.i, h.end, h.parent]), [
  [0, 11, null], [2, 6, "heading-0"], [4, 6, "heading-2"],
  [6, 8, "heading-0"], [8, 11, "heading-0"],
]);
assert.deepEqual([...nav.hiddenBlocks(hs, new Set(["heading-2"]))], [3, 4, 5]);
assert.deepEqual(nav.ancestors(hs, 5).map(h => h.i), [0, 2, 4]);
assert.deepEqual(nav.ancestors(hs, 6).map(h => h.i), [0, 6]);
assert.equal(nav.outline([{ md: "## A ##\n", i: 0, slug: "a", hash: "a" }])[0].text, "A");
assert.deepEqual([...nav.blockIds([{ i: 0, hash: "a" }, { i: 1, hash: "a" }, { i: 2, hash: "b" }]).values()],
  ["b-a", "b-a~1", "b-b"], "identical headings must have distinct reloadable links");
const resolved = { status: "resolved", kind: "block", turns: [
  { kind: "followup", text: "Already answered", draft: false },
  { kind: "answer", summary: "Done" },
  { kind: "followup", text: "New draft", draft: true },
] };
assert.equal(nav.matches(resolved, "todo"), true, "resolved draft must stay discoverable");
assert.equal(nav.matches(resolved, "drafts"), true);
assert.equal(nav.followups(resolved).latest.text, "New draft");
resolved.turns.pop();
assert.equal(nav.matches(resolved, "todo"), false);
assert.equal(nav.matches(resolved, "drafts"), false);
assert.equal(nav.matches(resolved, "followups"), true);
assert.equal(nav.matches({ status: "draft", kind: "note" }, "drafts"), false);
console.log("navigation: ALL GREEN");
