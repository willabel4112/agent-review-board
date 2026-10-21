# Origin and usage cases

These are retrospective accounts of one operator's use, reconstructed from
dated design documents, a bounded search of historical conversations, and the
current implementation. They are not controlled productivity measurements.
Identities, project subjects, operational details, exact usage counts and private
source identifiers are omitted. The runnable examples use newly written content.

## The original request: a conversation with branches

The initial request was concrete: render an existing project Markdown file in a
local webpage, let the reader comment on it, and have an agent answer each comment.
The pain was repeated mixed-subset follow-up: five questions produced answers and
new questions, then the next message referred back to only some of them.

Two other constraints mattered. Large projects exceeded what the operator could
keep in working memory, and terminal output was a poor surface for plans, diagrams
and wiring explanations. The requested board was persistent and interactive.
The human commented; the agent changed the underlying document.

The first choices were a resident local service, an agent session in the loop,
and commenting without a browser document editor. Later navigation features grew
from this same need to preserve a question's location and the reader's place.

## Case 1: refining a substantial research report

A long research document and its appendix attracted dozens of anchored questions
and several waves of follow-up. Some answers stayed short; others became linked
documents. Follow-ups could refer to a previous answer without restarting the
whole discussion. A conclusion eventually moved into a maintained policy document.

The operator explicitly described that session as a success and said the newer
board made this kind of questioning and refinement possible. The historical
design record also reports corrections to several first-draft judgments.

The useful mechanism was continuity: each objection had a place, a response and
a path to another question. The board did not make the initial research reliable
by itself. It made the process of challenging and improving it tractable.

The demo reconstructs this pattern using a fictional search-tool evaluation plan.
It contains no original report excerpts or original comments.

## Case 2: a plan changed, but the reader could barely see it

An early reviewed plan underwent a substantial rewrite. The UI's short
acknowledgments did not show the scale of the changes. Worse, some comments lost
their anchors precisely because their suggestions had been incorporated.

The response was to associate comments with source revisions, map old blocks
forward, show block-level changes, and allow the human to accept a reviewed
version. A changed passage could then be shown as modified instead of treating
successful feedback as a broken reference.

The public tests use a synthetic migration plan with rewritten, inserted,
unchanged and deleted blocks. They verify this mechanism; they do not reproduce
or reveal the original operational plan.

## Case 3: using the board to improve the board

Its own backlog became a review document. Comments shaped reading coverage,
compact threads, visible session ownership, search, long-document navigation and
the later exploration view. The maintained backlog ties accepted changes to
implementation work.

This also exposed a failure worth publishing. The operator wanted the feeling of
working together on a specification, while many agent responses only explained
things in threads. A diff cannot reveal a document edit that never happened.
The workflow therefore requires specification changes to reach the source document,
with a short explanation in the thread and an edit association.

The newest exploration view lets a question become a branch and a longer answer
become a child document. A reading stack preserves the route back. This feature
is included and tested, but has less accumulated usage evidence than the earlier
comment and revision workflow.

## What can be checked publicly

| Statement | Public evidence | Limit |
|---|---|---|
| Rewritten passages can retain review context | Synthetic anchor/diff regression tests | Not the original plan or its exact statistics |
| Drafts and notes stay out of normal agent delivery | Queue and exploration tests | Filesystem access is not restricted by this convention |
| Review rounds distinguish answering from accepting | Round/approval tests and demo | Does not measure decision quality |
| Branches and child documents use existing data | Exploration tests and runnable demo | Does not prove context isolation or automatic agent routing |
| The operator found a research review useful | This retrospective, based on historical first-person feedback | An anecdote, not a causal comparison |
