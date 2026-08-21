# Product Specification

## 1. Document status

- **Product:** Agent Obsidian Memory
- **Status:** Reviewed MVP baseline incorporating the approved design decisions
- **Primary user:** Donald McGillivray
- **Agent clients:** Pi and Hermes Agent
- **Code repository:** `/home/donald/projects/agent_obsidian_memory`
- **Runtime vault:** `/home/donald/agent-memory`
- **Knowledge format:** Open Knowledge Format (OKF) v0.2

This specification records the product decisions established during requirements discovery. Normative words such as **MUST**, **SHOULD**, and **MAY** are used in their usual requirements sense.

## 2. Problem statement

Pi and Hermes are stateless across fresh contexts unless relevant information is supplied again. Similar work therefore requires repeated explanations of project locations, preferences, prior decisions, people, procedures, and task history. Existing memory products did not satisfy the use case because their memories were difficult to inspect or correct, their injection behavior was unclear, or their storage ontology did not match the user's mental model.

The required system is a local-first memory layer that remains legible as ordinary Markdown. It must provide just-in-time context without silently constructing an opaque user model, and it must become more useful as recurring work is captured, refined, and eventually promoted into reusable skills.

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

### 3.6 Learning follows evidence

A lightweight procedure begins as an OKF concept. It becomes an executable `SKILL.md` only after at least three successful uses, stable steps, and a clear verification method. This prevents speculative procedures from producing skill sprawl.

## 4. Goals

The MVP must:

1. store short, readable, linked memories in an Obsidian-visible vault;
2. allow both Pi and Hermes to search and retrieve the same corpus;
3. allow agents to create and update concepts proactively without an approval gate;
4. record authorship, exact model identity, source provenance, and managed changes;
5. make retrieval observable at the session level;
6. create concise, evolving session summaries on compaction, reset, and session termination;
7. expose selected Pi and Hermes files in the same vault while respecting their native formats;
8. support direct Obsidian editing through an explicit reconciliation workflow;
9. protect user edits and synchronization conflicts from accidental overwrites;
10. preserve complete local Git history for managed concepts and summaries, with manual pushes to a private remote for backup;
11. synchronize changes to a local Obsidian vault through Syncthing; and
12. establish an end-to-end validation baseline before adaptive or semantic features are added.

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
- migrate agent configuration, skills, symlinks, prior Honcho memories, or Open Second Brain memories;
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

The MVP uses standard Markdown links. Link meaning is expressed by surrounding prose, in accordance with OKF v0.2. Typed relation metadata is deferred. Concepts may link to files outside the conformant OKF bundle, including skills, agent files, and session summaries. Those files need not link back.

## 8. Memory lifecycle

### 8.1 Creation

Agents may create concepts proactively. Before creation, the agent and CLI must search for existing or similar concepts. An existing concept is updated when the knowledge belongs there. A distinct concept receives a distinct filename and title.

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

### 8.6 Procedure promotion

A procedure is promoted automatically when:

1. at least three successful use events are recorded;
2. its sequence of steps is stable;
3. it has a clear verification method; and
4. the target agent compatibility is known.

Each use event stores only its timestamp, outcome, and source checkpoint. The successful-use count is derived from the event list; actor, model, and detailed context remain available through the checkpoint.

Skills are placed in `shared` by default, otherwise in `pi-only` or `hermes-only`. The resulting skill follows the Agent Skills `SKILL.md` format, not OKF. After promotion, the source concept remains `type: Procedure`, retains its use history and provenance, links to the skill, and is shortened so it does not duplicate the complete executable instructions.

## 9. Retrieval behavior

### 9.1 New sessions

A new Pi or Hermes session receives only the compact root `memory/index.md` plus retrieval instructions. Native agent files that Pi or Hermes already injects are not injected a second time.

### 9.2 Agentic search

Agents retrieve context when needed by:

1. inspecting the root index;
2. inspecting the generated concept index or a filtered Obsidian/CLI view;
3. running metadata or full-text searches; and
4. opening only the relevant concepts.

The first implementation uses filenames, OKF metadata, tags, links, and full-text search. Results use one fixed ordering: exact ID or slug, exact title, partial title, tags and description, body text, then a stable alphabetical tie-break. The CLI explains matched fields; ordering is not configurable in the MVP.

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

Repeated compactions append checkpoints to the same file. Events are idempotent so retries do not duplicate checkpoints. Completed checkpoint text may be edited, but anchors should remain stable and Git preserves prior versions. Hermes gateway and Telegram compression is checkpointed immediately; Hermes CLI compression is incorporated at reset or finalization as defined in Section 18.

### 10.3 Summary content

Each session summary contains:

- objective;
- essential context;
- decisions and rationale;
- actions and outcomes;
- files changed;
- memories or skills created or updated;
- unresolved items and next steps;
- checkpoint timestamps; and
- context-access audit.

Raw dialogue and routine tool output are excluded. Tool output appears only when essential to understanding an outcome, error, or decision.

### 10.4 Model policy

The active session provider/model is used for summarization by default. The provider and model may change between sessions and must be configurable. Every generated checkpoint records the exact summarizer `provider/model-id`.

### 10.5 Knowledge extraction

Automatic reusable-knowledge extraction runs at compaction, reset, or finalization rather than after every turn. Agents may still create memories during a turn when the durable value is already clear. Extracted candidates pass through duplicate detection and normal CLI validation before mutation.

### 10.6 Failure behavior

Summary, extraction, or automatic-promotion failure must not block compaction, reset, `/new`, or exit. Lifecycle adapters synchronously enqueue a sanitized, idempotent event descriptor to durable local state and return within a fixed timeout. A supervised worker performs model and Git work afterward. Failures are reported through the available interface, `system/errors.md`, and the next agent turn when immediate notification is unavailable.

## 11. Agent-specific files

### 11.1 MVP copy boundary

The MVP copies selected agent context files into the vault for visibility without changing their native locations. It does not create symlinks or keep the copies synchronized. Native files remain the runtime source of truth until the user establishes symlinks later.

### 11.2 Pi files

The MVP copies global `AGENTS.md` into `agents/pi/` when it exists. `SYSTEM.md`, `APPEND_SYSTEM.md`, `settings.json`, Pi skills, authentication, trust state, installed packages, caches, and raw sessions remain native and outside MVP management.

### 11.3 Hermes files

The MVP copies:

- `SOUL.md`;
- `memories/USER.md`; and
- `memories/MEMORY.md`.

`config.yaml`, skills, symlinks, and general migration tooling are deferred. `MEMORY.md` remains a compact, native working-context map for recent or active work and projects; it is not the authoritative durable concept store. Later native edits do not automatically update the vault copy or create managed OKF log entries.

Hermes secrets, authentication, logs, caches, locks, raw sessions, `state.db`, database sidecars, and bundled skills remain outside the vault.

### 11.4 Skills

Vault skills are divided into:

- `skills/shared/`
- `skills/pi-only/`
- `skills/hermes-only/`

Shared is preferred. Skills remain native `SKILL.md` packages and are not required to conform to OKF.

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

The OKF bundle uses the reserved root `memory/log.md`, not `CHANGELOG.md`. It records concise, newest-first summaries of CLI-managed concept creations, updates, verifications, promotions, renames, reconciliations, and deletions.

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

The vault and code repository must not contain `.env`, authentication tokens, API keys, bot tokens, OAuth credentials, or other secrets. Agent files selected for the one-time copy are screened before entering the vault. Notifications and logs must not include raw prompts, secret values, or unrestricted tool output.

### 14.5 Policy boundary

Work memory is accessible to Pi and Hermes, including authenticated Telegram use. This is not a hard security boundary because agents with unrestricted shell access can read the vault directly. Hard isolation is deferred.

## 15. Notifications and health

Failures are surfaced through all available paths:

- Pi TUI notifications;
- Hermes notifications in the originating interface, including the authenticated Telegram DM where possible;
- persistent `system/errors.md` entries;
- a warning on the next agent turn when immediate delivery fails; and
- `memory doctor`.

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
8. append checkpoints on Pi compaction, Hermes gateway/Telegram compression, and `/new` or reset, with Hermes CLI compressed intervals captured at reset or finalization;
9. synchronize changes into Obsidian;
10. present expected Bases views;
11. reconcile a direct Obsidian edit;
12. preserve uncommitted user work during a targeted-file conflict;
13. block writes during a simulated Syncthing conflict;
14. recover or retry a simulated summary failure without blocking the agent session; and
15. keep secrets and raw session stores outside the vault.

Focused automated integration tests, rather than the live acceptance exercise, verify Note deletion authorization, verification invalidation, procedure-use recording, and automatic promotion.

Semantic search and adaptive retrieval work must not begin until this workflow is operating and its failures have been observed.

## 17. Deferred roadmap

### 17.1 Retrieval optimization

After the MVP has produced a useful corpus, retrieval misses and irrelevant reads may be evaluated. Possible later work includes metadata ranking, link traversal, BM25, embeddings, reranking, and task-specific retrieval policies.

### 17.2 Adaptive workflows

Later versions may detect recurring tasks, suggest procedure refinement, evaluate retrieved context usefulness, and improve promotion decisions. These features require measured outcomes and a fixed evaluation approach rather than intuition alone.

### 17.3 Additional observability

Potential additions include watcher-based logging of native Hermes memory edits, automated handling of direct Obsidian edits, retrieval-quality dashboards, and per-concept usage analytics.

### 17.4 Stronger security

If Hermes later participates in untrusted channels, work knowledge requires a stronger boundary using separate profiles, Unix permissions, sandboxing, or channel-specific agent processes.

## 18. Hermes CLI compatibility decision

Hermes Agent 0.20.0 emits `session:compress` for gateway sessions, including Telegram, but its public CLI plugin hook set does not expose an equivalent compression event. The approved MVP behavior is therefore:

- full compression checkpoints for Hermes gateway/Telegram sessions;
- reset, `/new`, and finalization checkpoints for Hermes CLI sessions; and
- Hermes CLI compression captured at the next detectable reset or finalization, without a core patch in the MVP.

An invasive Hermes core patch solely for CLI compression is out of scope for the MVP.
