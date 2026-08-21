# Product Specification

## 1. Document status

- **Product:** Agent Obsidian Memory
- **Status:** Reviewed MVP baseline incorporating the approved design decisions
- **Primary user:** Donald McGillivray
- **Agent clients:** Pi and Hermes Agent
- **Code repository:** `/home/donald/projects/agent_obsidian_memory`
- **Runtime vault:** `/home/donald/agent-memory`
- **Knowledge format:** Open Knowledge Format (OKF) v0.2

This is the sole normative project document. `README.md`, `MEMORY_SYSTEM_SPECIFICATION.md`, and the other documents under `docs/` are derived or informative; this product specification wins on any conflict. Normative words such as **MUST**, **SHOULD**, and **MAY** are used in their usual requirements sense.

## 2. Problem statement

Pi and Hermes are stateless across fresh contexts unless relevant information is supplied again. Similar work therefore requires repeated explanations of project locations, preferences, prior decisions, people, procedures, and task history. Existing memory products did not satisfy the use case because their memories were difficult to inspect or correct, their injection behavior was unclear, or their storage ontology did not match the user's mental model.

The required system is a local-first memory layer that remains legible as ordinary Markdown. It must provide just-in-time context without silently constructing an opaque user model, and it must become more useful as durable knowledge is explicitly captured and refined.

## 3. Product principles

### 3.1 Observable by construction

The user must be able to inspect the current memory corpus, agent-specific context, session summaries, change history, and retrieval audit from Obsidian. The system must not require a proprietary viewer to explain what it knows.

### 3.2 Markdown is authoritative

The canonical knowledge representation is Markdown with YAML frontmatter. Derived indexes and views may improve navigation, but a database or vector index must not become the authoritative store.

### 3.3 Shared memory, attributed authorship

Pi and Hermes share the same memories by default. Every created or materially updated concept records the responsible agent or human, the agent version when applicable, the exact provider/model identifier when applicable, a timestamp, and available source provenance.

### 3.4 Retrieval is progressive

A compact root index is supplied at the start of a new agent session. Agents then retrieve additional context only when a task requires it. The MVP does not inject the complete memory corpus and does not perform automatic semantic retrieval.

### 3.5 Durable knowledge is curated, not accumulated blindly

Each memory represents one self-contained concept and remains concise. Existing concepts are updated in place when facts change. Incorrect or obsolete concepts are removed from the active corpus rather than retained as conflicting alternatives. Git preserves recoverability.

### 3.6 Procedures remain ordinary concepts

A Procedure is an ordinary OKF concept in the MVP. Procedure-use tracking and conversion to executable skills require a later, separately approved design.

## 4. Goals

The MVP must:

1. store short, readable, linked memories in an Obsidian-visible vault;
2. allow both Pi and Hermes to search and retrieve the same corpus;
3. allow agents to create and update concepts proactively without an approval gate;
4. record authorship, exact model identity, source provenance, and managed changes;
5. make retrieval observable at the session level;
6. preserve native Pi compaction summaries and safely resolved native Hermes compression summaries as concise, evolving session checkpoints;
7. support direct Obsidian editing through an explicit reconciliation workflow;
8. protect user edits and synchronization conflicts from accidental overwrites;
9. preserve complete local Git history for managed concepts and summaries, with manual pushes to a private remote for backup;
10. synchronize changes to a local Obsidian vault through Syncthing; and
11. establish an end-to-end validation baseline before adaptive or semantic features are added.

## 5. Non-goals for the MVP

The MVP does not:

- execute reminders or scheduled tasks;
- replace Hermes's scheduler, Telegram gateway, or native session database;
- store raw Pi or Hermes transcripts in Obsidian;
- provide vector search, embeddings, reranking, or automatic semantic recall;
- define typed graph relations beyond standard Markdown links;
- provide a hard filesystem security boundary between trusted agent channels;
- watch all Obsidian edits continuously;
- automatically summarize native Hermes edits to `MEMORY.md` or `USER.md` in the OKF log;
- manage Pi extensions, Pi prompt templates, Hermes plugins, or Hermes hooks as vault content;
- copy or manage agent files or create `agents/` or `skills/` vault directories;
- perform agent-file, configuration, or skill migration, snapshots, symlink or native-path cutover, or prior Honcho/Open Second Brain migration;
- record procedure-use events or counters, evaluate promotion eligibility, generate `SKILL.md`, manage skill directories, or change skill load paths;
- automatically extract reusable knowledge from sessions; agents may still explicitly create or update durable concepts during normal turns;
- automatically push Git commits or require remote availability for local writes;
- pause Syncthing through its REST API;
- treat Syncthing as a backup system; or
- optimize prompts, retrieval ranking, or workflow policy before the baseline system has been observed in use.

## 6. Users and interaction surfaces

### 6.1 Human user

The user interacts through:

- Obsidian on a separate computer;
- a shell on the Linux server;
- Pi's terminal interface; and
- Hermes through its CLI or authenticated Telegram direct messages.

### 6.2 Agent clients

Pi and Hermes:

- receive the compact memory index at new-session startup;
- use the `memory` CLI for deterministic search, retrieval, and mutation;
- share concepts unless a future access policy states otherwise;
- identify themselves and their active model to the CLI; and
- may create or refine durable memory when reusable knowledge is discovered.

### 6.3 Trusted channels

Hermes's Telegram access is treated as trusted for this deployment because the agent does not participate in untrusted groups. Work-scoped knowledge may be retrieved in the authenticated Telegram direct message. This is a policy boundary, not hard filesystem isolation.

## 7. Information model

### 7.1 Concept granularity

A memory concept MUST:

- express one self-contained idea;
- have a non-empty title, concise description, and non-empty Markdown body;
- use a stable, readable, lowercase `kebab-case` filename;
- contain no more than 600 words in its Markdown body by default;
- link outward to related concepts or non-OKF vault files where useful; and
- carry one and only one scope.

An H1 corresponding to the title is recommended but not required.

Managed creation MUST use a configured type. Imported content with another type remains OKF-conformant but receives a local-policy warning until the vocabulary is explicitly extended. An explicit exceptional override MAY permit a long reference, but 600 words remains the normal enforced limit.

### 7.2 Initial concept types

The initial vocabulary is:

- `Project`
- `Person`
- `Preference`
- `Procedure`
- `Note`
- `Task`
- `Decision`
- `Reference`

The vocabulary may be extended later without reorganizing the corpus. Base OKF validation accepts unknown types. Managed creation rejects a type outside the configured vocabulary; imported or externally edited content receives a local-policy warning and must be reconciled only after the vocabulary is extended or the type is corrected.

### 7.3 Scopes

Each concept has exactly one scope:

- `work`: information strictly related to work at Info-Tech;
- `personal`: personal projects, tasks, people, and activities; or
- `global`: information that applies across work and personal contexts.

Examples include a work procedure (`type: Procedure`, `scope: work`), a personal coding project (`type: Project`, `scope: personal`), and a general communication preference (`type: Preference`, `scope: global`).

### 7.4 Notes and tasks

Notes contain lists, thoughts, and informal capture, including material sent through Hermes on Telegram. A Note records `content_owner: user` when it captures the user's lists or thoughts, even if an agent created the file, and `content_owner: agent` when it is agent-authored operational material. A user-owned note may be deleted only with an explicit `human:donald` authorization linked to the source instruction. Agents may remove their own obsolete notes through a logged CLI transaction.

Tasks store daily or scheduled task information only. The memory system does not trigger reminders. Completed tasks are removed from active memory; before deletion, reusable knowledge must be incorporated into an existing procedure or captured as a new procedure when warranted.

### 7.5 Relations

The MVP uses standard Markdown links. Link meaning is expressed by surrounding prose, in accordance with OKF v0.2. Typed relation metadata is deferred. Concepts may link to session summaries or other files outside the conformant OKF bundle; those files need not link back.

## 8. Memory lifecycle

### 8.1 Creation

Agents may create concepts proactively. Before creation, the agent and CLI must search for existing candidates. The CLI rejects an exact slug or exact normalized-title duplicate. Ordinary deterministic search results guide whether an existing concept should be updated; the MVP has no fuzzy similarity threshold or confirmation override. A distinct concept receives a distinct filename and title.

### 8.2 Update

Ordinary refinements and changed facts update the existing concept in place. The original creator remains recorded, while the latest meaningful editor and model are updated. Conflicting current versions must not be retained.

### 8.3 Verification

Concepts are unverified unless they have been checked. A direct user statement is provenance, not automatically a review of the stored representation. Human review is recorded explicitly through:

```bash
memory verify <concept-id>
```

A direct human invocation confirms the action interactively. An agent may perform the equivalent action only after an explicit user instruction and must provide the authorizing session or message as provenance. Machine checks use an agent or process actor.

Trust presentation follows OKF v0.2: no `verified` field is **unverified**, verification only by non-human actors is **machine-confirmed**, and any current `human:*` verification is **human-reviewed**. A meaningful content or source change clears current verification; Git and `log.md` retain the historical review event.

### 8.4 Staleness

`stale_after` is optional. It is used only where a meaningful review or expiry date exists, such as active projects, time-sensitive work references, procedures tied to changing systems, or scheduled tasks. Durable people facts and preferences do not receive arbitrary expiration dates.

### 8.5 Deletion

Incorrect or obsolete concepts are deleted from the active bundle. Routine completed tasks are also deleted. The managed log records the deletion, and Git retains the recoverable history. OKF `deprecated` status is reserved for rare cases where a historical concept must remain linkable.

### 8.6 Deferred procedure-to-skill pipeline

Procedures remain ordinary concepts in the MVP. Procedure-use events, success counters, eligibility evaluation, automatic promotion, generated `SKILL.md` packages, skill-directory management, and load-path work are deferred in full.

## 9. Retrieval behavior

### 9.1 New sessions

A new Pi or Hermes session receives only the compact root `memory/index.md` plus retrieval instructions. Native agent files that Pi or Hermes already injects are not injected a second time.

### 9.2 Agentic search

Agents retrieve context when needed by:

1. inspecting the root index;
2. inspecting the generated concept index or a filtered Obsidian/CLI view;
3. running metadata or full-text searches; and
4. opening only the relevant concepts.

The first implementation uses filenames, OKF metadata, tags, links, and full-text search. Results use one fixed ordering: exact ID or slug, exact title, partial title, tags and description, then body text. Within the same match tier, results are sorted case-insensitively by title and then by concept ID. The CLI explains matched fields; ordering is not configurable in the MVP.

### 9.3 Retrieval observability

Agent retrieval through `memory search` and `memory show` is durably recorded in a per-session audit spool. Each event includes:

- timestamp;
- agent;
- exact active model;
- query or reason when supplied; and
- concepts returned or opened.

The spool is materialized into the active session summary at the next checkpoint or finalization, avoiding a Git commit and synchronized file mutation for every read. The session summary distinguishes automatically injected context from agent-requested retrieval. Direct `cat`, `rg`, or filesystem reads cannot be reliably audited; agent instructions therefore require the CLI for memory retrieval.

## 10. Session summaries

### 10.1 Storage model

Raw Pi session JSONL and Hermes `state.db` remain in their native locations. Only concise Markdown summaries enter the vault. Each logical session has one evolving summary file with a stable session ID and sequentially indexed checkpoints.

### 10.2 Checkpoint triggers

A checkpoint is created:

- after each observable Pi or Hermes context compaction/compression;
- before a session is reset;
- when `/new` replaces the current session;
- when an active session is otherwise finalized or terminated; and
- when an equivalent native lifecycle event is available.

Repeated compactions append checkpoints to the same file. Events are idempotent so retries do not duplicate checkpoints. Completed checkpoint text may be edited, but anchors should remain stable and Git preserves prior versions. A Pi `session_compact` descriptor may contain `compactionEntry.summary` and the stable compaction entry ID exposed by Pi. A Hermes gateway or Telegram `session:compress` event is only a committed-compression signal; its handling and Hermes CLI reset/finalization behavior are defined in Section 18.

### 10.3 Checkpoint content

A checkpoint stores the native Pi summary exposed by `session_compact`, or the single classified native compacted-summary message safely resolved for a Hermes gateway compression, unchanged in meaning. It also stores available host provenance: agent and version, exact active model when exposed by a separate reliable host context, session and native event identifiers, trigger, and timestamp. The session file materializes pending context-access audit and lifecycle state.

For Hermes gateway compression, summary resolution MUST be bound when the committed-compression hook publishes its descriptor, not inferred later from `compression_count`. Under the persisted adapter-state lock, the hook reads only the message row ID, Hermes summary-classification metadata, and content needed to inspect rows after the persisted previous message-row high-water boundary through the current high-water boundary. The hook atomically publishes a descriptor with the five exposed lineage fields, both boundaries, and, only when exactly one native summary segment is unambiguously isolated, that candidate row ID and the SHA-256 hash of the isolated segment. It contains neither summary text nor raw conversation. Its versioned canonical event ID hashes all of those fields, including both boundaries and the nullable candidate row/hash, so multiple queued in-place compressions and a restart that resets `compression_count` remain distinct. The hook persists the current boundary and old/new lineage before returning. Within one logical lineage, the worker materializes queued Hermes descriptors in ascending message-row boundary order so their distinct event IDs become ordered checkpoints.

`memory worker --once` MUST use the descriptor's exact bounded candidate row, re-isolate the native summary segment, and verify both row ID and SHA-256 hash before storing only that segment. Candidate metadata may be inspected for classification, but non-summary rows and preserved conversation content MUST NOT be serialized into a descriptor, diagnostic, or checkpoint. Hermes 0.20.0 isolation accepts only a recognized standalone or merged summary carrier with exactly one unambiguous segment delimited according to that version's recognized summary prefix, merged delimiter when present, and summary end marker. The isolated segment is the native summary body after the recognized prefix and before its matching end marker, and its UTF-8 bytes are the hash input. A standalone carrier may contain only that framing/segment plus permitted surrounding whitespace; in a merged carrier, the recognized delimiter must uniquely separate preserved tail or live user content from the framed summary, and all content outside the isolated body is excluded. Missing, repeated, misordered, or conflicting markers, multiple candidate carriers, a changed row, or a hash mismatch result in available lifecycle metadata and the literal status `native summary unavailable`.

The memory system MUST NOT run a second LLM summarization pass, configure an active or dedicated summarizer model, synthesize a replacement, or copy raw dialogue or routine tool output into the vault.

### 10.4 Explicit knowledge capture

Automatic reusable-knowledge extraction is deferred. During normal turns, agents may still explicitly create or update a durable concept through the ordinary validated transaction path.

### 10.5 Lifecycle worker and failure behavior

Adapters atomically publish sanitized, idempotent lifecycle descriptors to a durable non-hidden `ready/` directory and return within a fixed timeout. Durability after abrupt agent exit begins only when atomic descriptor publication completes; the system does not promise recovery when the lifecycle handler never ran or publication did not complete. Reset, `/new`, and finalization descriptors always request materialization of pending access audit and lifecycle state, whether or not a native summary is available.

On every start, `memory worker --once` acquires one worker lock, recovers descriptors already in the non-hidden `claimed/` directory first, then atomically claims and processes `ready/` descriptors one at a time until both directories are empty. A crash after a checkpoint transaction commits but before descriptor deletion is replayed safely by event-id idempotency. Retryable work receives bounded retries with capped backoff inside the same oneshot invocation; exhausted work moves atomically to an unwatched `failed/` directory. No delayed retry remains in watched `ready/`, and no timer or application daemon is used. `memory retry` atomically republishes selected failed work to `ready/`.

`worker.state_dir` is the only configurable worker-state directory; non-hidden `ready/`, `claimed/`, and `failed/` paths are derived beneath it. One systemd user `.path` unit uses `DirectoryNotEmpty=` for the resolved `ready/` and `claimed/` paths, names the `Type=oneshot` service as its target, and has `WantedBy=default.target`. Installation renders both resolved paths into the user's `.path` unit, runs `systemctl --user daemon-reload`, enables the path unit, and enables user lingering so queue recovery can run after boot without login; without lingering, recovery occurs at the next user login. The service runs `memory worker --once`.

Lifecycle callback and worker failures never block compaction, reset, `/new`, finalization, or exit. Failures are reported through the available interface, `system/errors.md`, and the next agent turn when immediate notification is unavailable. `memory doctor` detects failed path/service units, systemd start-limit failures, and stranded `ready/`, `claimed/`, or `failed/` state. After diagnosing repeated hard crashes, recovery resets both units with `systemctl --user reset-failed agent-memory-lifecycle.path agent-memory-lifecycle.service`, then restarts and enables the trigger with `systemctl --user enable --now agent-memory-lifecycle.path`.

## 11. Agent-specific files and skills

The MVP does not copy, manage, migrate, or expose Pi/Hermes agent files or skills in the vault and does not create `agents/` or `skills/` directories. Native files, skill packages, configurations, and load paths remain outside memory-system management.

Any future snapshots, migration, secret audit, canonical-path change, symlink cutover, divergence handling, rollback tooling, skill generation, or skill loading require a separately approved post-MVP design.

## 12. Obsidian experience

### 12.1 Synchronization

Obsidian operates on a local filesystem replica synchronized bidirectionally with the server through Syncthing. SSHFS is not the primary editing path because filesystem event propagation and atomic-save behavior are unreliable for hot reload.

### 12.2 Views

The Obsidian Bases core plugin presents filtered views over OKF properties. Initial named views include:

- Work
- Projects
- People
- Preferences
- Procedures
- Notes
- Tasks
- Decisions
- References

The underlying files remain ordinary Markdown. Freshness and verification remain available as columns and manual filters rather than additional required named views.

### 12.3 Direct editing

Direct Obsidian edits are permitted but are not automatically logged in the MVP. They are adopted through:

```bash
memory reconcile <concept-id> --summary "Reason for the correction"
```

Reconciliation validates the concept, preserves original creation metadata, records the edit as `human:donald`, updates derived artifacts, appends the log, and commits the change.

## 13. Change history and backup

### 13.1 Managed log

The OKF bundle uses the reserved root `memory/log.md`, not `CHANGELOG.md`. It records concise, newest-first summaries of CLI-managed concept creations, updates, verifications, renames, reconciliations, and deletions.

### 13.2 Git

Each successful CLI write transaction creates one local Git commit. A transaction may include several related concept mutations, affected index entries, the log entry, and related session metadata. Complete diffs and recovery are provided by Git.

### 13.3 Private remote

The vault is a separate private Git repository. Local commits are the MVP durability boundary. The user configures, reviews, and pushes to the private remote manually; remote availability or configuration never blocks local memory writes. `memory doctor` may warn when local commits have not been pushed.

## 14. Safety requirements

### 14.1 Concurrency

Reads may run concurrently. All write transactions are serialized by one short-lived global vault lock. Lock timeout fails clearly rather than waiting indefinitely.

### 14.2 Dirty files

If a transaction targets a file with uncommitted changes, it aborts and reports the conflict. It must not overwrite or silently commit the user's work. Any pre-existing staged Git change blocks managed writes and is reported clearly. Unrelated unstaged files remain untouched. The CLI checks current content hashes immediately before replacement and aborts on detected concurrent changes. It does not pause Syncthing or depend on Syncthing availability, leaving a documented narrow race window.

### 14.3 Syncthing conflicts

If a Syncthing conflict copy is detected anywhere in the vault, managed writes fail closed until the conflict is resolved. Reads and inspection remain available.

### 14.4 Secrets

The vault and code repository must not contain `.env`, authentication tokens, API keys, bot tokens, OAuth credentials, or other secrets. Managed writes reject secret-bearing filenames and content before staging. Notifications, lifecycle descriptors, worker diagnostics, and error records must redact raw prompts, secret values, and unrestricted tool output, including durable lifecycle state stored outside Git.

### 14.5 Policy boundary

Work memory is accessible to Pi and Hermes, including authenticated Telegram use. This is not a hard security boundary because agents with unrestricted shell access can read the vault directly. Hard isolation is deferred.

## 15. Notifications and health

Failures are surfaced through all available paths:

- Pi TUI notifications;
- Hermes notifications in the originating interface, including the authenticated Telegram DM where possible;
- persistent `system/errors.md` entries;
- a warning on the next agent turn when immediate delivery fails; and
- `memory doctor`, including failed lifecycle path/service units, systemd start-limit state, and stranded worker queues.

Error records include timestamp, agent, session ID, operation, concise failure, and retry state. They exclude sensitive content.

## 16. MVP acceptance criteria

The MVP is accepted only after an end-to-end test demonstrates that both Pi and Hermes can:

1. receive the root index in a new session;
2. search for and open relevant concepts;
3. create and update the same shared concept;
4. record original creator, latest editor, exact models, and source provenance;
5. record context access in the correct session summary;
6. update OKF indexes and `log.md`;
7. create one atomic Git commit per managed transaction;
8. reuse Pi's exposed native compaction summaries and, for Hermes gateway/Telegram compression, bind exact message-row boundaries and an isolated-summary row/hash at publication, then verify and store only that bounded segment, while materializing reset, `/new`, and finalization lifecycle/audit state without synthesizing unavailable summaries;
9. drain durable `claimed/` then `ready/` lifecycle descriptors idempotently with `memory worker --once` under rendered dual-`DirectoryNotEmpty=` systemd user path activation and a `Type=oneshot` service, including post-claim and commit-before-delete crashes, backlog, lingering boot/next-login recovery, failed-descriptor retry, and start-limit diagnosis/reset recovery;
10. synchronize changes into Obsidian;
11. present expected Bases views;
12. reconcile a direct Obsidian edit;
13. preserve uncommitted user work during a targeted-file conflict;
14. block writes during a simulated Syncthing conflict;
15. recover or retry a simulated lifecycle-materialization failure without blocking the agent session; and
16. keep secrets and raw session stores outside the vault and keep secrets out of lifecycle/error state outside Git.

Focused automated integration tests, rather than the live acceptance exercise, verify Note deletion authorization and verification invalidation.

Semantic search and adaptive retrieval work must not begin until this workflow is operating and its failures have been observed.

## 17. Deferred roadmap

### 17.1 Retrieval optimization

After the MVP has produced a useful corpus, retrieval misses and irrelevant reads may be evaluated. Possible later work includes metadata ranking, link traversal, BM25, embeddings, reranking, and task-specific retrieval policies.

### 17.2 Adaptive workflows

Later versions may detect recurring tasks, suggest procedure refinement, evaluate retrieved context usefulness, or propose a complete procedure-to-skill pipeline. Procedure-use events, success counters, eligibility, automatic promotion, generated `SKILL.md`, skill directories, and load-path work require measured outcomes and a separately approved design.

### 17.3 Additional observability

Potential additions include watcher-based logging of native Hermes memory edits, automated handling of direct Obsidian edits, retrieval-quality dashboards, and per-concept usage analytics.

### 17.4 Stronger security

If Hermes later participates in untrusted channels, work knowledge requires a stronger boundary using separate profiles, Unix permissions, sandboxing, or channel-specific agent processes.

### 17.5 Agent-file and skill work

All snapshots, managed `agents/` or `skills/` vault directories, migration, and cutover are deferred. A later proposal may make selected vault files canonical and connect native agent paths through configuration or symlinks, but it must separately define backups, secret auditing, skill classification, collision handling, divergence, validation, cutover, and rollback.

## 18. Hermes CLI compatibility decision

Hermes Agent 0.20.0 emits `session:compress` for gateway sessions, including Telegram, but the installed hook exposes only `platform`, `session_id`, `old_session_id`, `in_place`, and `compression_count`. It exposes no summary, model, timestamp, or native event ID. Its public CLI plugin hook set does not expose an equivalent compression event. The approved MVP behavior is therefore:

- treat gateway `session:compress` only as a committed-compression signal;
- under persisted adapter state, capture the previous and current message-row high-water boundaries after each committed compression and inspect only bounded candidate row IDs, summary-classification metadata, and content;
- atomically publish a descriptor containing the five exposed lineage fields, both boundaries, and, when unambiguous, the isolated native-summary candidate row ID and SHA-256 hash, but no summary or raw conversation text;
- derive the event identity from a versioned canonical encoding of all published identity fields, including boundaries and nullable candidate identity, rather than from the five hook fields alone, and persist the current boundary plus old/new lineage before returning;
- have `memory worker --once` fetch the exact bounded candidate row, re-isolate it using Hermes 0.20.0's recognized standalone/merged summary classification, prefix, merged delimiter, and end marker, verify row ID/hash, and store only the isolated segment with any preserved tail/live user content excluded;
- never serialize archived/raw conversation rows or `pre_llm_call` conversation history;
- if classification, isolation, or hash verification is ambiguous or fails, record lifecycle metadata and `native summary unavailable`;
- on Hermes CLI reset, `/new`, and finalization, flush lifecycle and context-access audit state; and
- never reconstruct an unavailable intermediate summary from raw dialogue/tool output or with another model.

Hermes state binding MUST be lazy and idempotent on every `pre_llm_call`, because `on_session_start` may not run for a continued or resumed session. Durable adapter state records the injection identity, old/new compression lineage, and current message-row high-water boundary so restart, resume, and rotated-session processing neither duplicate injection nor orphan checkpoints. Model provenance may be used only when exposed by a separate reliable Hermes context; the gateway compression hook itself supplies none.

An invasive Hermes core patch solely for CLI compression is out of scope for the MVP.
