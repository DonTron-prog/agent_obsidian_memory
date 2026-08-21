# Agent Obsidian Memory

A local-first, observable memory system shared by Pi and Hermes Agent. Durable knowledge is concise Markdown in an Obsidian vault; both agents use a deterministic CLI for retrieval and controlled updates.

## Status and documents

The project is specified but not yet implemented.

- [Product specification](docs/product-specification.md) — **sole normative source; wins on conflict**
- [Technical specification](docs/technical-specification.md) — derived architecture and contracts
- [Implementation and validation plan](docs/implementation-plan.md) — derived delivery plan
- [Main user stories](docs/user-stories.md) — informative outcomes
- [Compatibility document](MEMORY_SYSTEM_SPECIFICATION.md) — navigation for the former specification path

## Repository boundaries

- This repository will contain code, tests, adapters, and documentation.
- The runtime vault will be a separate private Git repository at `/home/donald/agent-memory`.
- Transaction journals and backups live outside it at `/home/donald/.agent-memory-txn/`.
- Syncthing replicates vault content to the computer running Obsidian.
- Local Git commits are automatic; private-remote pushes are manual.

## MVP baseline

- Markdown is authoritative; retrieval is observable and deterministic. Semantic retrieval is deferred.
- Agents may explicitly create and update durable concepts during normal turns. Automatic reusable-knowledge extraction is deferred.
- Procedures remain ordinary concepts. Procedure-use tracking, success counters, promotion, generated `SKILL.md`, skill directories, and load-path work are deferred.
- Creation rejects exact slug and exact normalized-title duplicates. There is no fuzzy similarity threshold or confirmation override.
- Pi checkpoints may carry the native `session_compact` summary and stable entry ID. At Hermes gateway compression publication, persisted message-row boundaries and an unambiguous isolated-summary row/hash bind the event; the worker verifies that exact bounded row and stores only its isolated Hermes 0.20.0 summary segment, or records `native summary unavailable`. No second model, preserved tail/live user content, archived/raw conversation rows, or `pre_llm_call` history is used.
- Lifecycle callbacks atomically publish sanitized descriptors and remain non-blocking; abrupt-exit durability begins only after publication completes. On each start and under one lock, `memory worker --once` recovers `claimed/`, then claims and processes `ready/` one item at a time. Commit-before-delete replay is idempotent; bounded in-invocation retries move exhausted work to unwatched `failed/` for `memory retry`.
- One configured `worker.state_dir` supplies derived `ready/`, `claimed/`, and `failed/` paths. Installation renders the two watched paths into the systemd user `.path`, runs daemon-reload, and enables it plus user lingering for boot recovery; otherwise recovery waits for login. `memory doctor` detects failed/start-limited lifecycle units and stranded queues. There is no timer or application daemon.
- The MVP does not copy or manage agent files and does not create `agents/` or `skills/` vault directories. Snapshot, migration, cutover, and skill-loading work are deferred.
- Transaction, Git, dirty-file, target-hash, Syncthing-conflict, secret, attribution, authorization, privacy, and recovery protections remain required.
- Hermes 0.20.0 gateway `session:compress` exposes only platform, current/old session IDs, in-place status, and compression count—not summary, model, timestamp, or event ID. Its versioned event identity also includes persisted previous/current message-row boundaries and nullable candidate row/hash, so queued compressions and a restart/count reset remain distinct. Hermes state binding runs lazily and idempotently on every `pre_llm_call`, with persisted injection identity, compression lineage, and row boundary for restart/resume.
