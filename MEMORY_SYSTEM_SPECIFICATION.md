# Shared Memory System Specification

## 1. Purpose

Build an observable, editable, just-in-time memory system shared by Hermes Agent and Pi on a Linux server.

The system should reduce repeated context-setting by preserving durable knowledge about projects, work, people, preferences, procedures, notes, tasks, decisions, and references. It should become more useful over time without hiding what it stores or injects.

The user interacts with the system through Obsidian. Agents interact through a shared command-line interface and normal filesystem tools.

## 2. Product principles

1. **Human-readable:** Memories are concise Markdown files that are easy to inspect and edit in Obsidian.
2. **Observable:** The user can see memories, provenance, retrieval activity, session summaries, changes, and failures.
3. **Agent-readable:** Agents receive a small index and retrieve detailed context only when needed.
4. **Shared by default:** Hermes and Pi use the same concept store.
5. **Attributable:** Every managed memory records its creator, latest editor, agent version, exact provider/model identifier, timestamp, and source when available.
6. **Low-conflict:** Existing concepts are normally updated rather than duplicated or superseded.
7. **Local-first:** The vault is made of normal files, synchronized with Syncthing, and versioned with Git.
8. **Incremental:** Start with deterministic agentic search. Add semantic retrieval only after observing actual retrieval failures.

## 3. Delivery phases

### 3.1 MVP: observable memory

The MVP includes:

- An OKF v0.2 concept bundle.
- Obsidian visibility and filtering.
- A shared Python CLI.
- Agent-driven retrieval.
- Provenance and change logging.
- Session summaries and context-access auditing.
- Pi and Hermes integration adapters.
- Git version history and Syncthing replication.

New or edited memories do **not** require manual approval.

### 3.2 Later: adaptive memory

Defer the following until the MVP is trusted and evaluated:

- Semantic/vector search.
- Retrieval ranking optimization.
- Typed relation metadata.
- Hard filesystem access isolation.
- Automatic logging of native Hermes edits to `MEMORY.md` and `USER.md`.
- Watcher-based detection of direct Obsidian edits.
- Agent-file migration, configuration migration, skill migration, symlink cutover, and rollback tooling.
- Automatic workflow optimization beyond the agreed procedure-to-skill lifecycle.

## 4. Repository and storage boundaries

### 4.1 Code repository

Memory-system source code lives in the current repository:

```text
/home/donald/projects/agent_obsidian_memory
```

### 4.2 Vault repository

The canonical logical vault lives at:

```text
/home/donald/agent-memory
```

The vault is a separate Git repository with a private remote for backup.

### 4.3 Obsidian synchronization

- Obsidian runs on the user's computer against a local vault replica.
- Syncthing synchronizes that replica with `/home/donald/agent-memory` on the server.
- This replaces SSHFS as the normal Obsidian access method so local file watching and hot reload are reliable.
- “Hot reload” means new and changed files become visible in Obsidian promptly. It does not require an already-running agent conversation to reload every context file immediately.
- Agents access the latest server-side state through normal filesystem operations.
- Machine-specific Obsidian state such as `.obsidian/workspace*.json` should not be synchronized.
- `.git/` should not be synchronized by Syncthing.
- Syncthing is not considered a backup; the private Git remote provides backup history.

## 5. Vault structure

The Obsidian vault contains both an OKF bundle and non-OKF agent files:

```text
agent-memory/
├── memory/
│   ├── index.md
│   ├── log.md
│   ├── memories.base
│   └── concepts/
│       ├── index.md
│       └── *.md
├── agents/
│   ├── pi/
│   └── hermes/
├── skills/
│   ├── shared/
│   ├── pi-only/
│   └── hermes-only/
├── sessions/
│   ├── pi/
│   └── hermes/
└── system/
    ├── memory.yaml
    ├── status.md
    └── errors.md
```

Only `memory/` is an OKF bundle. Agent files, `SKILL.md` packages, session summaries, configuration, and status files are outside that conformance boundary.

OKF concepts may link to non-OKF files using relative Markdown links. Non-OKF files do not need to link back.

## 6. Knowledge format

### 6.1 Standard

The concept bundle follows Google Cloud's **Open Knowledge Format v0.2**.

Required OKF conventions include:

- Markdown concept files with YAML frontmatter.
- A non-empty `type` field.
- Lowercase reserved files `index.md` and `log.md`.
- Standard Markdown links for relationships.
- OKF provenance, trust, freshness, and lifecycle fields where applicable.

### 6.2 Concept granularity

- Each file represents one self-contained concept.
- Each managed concept has a non-empty `title`, a concise `description`, and a non-empty Markdown body.
- An H1 corresponding to the title is recommended but not required.
- The default maximum is **600 words per concept**.
- The CLI rejects concepts over 600 words by default.
- An explicit `--allow-long` override may be used for exceptional references.
- Concept filenames use stable, readable `kebab-case` slugs.
- The relative path is the OKF concept ID.
- Renames must use the CLI so links and indexes can be updated safely.

### 6.3 Flat concept model

Concepts live together in `memory/concepts/`. Category directories are not used.

Organization is provided by orthogonal metadata and Obsidian Bases views.

Each concept has exactly one scope:

- `work`: Info-Tech knowledge.
- `personal`: personal work, including personal coding projects.
- `global`: knowledge that applies across work and personal contexts.

The initial controlled type vocabulary is:

- `Project`
- `Person`
- `Preference`
- `Procedure`
- `Note`
- `Task`
- `Decision`
- `Reference`

The vocabulary can be extended later without reorganizing the filesystem.

Examples:

```yaml
type: Procedure
scope: work
```

```yaml
type: Project
scope: personal
```

```yaml
type: Preference
scope: global
```

### 6.4 Meaning of key types

- **Project:** Personal coding projects or other personal projects. Info-Tech initiatives use `scope: work` with the most appropriate type.
- **Person:** Durable knowledge about a person.
- **Preference:** User preferences, communication expectations, and taste.
- **Procedure:** A reusable workflow that is not yet a mature executable skill.
- **Note:** Lists, thoughts, and informal captures, including items submitted to Hermes through Telegram. Every Note records `content_owner: user` or `content_owner: agent` so deletion policy does not depend on which process created the file.
- **Task:** Daily or scheduled task information. The memory system stores task state only; reminder execution is coordinated separately by the agents.
- **Decision:** A decision and its relevant rationale.
- **Reference:** Durable reference knowledge that does not fit another type.

### 6.5 Relations

The MVP uses standard OKF/Markdown links only.

- A link asserts a relation.
- Surrounding prose explains the relation.
- Obsidian graph and backlinks expose these links.
- Custom typed relation metadata such as `depends_on` or `owned_by` is deferred.

## 7. Metadata and attribution

### 7.1 Creator and latest editor

Memories are shared by default, but their origin must be filterable.

Preserve both original creation and latest meaningful update:

```yaml
created:
  by: pi/0.84.2
  at: 2026-06-01T12:00:00Z
  model: anthropic/exact-model-id
generated:
  by: hermes-agent/0.20.0
  at: 2026-06-03T09:30:00Z
  model: openrouter/exact-model-id
```

Rules:

- Record the exact runtime `provider/model-id`, not a friendly alias.
- Preserve `created` when another agent updates the concept.
- Update `generated` on the latest meaningful change.
- Human edits use `human:donald` and omit model metadata.
- Automated maintenance uses an appropriate `process:<id>` actor.
- Git and `log.md` record every CLI-managed editor and change summary.

### 7.2 Provenance

Every managed change should link to its source when available, including:

- Session ID or session summary.
- Telegram message or other user instruction.
- File path.
- URL.
- Related OKF concept.

Use OKF `sources` and claim-level citations where appropriate.

### 7.3 Trust and verification

- A direct user statement is provenance, not automatically human review of the final stored concept.
- A concept becomes human-reviewed only after explicit confirmation.
- Confirmation is performed with:

```bash
memory verify <concept-id>
```

- The user may instead tell an agent to mark a reviewed concept verified.
- A direct human invocation uses the simple command above and confirms the action interactively.
- An agent may use `human:donald` verification only after explicit user instruction and must supply the authorizing session or message as provenance.
- Machine checks use an agent or process actor.
- A substantial change to a concept's title, description, type, scope, body, or sources clears active verification. Path-only renames, verification-only updates, and non-semantic formatting changes do not.
- Historical verification remains recoverable from `log.md` and Git.
- Unverified and machine-confirmed concepts remain usable.

### 7.4 Freshness

`stale_after` is optional.

Use it only when a concept has a meaningful expiry or review date, such as a time-sensitive work process, active project state, or scheduled task. Do not assign arbitrary expiry dates to durable preferences or person facts.

### 7.5 Lifecycle and removal

- Existing concepts are normally updated in place.
- Superseding concepts is rare because conflicting active knowledge confuses agents.
- Incorrect or obsolete knowledge is deleted from the active bundle and remains recoverable from Git history.
- Use `deprecated` only when preserving historical context is genuinely useful.

## 8. Indexes, views, and progressive disclosure

### 8.1 Root index

`memory/index.md` is compact and automatically injected into new Pi and Hermes sessions.

It describes:

- The vault layout.
- The type and scope ontology.
- Retrieval instructions.
- Links to deeper indexes and views.

It does not inject all memories.

Existing auto-injected agent files such as `USER.md` are not duplicated.

### 8.2 Concept index

`memory/concepts/index.md` is generated and groups concepts for progressive disclosure, using type and scope rather than category directories.

### 8.3 Obsidian Bases

`memory/memories.base` provides filterable, sortable, editable views for:

- Work
- Projects
- People
- Preferences
- Procedures
- Notes
- Tasks
- Decisions
- References

Views should expose useful metadata such as scope, type, creator, latest editor, model, status, verification, and freshness.

## 9. Retrieval behavior

### 9.1 MVP retrieval

Retrieval is deterministic and agentic:

- Filename and slug search.
- OKF metadata filtering.
- Tags.
- Standard links.
- Full-text search.
- Generated indexes.

Search uses one fixed ordering: exact concept ID or slug, exact title, partial title, tags and description, body text, then a stable alphabetical tie-break. The CLI explains matched fields, but ranking is not configurable in the MVP.

Do not add embeddings or a vector database in the MVP.

### 9.2 Retrieval initiation

- A small root index is injected into each new session.
- The agent performs additional retrieval only when needed.
- Agents should use the `memory` CLI rather than arbitrary filesystem reads for memory retrieval so access can be audited.

### 9.3 Context-access audit

Each session summary includes a **Context Access** section recording:

- The automatically injected index.
- Retrieval timestamp.
- Query or reason.
- Concepts opened.
- Agent and exact model.

`memory search` and `memory show` append access events immediately. These events are committed with the next session checkpoint rather than creating a Git commit for every read.

Direct filesystem reads cannot be guaranteed to appear in this audit and should be discouraged in agent instructions.

## 10. Shared CLI

### 10.1 Implementation

- Language: Python.
- Dependency and environment management: `uv`.
- Installed command: `memory`.
- The server currently has no conflicting `memory` executable in `PATH`.
- Configuration: vault-managed `system/memory.yaml`.
- Secrets are referenced through environment variables and never stored in the vault.

### 10.2 Expected capabilities

The CLI should support at least:

- Search and filtering.
- Showing a concept with access auditing.
- Creating one or more related concepts in a transaction.
- Updating one or more related concepts in a transaction.
- Deleting concepts.
- Renaming concepts and updating links.
- Validation of OKF, metadata, links, word limits, and indexes.
- Verification.
- Reconciliation of direct Obsidian edits.
- Session checkpoint creation.
- Retrying failed queued work through `memory retry`.
- Previewing and applying interrupted-transaction recovery through `memory recover`.
- Health checks through `memory doctor`.

Exact command syntax beyond agreed examples may be finalized during implementation.

### 10.3 Proactive memory creation

Agents may proactively create and update memories without approval.

Automatic reusable-knowledge extraction runs at compaction, reset, and session end. Agents may still create or update a concept during a turn when clearly useful.

### 10.4 Duplicate prevention

Before creating a concept:

- Search for an existing matching concept.
- Refuse exact slug duplicates.
- Warn about similar titles and descriptions.
- Update the existing concept when it represents the same knowledge.
- Use a distinct name only when the concept is genuinely new.

## 11. Transactions, locking, and Git

### 11.1 Atomic transactions

One CLI transaction may update several related concepts.

A successful write transaction may include:

- Concept changes.
- Link updates.
- Index regeneration.
- A `log.md` entry.
- One Git commit.

Use one short-lived global vault write lock. Concurrent reads remain allowed, but all write transactions are serialized. Lock waits time out and fail clearly rather than waiting indefinitely.

Transaction journals, candidate files, and backups live outside the vault under `/home/donald/.agent-memory-txn/`. The CLI verifies that this sibling directory and the vault are on the same filesystem before writing.

### 11.2 Git commits

- Create one Git commit per successful CLI transaction.
- A transaction may contain multiple related updates.
- Never stage or commit unrelated user changes.
- Any pre-existing staged Git path blocks managed writes and is reported clearly.
- Git is the authoritative full diff/history layer.
- `memory/log.md` is the concise human-readable history for CLI-managed memory changes.
- One root `log.md` is sufficient; category-level logs are not needed.
- Local commits are the MVP durability boundary. The user configures and pushes to the private remote manually; remote state never blocks local memory writes.

### 11.3 Dirty-file protection

If a transaction targets a file with uncommitted direct edits:

- Abort the transaction.
- Report the conflict clearly.
- Do not overwrite or commit the user's work.
- Leave unrelated dirty files untouched.

### 11.4 Syncthing conflict protection

If a Syncthing conflict copy exists anywhere in the vault:

- Block further CLI writes.
- Notify the user.
- Continue allowing reads and inspection.
- Resume writes only after conflict resolution.

The MVP does not pause Syncthing or depend on its REST API. The CLI rechecks target hashes immediately before replacement and aborts when it detects a concurrent change. A narrow undetectable race remains possible and is documented; Syncthing availability does not control whether memory writes are allowed.

## 12. Direct Obsidian editing

Direct Obsidian edits are allowed and are a core observability/correction workflow.

The MVP does not continuously watch and summarize direct edits.

Use:

```bash
memory reconcile <concept-id> --summary "Description of correction"
```

Reconciliation should:

- Validate the edited concept.
- Preserve original creation metadata.
- Attribute the edit to `human:donald`.
- Update latest-generation metadata.
- Regenerate affected indexes.
- Append `log.md`.
- Create a Git commit.

Until reconciled, a direct edit remains uncommitted and blocks CLI transactions targeting that file.

## 13. Tasks, notes, and procedures

### 13.1 Tasks

- Tasks store daily or scheduled task information only.
- The memory system does not execute reminders or schedules.
- Completed tasks are deleted from active memory.
- Before deletion, create or refine a related procedure when the task produced reusable workflow knowledge.

### 13.2 Notes

- Notes contain lists, thoughts, and informal capture.
- Every Note has `content_owner: user` or `content_owner: agent`.
- User-owned notes require explicit user instruction before deletion, even when an agent created the Markdown file from a user message.
- Agents may proactively delete obsolete agent-owned notes.
- CLI-managed deletion remains visible in `log.md` and Git.

### 13.3 Procedure-to-skill lifecycle

Reusable procedures begin as OKF `Procedure` concepts.

A procedure is promoted automatically to a native `SKILL.md` package when it has:

1. At least three successful uses.
2. A stable sequence of steps.
3. A clear verification method.
4. Known target-agent compatibility.

Each use is recorded in a minimal event list with timestamp, outcome, and source checkpoint. The successful-use count is derived from these events; actor, model, and detailed context remain available through the checkpoint.

The promoted skill links back to its source procedure. The source concept remains `type: Procedure`, retains its use history and provenance, and is shortened after promotion so it does not duplicate the executable steps in `SKILL.md`.

Skills are organized as:

```text
skills/shared/
skills/pi-only/
skills/hermes-only/
```

Use `shared/` whenever possible. Use agent-specific directories only when a skill depends on agent-specific tools or metadata.

Skill migration is deferred beyond the MVP. When it is implemented later, import Pi user skills and only user-created or user-modified Hermes skills; untouched bundled and upstream skills should remain in their native package-managed locations.

## 14. Agent-specific files

### 14.1 MVP snapshot boundary

Agent-file migration is deferred beyond the MVP. The MVP only copies selected non-secret context files into the vault as visibility snapshots. It does not change native load paths, create symlinks, migrate configuration or skills, perform a cutover, or provide migration and rollback tooling.

Native files remain the runtime source of truth, and the vault snapshots may diverge until the user establishes symlinks later. The snapshot copy must not be described or treated as migration.

### 14.2 Pi files

The MVP copies global `AGENTS.md` into `agents/pi/` when it exists. `SYSTEM.md`, `APPEND_SYSTEM.md`, `settings.json`, Pi skills, installed package code, authentication, trust state, caches, and raw JSONL sessions remain native and outside MVP management.

### 14.3 Hermes files

The MVP copies these files without changing their native versions:

- `SOUL.md`;
- `memories/USER.md`; and
- `memories/MEMORY.md`.

Hermes `config.yaml`, skills, load-path changes, and symlinks are deferred. Hermes's native `MEMORY.md` writing remains enabled at its native path.

- `USER.md` is compact, durable profile context.
- `MEMORY.md` is a compact working-context map for recent or active projects and work.
- `MEMORY.md` should link toward authoritative OKF concepts and should not become a second long-term knowledge store.
- Later native edits do not automatically update the vault snapshots or create `memory/log.md` entries.

### 14.4 Exclusions

Do not place the following in the vault:

- API keys, bot tokens, passwords, or secret files.
- Pi `auth.json` or secret directories.
- Hermes `.env` or `auth.json`.
- Caches, logs, lock files, package installations, and generated runtime state.
- Hermes `state.db` or SQLite WAL files.
- Raw Hermes session stores.
- Installed third-party extension/plugin source.

User-created Pi extensions/prompts and Hermes hooks/plugins are not vault-managed in the MVP. Durable facts about their installation or behavior may be captured as OKF memories.

## 15. Session summaries

### 15.1 Storage

Only Markdown session summaries are stored in the vault.

- Raw Pi sessions remain in native JSONL storage.
- Hermes's canonical session database remains outside the vault.
- Do not live-sync Hermes `state.db`.

### 15.2 One evolving file per session

Each session has one evolving Markdown file.

- Every compaction or reset adds an indexed checkpoint section.
- Multiple compactions/resets in one session remain distinguishable by stable session ID and checkpoint sequence.
- `/new` creates the final checkpoint for the outgoing session.
- Session end should also finalize the summary when possible.
- Completed checkpoint text may be edited, but checkpoint anchors should remain stable. Git preserves prior versions.

### 15.3 Summary contents

Keep summaries concise and include:

- Objective.
- Essential context.
- Key decisions and rationale.
- Actions and results.
- Important files changed.
- Memories or skills created/updated.
- Unresolved items and next steps.
- Checkpoint timestamps.
- Context-access audit.

Exclude:

- Raw dialogue.
- Routine tool output.
- Nonessential command details.
- Large logs or pasted content.

### 15.4 Reusable knowledge extraction

At checkpoint/finalization time, reusable knowledge should be saved into or merged with an OKF concept rather than being buried only in the session summary.

Procedural knowledge remains an OKF `Procedure` until it meets the agreed skill-promotion threshold.

### 15.5 Summarizer model

- Use the session's active provider/model by default.
- Record the exact summarizer provider/model in the session file.
- Make provider and model configurable in `system/memory.yaml` because they will change over time.
- Future configuration may select a cheaper dedicated summarizer.

## 16. Pi and Hermes adapters

Automatic checkpointing requires thin native integrations:

- A Pi extension listening for compaction and session lifecycle events.
- Hermes hooks/plugins listening for compression, reset, and session lifecycle events.
- Both call the shared Python CLI.

For Hermes gateway and Telegram sessions, compression creates an immediate checkpoint. Hermes CLI 0.20.0 does not expose a public compression event, so CLI sessions checkpoint on reset, `/new`, and finalization; any intermediate compressed interval is incorporated at the next detectable checkpoint. A Hermes core patch is out of scope for the MVP.

Adapter source belongs in the memory-system code repository, not the Obsidian vault.

The adapters may be installed into the agents' native extension/hook locations even though general extensions/hooks are not vault-managed.

## 17. Error handling and notifications

Memory-system failures must not trap or block normal agent session lifecycle.

If summarization, extraction, or automatic promotion fails:

- Allow compaction, reset, `/new`, or session end to continue.
- Record a visible failure.
- Queue the operation for retry through the durable worker.
- Permit explicit retry with `memory retry` after automatic retries are exhausted.

Enable these notification paths by default:

- Immediate Pi TUI notification.
- Hermes notification in the originating interface.
- Telegram notification only to the authenticated user DM.
- Persistent `system/errors.md` entry visible in Obsidian.
- Warning on the next agent turn if immediate delivery fails.
- `memory doctor` health reporting.

Error records include timestamp, agent, session ID, operation, and retry status. They must not include raw prompts, secrets, or unnecessary sensitive content.

## 18. Access policy

- Work memories must be available remotely through Hermes on Telegram.
- Telegram access is intended for the user's authenticated direct messages.
- Hermes does not participate in untrusted groups.
- Policy-level access control is sufficient for the MVP.
- Hard filesystem isolation is deferred.
- This is not a security boundary against an agent with unrestricted shell access.

## 19. Migration deferred

All agent-file, configuration, and skill migration is deferred beyond the MVP. The MVP does not make vault copies canonical, switch native load paths, create symlinks, or implement inventory, cutover, divergence, or rollback workflows.

The only MVP action is the one-time snapshot copy described in Section 14: Pi `AGENTS.md` and Hermes `SOUL.md`, `memories/USER.md`, and `memories/MEMORY.md`. Native files remain unchanged and authoritative. The user will establish symlinks later outside this MVP.

When migration is designed later, it must be non-destructive and explicitly address backups, secret auditing, collisions, validation, divergence, and rollback. Do not migrate old Honcho or Open Second Brain memories automatically. Hermes skill migration must include only user-created or user-modified skills, not the complete bundled catalog.

## 20. MVP acceptance criteria

The MVP is accepted only after an end-to-end demonstration that both Pi and Hermes can:

1. Load the root index.
2. Search, create, and update the same shared concept.
3. Record creator, latest editor, exact model, provenance, and context access.
4. Update `index.md` and `log.md`.
5. Commit a multi-file transaction atomically to Git.
6. Create checkpoints on Pi compaction, Hermes gateway/Telegram compression, and `/new` or reset; capture Hermes CLI compressed intervals at the next reset or finalization.
7. Synchronize changes visibly into the local Obsidian vault.
8. Filter concepts through Obsidian Bases.
9. Reconcile a direct Obsidian edit.
10. Preserve user work during simulated dirty-file and Syncthing conflicts.
11. Report and retry a simulated summary failure without blocking the agent session.
12. Keep secrets and raw Hermes session databases outside the vault.

Semantic search, typed relations, and adaptive optimization should not begin until this workflow behaves reliably and matches the user's expectations.
