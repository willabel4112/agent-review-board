# Usage

## Main controls

| Control | Action |
|---|---|
| Send now / Send | Deliver this comment or follow-up immediately |
| Save draft / Save | Keep a draft without delivering it |
| Submit batch | Review and submit the pending drafts together |
| Mark as read | Mark the current document version as read |
| Approve version | Accept the reviewed version when a round awaits acceptance |
| Changes / ●Edit / +New | View the diff / inspect a modified block / identify a new block |
| Request changes | Return a reviewed version with an explanation |
| Follow-up | Continue a thread with another question |
| Resolve / Reopen | Close or reopen a thread |
| Explore | Open a question branch |
| Back to source / Back one level | Restore the original place or previous reading layer |
| Watching | The session currently waiting for this document |
| Private note | Save a note excluded from submission and the agent queue |

Select a passage to anchor a comment, or use the block comment control. Send one
question immediately, or save several and submit them as a batch. A follow-up
returns an answered thread to the agent's pending queue after delivery.
Batch confirmation opens in a centered dialog, including when the sidebar is
hidden. A failed request leaves the list and error visible so you can retry.

## Agent loop

```sh
python3 boardctl.py pending --doc plan --json
python3 boardctl.py wait --doc plan --timeout 300
```

`wait` exits with 0 when work is available, 1 on timeout, and 3 if another live
session owns the claim. A running agent must consume that result; there is no
background model service. `reply` and `answer-edit` require `--summary`.

Write long answers under `<document>.answers/` and pass a document-relative path:

```sh
python3 boardctl.py reply plan c-0001 --summary "Why we changed the sample" \
  --doc-ref plan.md.answers/sampling.md
```

For an independently reviewable answer, register a child document:

```sh
python3 boardctl.py register /absolute/path/sampling.md --id sampling \
  --title "Sampling strategy" --parent plan --born-from c-0001
```

The tree shows the relation. The agent still decides which source passages and
answers to read. The tree itself does not create a new model conversation.

## Identity and configuration

For an arbitrary agent harness, export a real session identifier and label:

```sh
export RB_AGENT_SESSION="your-native-session-id"
export RB_AGENT_HARNESS="your-harness"
export RB_AGENT_NAME="your-agent-label"
```

In PowerShell, set the equivalent `$env:RB_AGENT_SESSION`,
`$env:RB_AGENT_HARNESS` and `$env:RB_AGENT_NAME` variables. Use your actual session
identifier; the demo's seeded identity is fictional.

Without the explicit session override, native `CODEX_THREAD_ID` or
`CLAUDE_CODE_SESSION_ID` is used if present. Otherwise the harness is unknown and
no native session is claimed. No model is inferred from the harness name.

`RB_DATA_DIR` chooses the local registry, snapshots and claim directory. Set it
consistently for the server and CLI. `RB_PORT` defaults to 8798. `RB_HOST` changes
the host display alias. The demo uses a separate temporary directory and prints
the exact environment needed if you want an agent to answer its comments.

## Storage and sharing

The registered Markdown remains where it is. Human events append to
`<document>.comments.jsonl`; agent events append to `<document>.replies.jsonl`.
Those files can travel with the document repository, but contain review history
and potentially private notes. Review them before committing or publishing.
The source repository's `.gitignore` excludes runtime state and sidecars by default.

Snapshots preserve old versions for display. Losing them loses historical views,
not the canonical document. A registry is local to each installation.

## Optional resident service

After trying the foreground server, `./install.sh` runs the core tests and installs
a per-user launchd service on macOS or systemd service on Linux. It also creates a
`~/.local/bin/boardctl` symlink. This changes your service configuration; it is not
required for the demo. The default service uses port 8798, so stop a foreground
instance first. Custom port/data environment variables need to be configured in
your service manager, not just the installation shell.

On Windows, use the [native Windows installer](WINDOWS.md). The same `RB_DATA_DIR`
must be selected for the service and CLI. Cross-machine access and forwarding
are outside the public package's scope. The old peer API and tunnel helper are
removed; existing peer files are ignored. If you previously installed a tunnel
service, stop/remove that service yourself; updating source does not change it.
