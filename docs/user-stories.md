# Main User Stories

These stories are informative summaries of [product-specification.md](product-specification.md), the sole normative source. The product specification wins on conflict.

## 1. Inspect and organize memory in Obsidian

**As Donald, I want** durable knowledge stored as concise Markdown concepts with clear metadata, **so that** I can inspect, filter, link, and correct everything the agents know using Obsidian.

**Acceptance criteria:**
- Each concept has one type and scope, a title, description, body, and attribution.
- Concepts are normally limited to 600 words and use stable, readable filenames.
- Obsidian Bases provides Work, Projects, People, Preferences, Procedures, Notes, Tasks, Decisions, and References views.
- No proprietary database is required to understand the memory corpus.

## 2. Share context between Pi and Hermes

**As Donald, I want** Pi and Hermes to use the same canonical concept store, **so that** I do not need to repeat context when switching agents or interfaces.

**Acceptance criteria:**
- Both agents receive the compact root index once per logical session; Hermes lazily and idempotently binds persisted injection identity on every `pre_llm_call` so continued/resumed sessions do not depend on `on_session_start`.
- Both agents can search, open, create, and update the same concepts through the `memory` CLI.
- Work memories are available through authenticated Hermes Telegram direct messages.
- The complete corpus is not automatically injected into every session.

## 3. Retrieve relevant context just in time

**As Donald, I want** agents to retrieve only the memory needed for the current task, **so that** context remains focused and explainable.

**Acceptance criteria:**
- Search covers concept IDs, titles, metadata, tags, descriptions, links, and body text.
- Results follow a fixed deterministic order and explain matched fields.
- Exact slug and exact normalized-title duplicates are rejected on creation.
- The MVP has no embeddings, vector search, fuzzy duplicate threshold, or confirmation override.

## 4. Capture and refine durable knowledge explicitly

**As Donald, I want** agents to create or refine useful concepts without requiring approval for every change, **so that** normal work can preserve durable knowledge.

**Acceptance criteria:**
- Agents search ordinary deterministic candidates before creation.
- Existing concepts are updated in place when they represent the same knowledge.
- Every managed change records creator, latest editor, exact model when applicable, timestamp, and available source.
- Automatic reusable-knowledge extraction is deferred; agents act explicitly during normal turns.
- Procedures remain ordinary concepts; all procedure-use and skill-promotion machinery is deferred.

## 5. Review and correct memories directly

**As Donald, I want** to edit concepts directly in Obsidian and explicitly reconcile those edits, **so that** I retain control without losing provenance.

**Acceptance criteria:**
- `memory reconcile` validates and commits a selected direct edit.
- Original creation metadata is preserved; the latest editor becomes `human:donald`.
- Meaningful edits clear active verification until reviewed again.
- `memory verify` records explicit human review, and agents may assert it only with user authorization and provenance.

## 6. Understand what context each agent used

**As Donald, I want** each session to show which memory was injected, searched, and opened, **so that** I can understand how stored context influenced the work.

**Acceptance criteria:**
- `memory search` and `memory show` durably record access events outside the synchronized vault.
- Events include timestamp, query or reason, concepts, agent, and exact model.
- Access records are materialized at the next checkpoint, reset, `/new`, or finalization.
- Ordinary retrieval does not create a Git commit for every read.

## 7. Preserve native session continuity

**As Donald, I want** native Pi and Hermes compaction/compression summaries represented in evolving Markdown session files, **so that** host-provided continuity is visible without storing raw transcripts or paying for another summary pass.

**Acceptance criteria:**
- Observable lifecycle events append idempotent checkpoints to one file per logical session.
- Pi `session_compact` may supply the native summary and stable compaction entry ID.
- Hermes gateway `session:compress` is only a committed-compression signal. At publication, persisted adapter state binds its five exposed lineage fields to previous/current message-row high-water boundaries and, when unambiguous, an isolated-summary candidate row ID/hash; the versioned event ID includes all of them, not `compression_count` alone. The descriptor stores no summary, raw conversation, or model.
- The worker fetches only the exact bounded candidate row, repeats Hermes 0.20.0 standalone/merged summary-segment isolation, verifies row ID/hash, and stores only the isolated segment. Preserved tail/live user content, archived/raw conversation rows, and `pre_llm_call` history are never serialized.
- Reset, `/new`, and finalization flush pending access audit and lifecycle state.
- If a classified native summary cannot be resolved, the checkpoint records lifecycle metadata and `native summary unavailable`; no active/configured summarizer model reconstructs it.
- Raw Pi JSONL and Hermes session databases remain outside the vault.

## 8. Drain lifecycle work without blocking agents

**As Donald, I want** lifecycle materialization retried outside callbacks, **so that** compaction, reset, `/new`, finalization, and exit remain responsive and recoverable.

**Acceptance criteria:**
- Callbacks atomically publish sanitized, idempotent descriptors to durable ready state and return within a fixed timeout; abrupt-exit durability starts only after publication completes, not when a handler never ran.
- On every start and under one lock, `memory worker --once` recovers claimed descriptors first, then claims and processes ready descriptors one at a time until both queues are empty.
- Commit-before-delete replay is safe by event idempotency. Retryable work receives bounded backoff in that invocation; exhausted work moves to unwatched `failed/` until `memory retry` republishes it.
- A single configurable `worker.state_dir` supplies derived non-hidden `ready/`, `claimed/`, and `failed/` paths. Installation renders the resolved ready/claimed paths into both `.path` `DirectoryNotEmpty=` entries, runs daemon-reload, targets one `Type=oneshot` service, and enables it under `default.target`. User lingering provides boot recovery; otherwise backlog is recovered on next login.
- The worker has no daemon or timer. `memory doctor` reports failed/start-limited path or service units and stranded queue state; after crash diagnosis, the runbook resets both failed units and re-enables/starts the path.

## 9. Protect edits with atomic history and conflict checks

**As Donald, I want** managed writes to be transactional, versioned, and conflict-aware, **so that** agents cannot overwrite my work or commit unrelated changes.

**Acceptance criteria:**
- Writes are serialized and commit all related concept, link, index, and log changes together.
- Dirty targets, pre-staged Git changes, changed target hashes, and Syncthing conflict copies block writes clearly.
- Only transaction-owned paths are staged in one local Git commit.
- Interrupted transactions can be diagnosed and previewed before recovery.
- Reads remain available during write conflicts.

## 10. Synchronize safely and keep scope narrow

**As Donald, I want** memory changes synchronized to Obsidian while native agent files and skills remain untouched, **so that** the MVP stays private, resilient, and reviewable.

**Acceptance criteria:**
- Syncthing propagates vault content while excluding `.git`, machine-local state, secrets, and transient files.
- Git provides local history; pushes to a reviewed private remote remain manual.
- No `agents/` or `skills/` vault directories, visibility snapshots, migration, symlink cutover, or skill load-path changes are created by the MVP.
- Secrets, raw sessions, and unrestricted tool output remain outside the vault and error records.
