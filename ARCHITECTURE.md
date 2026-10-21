# Architecture

The source Markdown is the document. The board renders it without rewriting its
bytes. The agent edits that source through its usual filesystem and Git workflow.

```text
human browser -> server -> document.comments.jsonl
agent session -> boardctl -> document.replies.jsonl
                      both streams -> board_lib -> browser views
agent editor -> document.md -> version snapshots and block diffs
```

## Files and ownership

| File | Responsibility |
|---|---|
| `board_lib.py` | Blocks, anchors, diff, snapshots, event folding, registry, claims and exploration projection |
| `server.py` | Loopback HTTP; rendering data and human comment events |
| `boardctl.py` | Agent-facing CLI; replies and edit associations |
| `static/app.js` | Document rendering, selection, threads, review rounds and incremental polling |
| `static/navigation.js` | Pure heading, fold, navigation and follow-up helpers |
| `static/explore.js` | Document family tree and same-origin iframe reading stack |
| `contracts/comments.md` | Versioned event semantics; detailed technical reference, currently Chinese |

Human events and agent events have separate append-only files with file locks.
Their timestamps determine the combined view. The local registry associates IDs
with documents and parent/child relationships. Snapshots are immutable Markdown
cached by content hash. Git history remains separate: a document revision is a
content hash, while an edit answer may also reference a Git commit.

## Invariants learned through use

- Anchors use content, quotations and revisions, not line numbers. An accepted
  edit may rewrite the very passage a comment refers to; use its old snapshot to
  distinguish a modified passage from an unresolvable anchor.
- Attribute changes at review-round granularity. One commit may address several
  comments; exact per-paragraph causality cannot be inferred from that fact.
- Rendering and navigation do not imply approval. Reading coverage is explicit,
  and rewritten blocks need to be read again.
- Unsent material is excluded from the agent queue and exploration tree. Private
  notes never become submitted comments.
- Parent/child links live in registry metadata. Adding navigation links to a
  source document during review would contaminate its diff with unrelated edits.
- Polling must preserve selection, focus and unsent input. It cannot rebuild the
  whole document every few seconds.
- Several comments anchored to the same old revision share a cached block diff.
  Recomputing that diff once per comment makes large reviews unnecessarily slow.
- Exploration is a projection of existing threads and documents. It is not a new
  chat store or an automatic model context manager.

## Public package boundary

`RB_DATA_DIR` selects a separate state directory; otherwise state lives beside the
source. Sidecars always live beside their documents. `RB_PORT` selects the loopback
port. `RB_HOST` supplies an optional display alias.

The public CLI reads explicit environment identities and does not consult a
private machine inventory or a harness session registry. Claims are local,
process-bound coordination, not a multi-user distributed lock. The original
service templates are retained under a generic public label; installation is
optional and the disposable demo does not install a service.
