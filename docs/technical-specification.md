# Technical Specification

## 1. Scope and conventions

This derived document describes the MVP architecture and contracts for Agent Obsidian Memory. [product-specification.md](product-specification.md) is the sole normative source and wins on conflict. The implementation is a Python application installed with `uv`, plus thin Pi and Hermes adapters.

Paths shown here are deployment defaults and MUST be configurable unless identified as native agent paths.

## 2. System architecture

### 2.1 Components

| Component | Responsibility |
|---|---|
| `memory` CLI | Validate, search, retrieve, mutate, reconcile, index, log, commit, and inspect the vault |
| Python core library | Shared domain model, OKF parser, transaction engine, Git integration, session writer, and policy enforcement |
| `memory worker --once` | Under one worker lock, recover claimed descriptors first, then atomically claim and process ready descriptors one at a time until both queues are empty; materialize native summaries and audit state idempotently, run bounded in-invocation retries, move exhausted work to failed state, notify, and exit |
| systemd user `.path` + oneshot service | Use `DirectoryNotEmpty=` for both ready and claimed queues to activate `memory worker --once` for normal, crash-recovery, backlog, and boot/login processing; not an application daemon |
| Pi adapter | Inject root index, expose session/model context, publish compaction and lifecycle descriptors, and notify immediate errors |
| Hermes adapter | Inject root index, expose session/model/channel context, publish compression/reset/finalization descriptors, and notify errors |
| Obsidian vault | Canonical human-readable concepts, native-summary checkpoints, and system status |
| Syncthing | Bidirectional replication between server vault and local Obsidian vault |
| Git | Version history, attribution, recovery, and private-remote backup |
| Obsidian Bases | Human filtering, grouping, and editing of Markdown properties |

### 2.2 Data flow

#### Retrieval

1. The adapter supplies `memory/index.md` when a new session begins.
2. The agent runs `memory search` with a query or metadata filters.
3. The CLI returns compact, explainable matches and records the search in the active session audit.
4. The agent runs `memory show <concept-id>` for selected concepts.
5. The CLI returns the complete concept and records that it was opened.
6. The agent uses the retrieved context in the task.

#### Mutation

1. The agent searches before creating or updating a concept.
2. The agent submits one operation or a batch transaction to the CLI.
3. The CLI acquires the writer lock and performs all safety preflights.
4. Candidate files are rendered in a transaction workspace and validated.
5. Concept files are replaced atomically, affected indexes are updated, and `memory/log.md` is prepended.
6. Only transaction-owned paths are staged and committed.
7. The local commit succeeds before the transaction reports success.
8. Syncthing propagates the committed files to Obsidian.
9. The user pushes local commits to the private remote manually.

#### Session checkpoint

1. A native compaction, compression, reset, `/new`, or finalization event fires and its handler runs.
2. A Pi adapter captures the exposed stable compaction entry ID and `compactionEntry.summary`. After a committed Hermes gateway compression, the handler uses persisted adapter state to bind the exposed lineage to previous/current message-row high-water boundaries and, when unambiguous, an isolated native-summary candidate row ID/hash; it does not receive a summary, model, timestamp, or native event ID from the hook payload.
3. The adapter atomically publishes a sanitized, idempotent descriptor to a durable non-hidden ready directory outside the synchronized vault and returns within a fixed timeout after persisting the new Hermes boundary/lineage. Abrupt-exit recovery begins only after publication completes.
4. The systemd user `.path` unit activates a `Type=oneshot` service running `memory worker --once` when either ready or claimed state is non-empty.
5. Under one worker lock, the command recovers claimed descriptors first, then claims and processes ready descriptors one at a time until both are empty. For Hermes gateway compression it fetches the exact descriptor-bound candidate row, re-isolates and verifies its summary segment, and stores only that segment; otherwise it consumes the Pi descriptor summary. It appends checkpoints idempotently without another model pass.
6. Reset, `/new`, and finalization materialize pending context-access audit and lifecycle state. An unavailable summary produces lifecycle metadata and `native summary unavailable`, never raw dialogue/tool output.
7. Retryable failures receive bounded capped backoff in the same invocation. Exhausted descriptors move to unwatched failed state for explicit `memory retry`; no delayed work remains in ready and no timer is used. Processing failure never blocks the native lifecycle.

## 3. Repository and vault boundaries

### 3.1 Code repository

```text
/home/donald/projects/agent_obsidian_memory/
├── README.md
├── docs/
├── pyproject.toml
├── src/agent_memory/
├── adapters/
│   ├── pi/
│   └── hermes/
├── deploy/systemd/
│   ├── agent-memory-lifecycle.path
│   └── agent-memory-lifecycle.service
├── tests/
└── scripts/
```

This repository contains no user memory corpus, raw sessions, or credentials.

### 3.2 Runtime vault

```text
/home/donald/agent-memory/
├── .git/
├── .gitignore
├── README.md
├── memory/
│   ├── index.md
│   ├── log.md
│   ├── memories.base
│   └── concepts/
│       ├── index.md
│       └── <concept-id>.md
├── sessions/
│   ├── pi/<year>/<session-id>.md
│   └── hermes/<year>/<session-id>.md
└── system/
    ├── memory.yaml
    ├── errors.md
    └── status.md
```

`memory/` alone is an OKF bundle. Markdown elsewhere in the vault follows its native format and is outside OKF conformance checks.

Transaction journals, rendered candidates, and backups live outside the vault at `/home/donald/.agent-memory-txn/`. The CLI verifies that this sibling directory is on the same filesystem as the vault before a write. Durable lifecycle descriptors and audit spools live in user state outside the vault. The MVP creates no `agents/` or `skills/` vault directories and does not copy or alter native agent files.

### 3.3 Exclusions

The vault `.gitignore` MUST exclude at least:

```gitignore
# Obsidian machine-local state
.obsidian/workspace*.json
.obsidian/cache/

# Syncthing and conflict artifacts are never committed
.stfolder/
.stignore
*.sync-conflict-*

# Runtime/transient state
system/.state/
*.lock
*.tmp
*.swp

# Secrets and native stores
.env
**/.env
**/auth.json
**/*token*
**/*secret*
**/state.db*
```

Secret-like filename exclusions are defense in depth, not a substitute for content auditing.

## 4. OKF bundle contract

### 4.1 Conformance boundary

Within `memory/`:

- `index.md` and `log.md` are reserved OKF files;
- `concepts/index.md` is a reserved nested index;
- every other Markdown file MUST contain parseable YAML frontmatter and non-empty `type`;
- unknown frontmatter keys MUST be preserved on round-trip;
- broken links are reported but do not make the bundle non-conformant; and
- `memory validate` reports both strict local policy failures and softer OKF warnings.

### 4.2 Root index

`memory/index.md` is the only index allowed to contain frontmatter:

```markdown
---
okf_version: "0.2"
---

# Agent Memory

Short explanation of the corpus, approved types and scopes, retrieval commands,
and links to the concept index, log, Obsidian Base, and sessions.
```

The root index is compact enough for automatic new-session injection. It MUST describe structure and retrieval behavior rather than enumerate every concept.

### 4.3 Concept index

`memory/concepts/index.md` contains no frontmatter. It is generated from committed concept metadata and uses relative links. Entries are grouped first by scope and then type, or exposed through equivalent concise sections. Each entry includes title and description.

The index generator MUST be deterministic: the same concept set produces byte-identical output. Sorting is case-insensitive by title, with concept ID as the tie-breaker.

### 4.4 Managed log

`memory/log.md` follows OKF v0.2: ISO date headings, newest date first, and newest entry first within a date. A managed entry has the following prose convention:

```markdown
## 2026-06-03
* **Update**: [Agent Memory System](concepts/agent-memory-system.md) — corrected the canonical vault path. Actor `pi/0.84.2`, model `openai-codex/gpt-5.4`, session `…`.
```

Allowed leading labels include `Creation`, `Update`, `Verification`, `Rename`, `Reconciliation`, and `Deletion`. A deletion links only when a stable replacement exists; otherwise it records the former concept ID as code.

The log is an operational summary. Git remains the complete diff and attribution record.

## 5. Concept schema

### 5.1 Canonical frontmatter

```yaml
---
type: Project
title: Agent Obsidian Memory
description: Local-first shared memory for Pi and Hermes.
scope: personal
tags: [agents, memory, obsidian]
status: stable
created:
  by: pi/0.84.2
  at: 2026-06-03T12:00:00Z
  model: openai-codex/gpt-5.4
generated:
  by: hermes-agent/0.20.0
  at: 2026-06-04T09:30:00Z
  model: openrouter/anthropic/claude-sonnet-4
verified:
  - by: human:donald
    at: 2026-06-04T10:00:00Z
sources:
  - id: session-pi-019-checkpoint-2
    resource: ../../sessions/pi/2026/019....md#checkpoint-2--compaction
    checkpoint_id: pi:019...:compact:2
    title: Pi session checkpoint
    author: pi/0.84.2
    last_modified: 2026-06-03
stale_after: 2026-12-31
---
```

### 5.2 Required local fields

The local profile requires more than base OKF:

| Field | Requirement |
|---|---|
| `type` | Required by OKF. Managed creation accepts only the configured vocabulary; imported unknown types remain OKF-valid but receive a local warning |
| `title` | Required; non-empty human title |
| `description` | Required; one concise sentence |
| `scope` | Required; exactly one of `work`, `personal`, `global` |
| `created.by` | Required; immutable original actor |
| `created.at` | Required; immutable ISO 8601 UTC timestamp |
| `generated.by` | Required; actor responsible for current meaningful content |
| `generated.at` | Required; ISO 8601 UTC timestamp of current meaningful content |

`created.model` and `generated.model` are required when the corresponding actor is an agent. They are omitted for `human:*` and deterministic `process:*` actors.

### 5.3 Optional fields

- `tags`: lowercase strings, used sparingly for cross-cutting retrieval;
- `status`: `draft`, `stable`, or `deprecated`; absent is interpreted as `stable` by OKF;
- `verified`: one mapping or a list of verification events;
- `sources`: OKF provenance entries;
- `stale_after`: ISO date with a meaningful review policy;
- `resource`: canonical external URI where a concept describes another asset;
- `content_owner`: required for `Note`, with value `user` or `agent`; and
- future producer extensions, which must be preserved.

### 5.4 Actor identities

Configured actor examples are:

- `pi/<runtime-version>`
- `hermes-agent/<runtime-version>`
- `human:donald`
- `process:memory-cli`

The CLI MUST reject `human:donald` verification unless the command supplies an explicit authorization source from the user's instruction or is confirmed in an interactive human invocation. Merely running an agent-authored mutation on the user's server does not make it human-reviewed.

Trust tiers are derived exactly as OKF v0.2 specifies: no `verified` field is `unverified`; only non-human verifiers is `machine-confirmed`; at least one current `human:*` verifier is `human-reviewed`. A meaningful change to title, description, type, scope, body, resource, or sources clears `verified`. A path-only rename, verification event, or non-semantic formatting change does not change `generated.at` or invalidate verification.

### 5.5 Model identity

Agent model identity is stored exactly as the runtime reports it, in `provider/model-id` form. Friendly aliases are not authoritative. If a provider reports a nested or routed model identifier, the full reported identifier is retained.

### 5.6 Body contract

The Markdown body:

- MUST be non-empty;
- SHOULD begin with an H1 matching or clearly corresponding to `title`, but validation does not require it;
- MUST contain no more than 600 words under the default policy;
- SHOULD use headings, lists, and tables instead of undifferentiated prose;
- SHOULD state uncertainty rather than convert inference into fact; and
- SHOULD place relationship links in explanatory prose.

Word counting excludes YAML frontmatter and fenced code-block delimiters, but includes natural-language text within code fences. An explicit `--allow-long` override is limited to justified `Reference` concepts and is recorded in the log.

### 5.7 Suggested body patterns

These headings are guidance, not OKF requirements:

| Type | Suggested sections |
|---|---|
| `Project` | Purpose, Current State, Key Context, Related Concepts |
| `Person` | Context, Relevant Facts, Working Preferences |
| `Preference` | Preference, Application, Exceptions |
| `Procedure` | When to Use, Steps, Verification, Usage Notes |
| `Note` | Content, Related Concepts |
| `Task` | Action, Timing, Context, Completion Condition |
| `Decision` | Decision, Rationale, Consequences |
| `Reference` | Key Information, Source, Usage |

### 5.8 Concept IDs and filenames

A concept ID is its bundle-relative path without `.md`, for example `concepts/agent-memory-system`. User-facing CLI commands accept this full ID and may accept an unambiguous slug shorthand.

Filenames MUST:

- use lowercase ASCII letters, digits, and single hyphens;
- not begin or end with a hyphen;
- not contain consecutive hyphens; and
- remain stable after creation unless a rename materially improves correctness.

`memory rename` updates all Markdown links inside the vault, affected indexes, the log, and Git in one transaction. Direct filesystem renames are reconciled only when the old and new paths can be identified unambiguously.

### 5.9 Sources

When available, `sources` links to:

- the relevant session summary;
- a user instruction or message represented by that session;
- a source file or document;
- an external URL; or
- another OKF concept.

Bundle-relative paths are preferred inside the bundle. Relative paths may traverse to non-OKF vault content. Absolute server paths may be recorded as text or file resources when they are useful to agents, but the concept should acknowledge that they are not portable to the local Obsidian computer.

## 6. Procedure scope

A Procedure uses the ordinary concept schema and transaction path. The MVP has no procedure-use event schema, counters, eligibility state, promotion commands, generated `SKILL.md`, skill directories, or load-path integration. The complete procedure-to-skill pipeline is deferred.

## 7. Session summary schema

Session summaries are not OKF concepts. They use YAML frontmatter for machine navigation and a structured Markdown body.

### 7.1 Frontmatter

```yaml
---
agent: pi
agent_version: 0.84.2
session_id: 019...
native_store_ref: pi-session:019...
started_at: 2026-06-03T10:00:00Z
updated_at: 2026-06-03T14:00:00Z
status: active
checkpoint_count: 2
host_models:
  - openai-codex/gpt-5.4
summary_policy: native-only
---
```

`native_store_ref` is an opaque identifier for server-side traceability and may be absent when the native store cannot expose a stable ID. Absolute raw-session paths remain in non-synchronized diagnostic state only. Raw session data is never copied into the vault.

### 7.2 Body

```markdown
# Session title

## Context Access

| Time | Mode | Query or reason | Concepts | Model |
|---|---|---|---|---|
| ... | injected | new session | `memory/index.md` | ... |
| ... | search | deployment workflow | ... | ... |
| ... | show | relevant procedure | ... | ... |

## Checkpoint Index

1. [Compaction 1](#checkpoint-1--compaction)
2. [Finalization](#checkpoint-2--finalization)

## Checkpoint 1 — Compaction

- **Time:** ...
- **Event ID:** ...
- **Native summary source:** pi compaction
- **Host model:** ...

### Native Summary

The summary exposed by the host.

## Checkpoint 2 — Finalization

### Native Summary

native summary unavailable
...
```

### 7.3 Checkpoint semantics

- A Pi checkpoint stores the native `compactionEntry.summary` and stable compaction entry ID exposed by `session_compact`, plus available host provenance.
- A Hermes gateway descriptor binds the committed event to persisted previous/current message-row high-water boundaries and, when unambiguous, one classified native-summary row ID plus SHA-256 of its isolated segment. A checkpoint stores only the segment that the worker re-isolates from that exact bounded row after verifying the ID/hash.
- Hermes 0.20.0 isolation accepts recognized standalone or merged summary carriers only when its summary prefix, optional merged delimiter, and end marker identify exactly one summary body. The isolated body is after the prefix and before the matching end marker; SHA-256 hashes its UTF-8 bytes. A merged delimiter must uniquely separate preserved tail/live user content, all of which remains outside the isolated body. Ambiguous structure, multiple carriers, or hash mismatch yields `native summary unavailable`.
- Candidate classification may inspect only needed row metadata/content. The publisher and worker never serialize archived/raw conversation rows, preserved tails, or the conversation history passed to `pre_llm_call`. No active, configured, or dedicated summarizer model is called.
- If the single classified native summary cannot be bound and verified, the checkpoint stores available lifecycle metadata and `native summary unavailable`; it never copies raw dialogue/tool output or synthesizes a replacement.
- The checkpoint index is regenerated deterministically.
- Pi's stable compaction entry ID or Hermes's deterministic descriptor event identity provides idempotency.
- A retried event with the same ID replaces a failed placeholder or becomes a no-op; it never appends a duplicate. A replay after commit but before descriptor deletion is therefore safe.
- Context-access events are appended immediately to a per-session JSONL spool under the durable local state directory, using an atomic locked append. They are materialized at the next checkpoint and always on reset, `/new`, or finalization.
- The audit spool is outside Syncthing and Git, contains no concept body, and survives agent-process exit.
- Finalization sets `status: closed`. A failed or abruptly lost session may remain `active` until `memory session recover` marks it `incomplete` or finalizes it.
- Completed checkpoint text may be edited. Anchors SHOULD remain stable, and Git preserves prior versions.
- Automatic reusable-knowledge extraction is absent. Agents may explicitly create/update durable concepts during ordinary turns.

## 8. Obsidian Bases contract

`memory/memories.base` is generated or installed once and then versioned. It reads Markdown properties from `memory/concepts/` and provides named views.

Minimum filters are:

| View | Filter |
|---|---|
| Work | `scope == "work"` |
| Projects | `type == "Project"` |
| People | `type == "Person"` |
| Preferences | `type == "Preference"` |
| Procedures | `type == "Procedure"` |
| Notes | `type == "Note"` |
| Tasks | `type == "Task"` |
| Decisions | `type == "Decision"` |
| References | `type == "Reference"` |

Table columns SHOULD include title, type, scope, description, status, generated actor, generated model, generated time, verification, and staleness. Freshness and verification may be filtered manually but do not create additional required named views in the MVP. Work remains a scope view rather than a duplicate type or directory.

## 9. CLI contract

### 9.1 Command name and installation

The executable is `memory`. No existing command currently occupies that name on the target server. The package is installed with `uv tool install` or an equivalent reproducible `uv` workflow and exposes a Python entry point.

Commands support `--json` for adapters and automation. Human-readable output remains the default.

### 9.2 Environment-derived context

The CLI uses explicit flags first, then runtime environment variables, then configured defaults.

#### Pi

- agent: `pi`
- session: `PI_SESSION_ID`
- raw source: `PI_SESSION_FILE`
- model: `${PI_PROVIDER}/${PI_MODEL}`

#### Hermes

- agent: `hermes-agent`
- session: `HERMES_SESSION_ID`
- platform: `HERMES_SESSION_PLATFORM` or `HERMES_SESSION_SOURCE`
- chat type: `HERMES_SESSION_CHAT_TYPE` when available
- model: supplied by the Hermes adapter or explicit CLI context

Adapters MUST pass context explicitly when process-global environment state could be ambiguous. A direct human shell invocation uses actor `human:donald` only for explicitly human-authored commands.

### 9.3 Core commands

#### Initialization and health

```bash
memory init
memory validate [<concept-id>] [--strict] [--json]
memory doctor [--json]
memory retry [--all | <retry-id>]
memory recover --transaction <id> [--apply]
```

- `init` creates an empty vault skeleton but never overwrites populated files.
- `validate` checks OKF conformance, local schema, links, indexes, word limits, and configured policy.
- `doctor` checks paths, transaction-state filesystem placement, worker lock, ready/claimed/failed descriptors and stranded queue state, failed lifecycle path/service units and systemd start-limit state, Git, unpushed local commits, Syncthing conflict files, adapter installation, and secret exclusions.
- `retry` atomically republishes eligible failed descriptors to ready state after automatic retries are exhausted.
- `recover` previews interrupted-transaction recovery by default; `--apply` performs the displayed recovery plan.

#### Retrieval

```bash
memory search <query> [--type TYPE] [--scope SCOPE] [--tag TAG]
              [--creator ACTOR] [--status STATUS]
              [--verification unverified|machine-confirmed|human-reviewed]
              [--stale]
              [--limit N] [--reason TEXT] [--json]
memory show <concept-id> [--reason TEXT] [--no-audit] [--json]
```

`--no-audit` is reserved for human maintenance and internal validation. Agent instructions prohibit its use for ordinary retrieval.

#### Mutation

```bash
memory create --type TYPE --scope SCOPE --title TITLE
              --description TEXT --body-file PATH [--source RESOURCE ...]
              [--slug SLUG] [--allow-long]
memory update <concept-id> --body-file PATH [metadata options]
memory delete <concept-id> --reason TEXT
              [--authorized-by human:donald --authorization-source RESOURCE]
memory rename <concept-id> <new-slug> --reason TEXT
memory verify <concept-id> [--authorization-source RESOURCE] [--note TEXT]
memory reconcile <concept-id> --summary TEXT
memory apply <transaction.yaml> [--dry-run]
```

All mutation commands support a dry-run representation through either `--dry-run` or the batch transaction command. Mutation output includes changed paths and the local commit hash.

#### Lifecycle worker

```bash
memory worker --once
```

On every start, the command acquires one worker lock, recovers descriptors already in `claimed/` first, then atomically moves and processes one `ready/` descriptor at a time until both directories are empty. It deletes a descriptor only after committed materialization; replay after a commit-before-delete crash is safe by event idempotency. Retryable failures receive bounded capped backoff within this invocation, and exhausted work moves to unwatched `failed/`. `memory retry` republishes failed work. It is run by a systemd user `Type=oneshot` service activated by a `.path` unit; no timer or application daemon is used.

#### Sessions

```bash
memory session start --agent AGENT --session-id ID [context options]
memory session access --session-id ID --mode injected|search|show
                      [--query TEXT] [--concept ID ...]
memory session checkpoint --session-id ID --event-id ID
                          --trigger compaction|compression|reset|new|finalization
                          [--native-summary-file PATH] [context options]
memory session finalize --session-id ID [--event-id ID]
memory session recover [--agent AGENT]
```

Both external adapters use the JSON form of these CLI commands and do not import the Python library directly. The lifecycle worker is internal to the memory application and may call the core library.

`memory delete` rejects a `Note` with `content_owner: user` unless a `human:donald` authorization and source are supplied. An interactive human may run `memory verify <concept-id>` directly; an agent asserting human review must include an authorization source.

### 9.4 Batch transaction format

```yaml
version: 1
actor:
  by: pi/0.84.2
  model: openai-codex/gpt-5.4
  session_id: 019...
summary: Refine the project and its related deployment procedure.
operations:
  - action: update
    id: concepts/agent-memory-system
    body_file: /tmp/project.md
  - action: create
    type: Procedure
    scope: personal
    title: Deploy the memory CLI
    description: Verified steps for deploying the CLI on the server.
    slug: deploy-memory-cli
    body_file: /tmp/procedure.md
    sources:
      - resource: ../../sessions/pi/2026/019....md#checkpoint-2--compaction
        checkpoint_id: pi:019...:compact:2
```

All operations either commit together or are rolled back/recovered as one logical transaction.

## 10. Search and duplicate detection

### 10.1 Search inputs

Search uses:

- exact or partial filename and title matches;
- frontmatter type, scope, tags, actors, status, verification, and staleness;
- description text;
- Markdown body text; and
- linked concept IDs.

No external index is authoritative. An optional ephemeral cache may be rebuilt entirely from Markdown and is not required for correctness.

### 10.2 Ranking

The deterministic default rank is:

1. exact concept ID or slug;
2. exact normalized title;
3. title prefix/substring;
4. tag and description matches;
5. body matches;
6. case-insensitive title, then concept ID tie-break.

The CLI explains which fields matched. This ordering is fixed for the MVP and has no configurable weights.

### 10.3 Duplicate safeguards

Creation preflight checks:

- exact slug collision: hard failure;
- exact normalized title: hard failure with the existing concept suggestion; and
- ordinary deterministic search results: candidate list for the agent to inspect.

The MVP has no fuzzy similarity threshold, fuzzy configuration, or distinct-concept confirmation override. Managed creation with an unknown type fails until `system/memory.yaml` explicitly extends the vocabulary; reading imported unknown types remains valid and produces a warning.

## 11. Transaction and Git semantics

### 11.1 Writer lock

One exclusive server-side lock serializes all managed writes. Reads do not acquire it. The lock lives outside the synchronized vault, preferably under `$XDG_RUNTIME_DIR`, with a user-cache fallback. Lock metadata records PID, command, actor, and acquisition time.

A configurable timeout, initially 10 seconds, ends with a clear error. Stale-lock recovery verifies process liveness before removal.

### 11.2 Preflight

Before rendering a write, the transaction engine verifies:

1. the vault path and Git root;
2. no `sync-conflict` artifact exists anywhere in the vault;
3. the configured branch is valid and no pre-existing staged entry exists;
4. every target and derived target has the expected baseline hash;
5. no target has uncommitted changes outside the proposed transaction;
6. no index or `log.md` edit would overwrite uncommitted user work;
7. the operation's actor, model, session, and source metadata are valid; and
8. content and filename secret scans pass.

Unrelated unstaged files remain untouched. Index updates are incremental so an unrelated dirty concept is not silently incorporated into a staged index. A full index rebuild refuses to run while unreconciled concept edits exist. The engine does not call the Syncthing REST API. It rechecks every current target hash immediately before replacement and aborts if a synchronized or local edit is detected. Syncthing being stopped or unavailable does not block writes.

### 11.3 Logical atomicity

Filesystem replacement across several files cannot be one kernel-level atomic operation. The implementation therefore provides durable logical atomicity:

1. render every output into `/home/donald/.agent-memory-txn/<id>/`, after verifying the transaction root and vault are on the same filesystem;
2. validate the complete candidate tree;
3. write and `fsync` a transaction journal containing the expected parent `HEAD`, target old/new hashes, backup paths, and phase;
4. recheck each current target hash immediately before replacement;
5. atomically replace each target with `os.replace`, `fsync` the file and parent directory, and advance the journal phase;
6. stage only explicit transaction paths after confirming the real Git index was empty at preflight;
7. validate the staged result;
8. commit and record the resulting commit hash in the fsynced journal; and
9. mark the journal complete and remove backups only after recovery is no longer needed.

On an ordinary failure, a target is restored only when its current hash equals the transaction's recorded output hash. An unrecognized hash means an external or native write won the race; recovery stops and preserves both versions for manual resolution. After a process crash, `memory doctor` diagnoses the exact journal phase. `memory recover --transaction <id>` previews roll-forward or rollback; `--apply` executes the displayed plan only when hashes, expected parent, and resulting commit make the outcome provably unambiguous. The engine never uses `git reset --hard` on the vault.

### 11.4 Commit format

Commit subjects are concise and attributable:

```text
memory(pi): update agent-memory-system
memory(hermes): checkpoint session 019...
memory(human): reconcile concise-agent-responses
```

The commit body records transaction ID, actor, exact model when applicable, session ID, operation summary, and changed concept IDs. Git author configuration may use a service identity; concept metadata remains the authoritative content actor. Any pre-existing staged path is a hard preflight failure, preventing an unrelated staged change from entering the transaction commit.

### 11.5 Backup behavior

Local commit success is the MVP durability boundary. The user configures and pushes to the private remote manually using ordinary Git. Remote configuration, availability, and unpushed commits never block memory writes. `memory doctor` warns when the vault has no private remote configured or has local commits not present on its configured upstream, but it does not push.

## 12. Direct edit reconciliation

`memory reconcile`:

1. compares the selected working-tree concept with `HEAD`;
2. refuses unrelated or ambiguous renames;
3. parses and validates frontmatter and body;
4. preserves immutable `created` metadata;
5. updates `generated.by` to `human:donald` and `generated.at` to the reconciliation time;
6. omits `generated.model` for the human edit;
7. clears prior `verified` metadata when the edit is meaningful, requiring a separate explicit verification after the correction;
8. validates links and the 600-word policy;
9. incrementally updates the concept index;
10. prepends a `Reconciliation` log entry; and
11. commits only the selected concept and derived files.

If a user edits `created` metadata directly, reconciliation restores the committed original. Administrative provenance migration is outside the MVP.

## 13. Adapter contracts

### 13.1 Common adapter interface

Both adapters provide:

- `on_session_start(context)`
- `inject_root_index(context)`
- `publish_checkpoint(trigger, native_reference, context)`
- `publish_session_finalize(context)`
- `notify(level, message, context)`
- `publish_retry(operation, sanitized_context)`

An adapter is thin: it atomically publishes a minimal descriptor before returning and does not perform model or Git work in a teardown/reset callback. A lifecycle event becomes recoverable only after publication completes; no recovery is promised if the handler never ran or publication failed. Domain validation, file rendering, Git, retries, and native-summary checkpoint materialization remain in the Python core and `memory worker --once`.

### 13.2 Pi adapter

The Pi adapter is an auto-discovered TypeScript extension. It uses documented lifecycle events:

- `session_start` to establish session state;
- the first `before_agent_start` to add the root index as a visible persistent custom message;
- `session_compact` after a compaction entry is saved, to publish its stable native entry reference and exposed native summary;
- `session_shutdown` for `new`, `resume`, `fork`, and `quit` finalization; and
- Pi UI notification methods for descriptor-publication failures.

`reload` must not close or duplicate the logical session summary. The adapter uses `ctx.sessionManager`, `ctx.model`, and Pi's session environment. It writes the durable descriptor within a bounded timeout, then returns. Checkpoint work must be idempotent because overflow recovery and extension reloads can repeat lifecycle edges.

### 13.3 Hermes adapter

The Hermes adapter is an enabled user plugin plus a narrow gateway hook:

- every `pre_llm_call` lazily and idempotently binds current session state, because `on_session_start` may not run for continued or resumed sessions;
- the same binding injects the root index only when its persisted injection identity has not already been recorded;
- `on_session_start`, when it runs, may eagerly invoke the same idempotent binding but is not required for correctness;
- the gateway `HOOK.yaml` handler observes `session:compress` for Telegram and other gateway sessions;
- the plugin uses `on_session_finalize` before `/new`, gateway reset/GC, or CLI exit;
- the plugin uses `on_session_reset` to bind the new identity; and
- durable adapter state records injection identity, old/new compression lineage, and the current message-row high-water boundary across restart and resume.

Hermes 0.20.0 gateway `session:compress` exposes exactly `platform`, `session_id`, `old_session_id`, `in_place`, and `compression_count`; it exposes no summary, model, timestamp, or native event ID. Because `compression_count` may reset, those five fields alone are not an event identity. After the compression is committed and under the per-session persisted adapter-state lock, the hook reads the previous message-row high-water boundary, obtains the current high-water boundary, and queries only row ID, Hermes summary-classification metadata, and content for candidates in `(previous, current]`. It applies Hermes 0.20.0's recognized classification and structure: a standalone carrier is accepted only when one summary prefix and matching end marker isolate the sole summary body; a merged carrier is accepted only when the recognized merged delimiter uniquely separates preserved content from one prefix/body/end-marker frame. The body after the prefix and before the end marker is the isolated segment; all framing, preserved tail, and live user content is excluded. Missing, duplicate, conflicting, or misordered markers and multiple carriers are ambiguous.

The hook atomically publishes the five fields, both boundaries, pending audit references, and, when isolation is unambiguous, the candidate row ID and SHA-256 of the isolated native summary segment. No summary text, preserved tail, or raw conversation is placed in the descriptor. Its event ID is SHA-256 over a version-tagged canonical JSON encoding of the five fields, both boundaries, and nullable candidate row ID/hash. Before returning, the hook durably persists the current high-water boundary and old/new lineage. Publication and state advancement run under the same lock and must be restart-idempotent, so two in-place compressions may remain queued without either descriptor being rebound to a later row and a reset `compression_count` cannot collide with an older event.

For that event, `memory worker --once` orders queued Hermes descriptors within a logical lineage by ascending message-row boundary, fetches the exact candidate row named inside each descriptor's boundaries, reads only metadata/content needed to repeat classification, re-isolates the segment with the same Hermes 0.20.0 rules, and verifies the row ID and SHA-256 before storing only that isolated segment. It never searches later rows to repair a stale descriptor and never serializes non-summary rows, preserved tails, archived/raw conversation, or history passed to `pre_llm_call`. A missing candidate identity, changed/missing row, ambiguous classification/isolation, or hash mismatch yields lifecycle metadata and `native summary unavailable`.

Hermes 0.20.0 does not expose CLI compression through its public plugin lifecycle. CLI reset, `/new`, and finalization descriptors flush lifecycle/audit state and record `native summary unavailable` unless a separate reliable host surface exposes a classified native summary. The gateway hook itself never supplies model provenance; model identity may come only from separately persisted, session-scoped plugin context. The adapter never reconstructs an interval or invokes another model. Patching Hermes core is outside the MVP. Telegram notification targets must match the configured authenticated direct message.

### 13.4 Index injection record

Every successful automatic index injection creates a session access row with mode `injected`, resource `memory/index.md`, trigger `new session`, and active model. Injection must be visible in the agent transcript or documented native context, not silently hidden.

## 14. Configuration contract

`system/memory.yaml` contains no secrets. Initial shape:

```yaml
version: 1
vault: /home/donald/agent-memory
identity:
  human: human:donald
limits:
  concept_words: 600
locking:
  timeout_seconds: 10
search:
  default_limit: 10
transactions:
  state_dir: /home/donald/.agent-memory-txn
git:
  branch: main
syncthing:
  folder_id: agent-memory
worker:
  state_dir: ~/.local/state/agent-memory/lifecycle
  publish_timeout_ms: 250
notifications:
  pi_tui: true
  hermes_origin: true
  telegram_owner_dm: true
  errors_file: system/errors.md
```

`worker.state_dir` is the single configurable worker-state path. `ready/`, `claimed/`, `failed/`, audit spools, locks, and adapter state are derived beneath it; the three queue directories are not independently configurable. Managed writes always create local commits, and observable lifecycle triggers always request their required checkpoints; neither behavior has an enable/disable configuration switch. There is no summarizer model configuration. Agent model identifiers are captured only as host provenance when a reliable host surface exposes them. Credentials remain owned by Pi or Hermes and are not read for checkpoint generation.

### 14.1 systemd activation contract

The non-hidden `ready/` and `claimed/` directories derived from `worker.state_dir` are both watched. Installation renders both `DirectoryNotEmpty=` values into the user's `.path` unit from the resolved configured state directory. The following unit is only the deployment-default rendered example:

```ini
[Unit]
Description=Activate Agent Memory lifecycle drain

[Path]
DirectoryNotEmpty=%h/.local/state/agent-memory/lifecycle/ready
DirectoryNotEmpty=%h/.local/state/agent-memory/lifecycle/claimed
Unit=agent-memory-lifecycle.service

[Install]
WantedBy=default.target
```

The target service has `Type=oneshot` and `ExecStart=memory worker --once`. After rendering or updating both units, installation runs `systemctl --user daemon-reload`, then `systemctl --user enable --now agent-memory-lifecycle.path`, and enables boot-without-login recovery with `loginctl enable-linger "$USER"`. If lingering is not enabled, a boot backlog is recovered at the next login instead. The command's single worker lock prevents concurrent drains if activations coalesce or overlap. Watching `claimed/` recovers a post-claim crash; watching `ready/` handles newly published work and backlog. No timer, long-running application daemon, or in-process queue consumer is part of the MVP.

Repeated hard service crashes can exhaust systemd start limits and strand queue work even though path activation is configured. `memory doctor` inspects both units for failed/start-limit state and reports non-empty `ready/`, `claimed/`, or `failed/` directories, including a watched queue whose path/service is not able to run. After diagnosing the crash with unit status/journal output and correcting its cause, the runbook executes `systemctl --user reset-failed agent-memory-lifecycle.path agent-memory-lifecycle.service`, followed by `systemctl --user enable --now agent-memory-lifecycle.path` to restore activation.

## 15. Error and retry contract

### 15.1 Error record

`system/errors.md` is newest-first and contains:

- UTC timestamp;
- severity;
- operation and retry ID;
- agent and session ID;
- concise sanitized message;
- affected concept IDs or checkpoint ID;
- retry state; and
- resolution timestamp when fixed.

It never stores conversation bodies, raw tool output, secret-bearing command lines, environment dumps, or provider credentials.

### 15.2 Retry descriptor

A retry descriptor contains only the minimum durable inputs needed to reproduce a failed managed operation. Lifecycle descriptors and audit spools live under the user's durable state directory, outside Git and Syncthing, and receive the same secret rejection/redaction policy as vault writes and errors. Large or sensitive native session material remains in the native store and is addressed by session identity and a stable native row/entry reference when available. Retry execution revalidates current state and must not replace a newer checkpoint with stale native-summary or lifecycle data. Retryable work is attempted a bounded number of times with capped backoff before the current oneshot exits; exhaustion atomically moves it to unwatched `failed/`, non-retryable work moves there immediately, and `memory retry` republishes selected failed work to `ready/`.

### 15.3 Failure classes

- validation and duplicate conflicts: no automatic retry;
- target dirty or Syncthing conflict: wait for user resolution;
- lock timeout: bounded automatic retry;
- transient filesystem or host-reference error: exponential retry with cap;
- adapter notification failure: record persistent error and warn next turn; and
- incomplete transaction journal: block new writes until recovery.

## 16. Synchronization contract

### 16.1 Syncthing layout

- server folder: `/home/donald/agent-memory`
- computer folder: a local filesystem path selected by the user
- mode: send and receive on both devices
- versioning: optional; Git remains the primary history
- server writer protocol: ordinary filesystem writes with target-hash rechecks immediately before replacement; no Syncthing REST dependency

`.git/` SHOULD be excluded from Syncthing so the server remains the Git writer and to avoid repository lock/index conflicts. The local computer synchronizes vault content, not the server's Git internals. Obsidian machine-local workspace state is also excluded or selectively synchronized.

### 16.2 Conflict behavior

Syncthing conflict filenames are detected by pattern before any managed write. They MUST NOT be hidden by `.stignore` in a way that prevents server detection. The CLI reports every conflict path and blocks writes globally. Conflict resolution is manual, followed by `memory reconcile` where concept content changed and `memory doctor` before writes resume.

Hash rechecks catch synchronized changes that arrive before each replacement. Because Syncthing is not paused, a narrow race remains between the final hash check and replacement. This limitation is documented and may justify optional coordination only if real collisions are observed.

### 16.3 Hot reload expectation

"Hot reload" means that files created or changed on the server become visible in the local Obsidian vault after Syncthing propagation and normal Obsidian filesystem refresh. It does not require Pi or Hermes to re-read every changed context file during an already-running model call.

## 17. Agent-file and skill work deferred

The MVP performs no visibility snapshot or copy, creates no `agents/` or `skills/` vault directories, and does not alter native files or load paths.

Agent-file/configuration/skill inventory, snapshotting, secret auditing for copies, migration, canonical-path changes, symlink cutover, skill generation/import, bundled-skill classification, divergence handling, validation, and rollback tooling all require a separately approved post-MVP design.

## 18. Security and privacy

- File permissions restrict the vault and code configuration to the server user by default.
- The user manually configures and reviews a private remote before pushing work content.
- Syncthing transport and device authorization must be configured securely.
- Agent-provided paths are resolved and constrained to allowed roots before mutation.
- YAML uses a safe parser; arbitrary tags and object construction are rejected.
- Git arguments are passed as argv, not shell-concatenated strings.
- Markdown and host-provided native summaries are treated as untrusted data when rendered.
- Every staged blob receives a content scan in addition to filename checks; secret-bearing managed writes fail before staging.
- Lifecycle descriptors, worker diagnostics, and errors are redacted before durable storage, including state outside Git.
- Error messages are redacted before Telegram delivery.
- Policy-level channel restrictions do not claim to prevent an unrestricted shell agent from reading files.

## 19. Compatibility and versioning

- Vault configuration has a numeric `version`.
- Batch transaction schemas have an independent version.
- Local frontmatter extensions are backward-compatible additions to OKF v0.2.
- Agent-file snapshots, managed `agents/`/`skills/` directories, configuration or skill migration, and load-path work are deferred in full.
- Adapter compatibility is tested against the installed Pi and Hermes versions and documented in release notes.
- A native agent upgrade that changes lifecycle hooks or file formats causes `memory doctor` to warn until compatibility is revalidated.

## 20. Checkpoint provenance

A concept sourced from a session summary MUST identify the checkpoint, not merely the evolving session file. `sources[].resource` includes a stable Markdown anchor, and the source entry includes the local extension `checkpoint_id`. Completed checkpoint text may be edited, so provenance resolves to its current text while Git retains prior versions. Editors SHOULD preserve checkpoint anchors.

Native raw-store references use opaque IDs where possible rather than absolute paths. Absolute source paths may remain in server-only diagnostic state but SHOULD NOT be synchronized when an opaque reference is sufficient.

## 21. External references

- OKF v0.2 specification: <https://github.com/GoogleCloudPlatform/knowledge-catalog/blob/main/okf/SPEC.md>
- Google Cloud OKF introduction: <https://cloud.google.com/blog/products/data-analytics/how-the-open-knowledge-format-can-improve-data-sharing/>
- Obsidian Bases: <https://help.obsidian.md/Bases/Introduction%20to%20Bases>
- Hermes configuration: <https://hermes-agent.nousresearch.com/docs/user-guide/configuration>
- Hermes memory: <https://hermes-agent.nousresearch.com/docs/user-guide/features/memory>
- Hermes sessions: <https://hermes-agent.nousresearch.com/docs/user-guide/sessions>
- Hermes hooks: <https://hermes-agent.nousresearch.com/docs/user-guide/features/hooks>
- Pi documentation is read from the installed package under `@earendil-works/pi-coding-agent` and should be rechecked against the deployed version during implementation.
