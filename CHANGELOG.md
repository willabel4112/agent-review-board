# Release notes

## 0.2.0 — Native Windows and local review updates

- Add native Windows support: whole-file event locks, cooperative readers,
  read-only process checks, atomic state/snapshot writes and UTF-8 CLI/logs.
- Add a Windows CLI launcher and an optional per-user Task Scheduler installer
  with install/start/status/stop/uninstall actions and ownership checks.
- Keep `RB_DATA_DIR` for both CLI and service; imports no longer create state
  directories. The Windows launcher selects Python 3.11+ through `py -3`, with
  explicit interpreter selection available.
- Keep batch confirmation visible in a centered dialog even with the sidebar
  hidden. Show progress and submission errors, and retain the list for retry.
- Validate snapshot IDs and resource paths across Windows/POSIX; support
  percent-encoded Unicode document IDs and filenames.
- Remove the peer homepage/API, SSH tunnel helper and its service template.
  The public package covers local operation on one machine. Existing peer
  configuration files are ignored; existing tunnel services are not modified.
- Add native platform regression coverage and CI on Windows, macOS and Linux.

## 0.1.1 — English interface

- Translate the document viewer, home page, exploration reader, tooltips,
  accessibility labels, CLI help and server messages into English.
- Allow sidebar filters to wrap and widen thread labels for longer English text.
- Replace the README and blog screenshots with the English synthetic demo.
- Keep stored event fields, document content and review behavior unchanged.

## 0.1.0 — first public release

Derived from the working v2.7 feature set: anchored threads and follow-ups,
immediate/batch delivery, review rounds and block diffs, reading coverage, local
session claims, exact search, collapsible long-document navigation, and question
branches with reversible child-document reading.

The public package adds `RB_DATA_DIR`, portable explicit/native session identity,
generic service labels, English documentation and synthetic examples. It removes
private environment integrations and replaces operational regression fixtures.
The Chinese interface is retained. No model API or responder is bundled.

Known limits: trusted single-user local operation, no authentication, no automatic
agent wake after the active process stops, no automatic context management,
and no Windows support (`fcntl` and optional POSIX service tools are used).
