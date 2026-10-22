# Release notes

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
