# Working through the board

Read README.md and ARCHITECTURE.md before changing the implementation. Update
contracts/comments.md before changing an event consumed by the server, CLI and UI.
Run the Python tests and the navigation JavaScript tests after relevant changes.
For platform changes, run `tests/test_platform.py` on native Windows as well;
POSIX emulation does not verify Windows support. Use synthetic documents and
isolated `RB_DATA_DIR` state. The public package is local-only; do not add private
machine inventories, remote PIN/auth setup, peer directories or SSH forwarding.

For document review:

1. Scope every request to a document: `python3 boardctl.py pending --doc ID --json`.
2. Read only delivered comments and follow-ups. Do not inspect raw sidecars,
   browser-only thread APIs, drafts or private notes to get more context.
3. For a specification or plan change, edit the canonical Markdown first. Verify
   the result, commit within the document repository's rules, then use
   `answer-edit ID COMMENT --commit SHA --summary "What changed"`.
4. For a question, use `reply ID COMMENT --file answer.md --summary "Conclusion"`.
   Put long answers in a document and use `--doc-ref`. Keep the summary useful
   without requiring the reader to open the full answer.
5. A branch marked `intent: explore` is a specific question. Answer that question;
   if it deserves its own Markdown document, register it with `--parent ID
   --born-from COMMENT`. Bring decisions affecting the parent back into its text.
6. Approval, resolve, and the meaning of unread material belong to the human.
   Do not infer agreement from silence or use the browser to approve your work.
7. `wait --doc ID --timeout 300` owns a claim only while its process is alive.
   Stop your wait process on cancellation. Do not promise replies after the agent
   session has stopped. Do not take over another live claim without instruction.

Use `RB_AGENT_SESSION`, `RB_AGENT_HARNESS` and `RB_AGENT_NAME` for explicit identity.
Otherwise the CLI recognizes native `CODEX_THREAD_ID` or `CLAUDE_CODE_SESSION_ID`.
Keep their meanings intact. Do not invent a native session or a model identity.
