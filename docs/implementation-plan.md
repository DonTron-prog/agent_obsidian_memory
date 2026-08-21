# Implementation and Validation Plan

## 1. Delivery strategy

This derived plan follows [product-specification.md](product-specification.md), the sole normative source, which wins on conflict. The system will be delivered in serial milestones with focused tests and explicit gates. Semantic retrieval, automatic extraction, procedure-to-skill work, and agent-file/snapshot/migration work will not be mixed into the MVP.

The implementation repository is currently empty apart from specifications. The vault is a separate deployment artifact and must not be created inside this repository.

## 2. Proposed technology stack

- Python 3.11 or newer
- `uv` for project, lockfile, test execution, and tool installation
- `typer` or `click` for the CLI
- `ruamel.yaml` for safe YAML round-tripping and preservation of unknown fields
- `pydantic` or dataclasses plus explicit validators for local schemas
- `python-frontmatter` only if it preserves required formatting; otherwise a small delimited-frontmatter parser
- `portalocker` or native `fcntl.flock` for the Linux writer lock
- Git invoked through argv-based subprocess calls
- `pytest` for unit and integration tests
- `ruff` for linting and formatting
- optional `mypy` or `pyright` for static checks
- TypeScript for the Pi adapter
- Python user plugin and, where necessary, gateway hook for Hermes

Dependencies should remain modest. The corpus is small enough that SQLite, a daemon, and a vector database are unnecessary in the MVP.

## 3. Planned source layout

```text
src/agent_memory/
├── __init__.py
├── cli.py
├── config.py
├── models.py
├── okf.py
├── markdown.py
├── validation.py
├── search.py
├── indexes.py
├── log.py
├── transactions.py
├── locking.py
├── git.py
├── audit.py
├── sessions.py
├── notifications.py
├── retry.py
├── worker.py
└── doctor.py

adapters/
├── pi/
│   ├── index.ts
│   └── README.md
└── hermes/
    ├── plugin.yaml
    ├── __init__.py
    ├── gateway-hook/              # only if compression/Telegram events require it
    └── README.md

deploy/systemd/
├── agent-memory-lifecycle.path
└── agent-memory-lifecycle.service

tests/
├── unit/
├── integration/
├── fixtures/
└── e2e/
```

## 4. Milestone 0 — Specification baseline

### Deliverables

- product specification;
- technical specification;
- implementation and validation plan;
- repository README; and
- documented unresolved assumptions, if any.

### Gate

The user confirms that the product specification captures the intended system, including the Hermes CLI compression compatibility decision, and that derived documents agree with it. The baseline is then committed. Material architecture changes after this gate amend the product specification rather than being silently implemented.

## 5. Milestone 1 — Project skeleton and pure domain model

**Status: Complete**

### Work

1. [x] Create `pyproject.toml`, `uv.lock`, package entry point, lint, and test configuration.
2. [x] Implement configuration loading with defaults and path normalization.
3. [x] Implement frontmatter parsing and safe YAML round-trip behavior.
4. [x] Define concept, actor, source, verification, and session models.
5. [x] Implement concept ID and slug validation.
6. [x] Implement body word counting and the default 600-word limit.
7. [x] Implement OKF v0.2 and local-profile validation.
8. [x] Add deterministic fixture builders for temporary vaults.

### Tests

- [x] parses minimal conformant OKF;
- [x] rejects missing or empty `type`;
- [x] requires local title, description, scope, and attribution;
- [x] preserves unknown frontmatter fields;
- [x] accepts bare and list-form `verified` values;
- [x] rejects scalar `sources` entries and requires mappings containing `resource`;
- [x] requires `content_owner` for Notes;
- [x] distinguishes human, agent, and process actors;
- [x] requires exact model field for agent-generated content;
- [x] omits model requirement for human/process actors;
- [x] enforces one scope;
- [x] rejects unknown types for managed creation while accepting them as base-OKF-conformant imported content with a local warning;
- [x] counts body words consistently;
- [x] rejects invalid slugs and traversal paths; and
- [x] never constructs arbitrary YAML objects.

### Gate

- [x] `uv run pytest tests/unit` and lint pass.
- [x] A concept round-trips without losing unknown metadata or changing body content unexpectedly.

## 6. Milestone 2 — Read-only vault and search

**Status: Complete**

### Work

1. [x] Implement vault discovery and the OKF conformance boundary.
2. [x] Implement deterministic root/concept index parsing.
3. [x] Implement metadata and full-text scanning over Markdown.
4. [x] Implement the fixed explainable result ordering and filters.
5. [x] Implement `memory search`, `memory show`, `memory validate`, and JSON output.
6. [x] Implement read-time staleness and trust-tier presentation.
7. [x] Implement locked atomic append to the durable per-session audit spool with explicit session context. Session Markdown materialization remains in Milestone 5.

### Tests

- [x] exact slug/title outranks body-only matches;
- [x] type and scope filters are exact;
- [x] stale and verification-tier filters (`unverified`, `machine-confirmed`, and `human-reviewed`) behave as specified;
- [x] result order is stable across runs;
- [x] search explains matched fields;
- [x] search does not require a generated index to be correct;
- [x] show resolves full and unambiguous short IDs;
- [x] path traversal is rejected;
- [x] access events record agent, model, query/reason, and concepts in the non-vault audit spool;
- [x] concurrent append and process-exit tests preserve complete JSONL records;
- [x] direct human reads may opt out of session audit; and
- [x] concurrent readers do not block each other.

### Gate

- [x] Given a fixture vault, an agent can discover a concept through the root index, search it deterministically, open it, and durably spool a correct context-access event without modifying a synchronized vault file.

## 7. Milestone 3 — Transaction engine and managed writes

**Status: Complete**

### Work

1. [x] Implement `memory init`, the vault skeleton, initial root files, and the nine-view `memories.base`.
2. [x] Implement global writer lock and stale-lock diagnostics.
3. [x] Implement Syncthing conflict-file detection without a REST API dependency.
4. [x] Implement Git status parsing by exact path and block any pre-existing staged entry.
5. [x] Implement `/home/donald/.agent-memory-txn/`, same-filesystem verification, fsynced phase journals, backups, compare-and-replace, rollback, and explicit recovery.
6. [x] Recheck target hashes immediately before replacement and abort on detected concurrent changes.
7. [x] Implement incremental concept index updates.
8. [x] Implement OKF `log.md` prepending.
9. [x] Implement exact-path staging and commit metadata.
10. [x] Implement create, update, delete, rename, and batch apply.
11. [x] Implement exact slug and exact normalized-title duplicate rejection, with ordinary deterministic search candidates.
12. [x] Implement user-owned Note deletion authorization.
13. [x] Keep local commits independent of Syncthing availability and remote Git state.
14. [x] Reject secret-bearing managed filenames and content before any staging.

### Tests

- [x] init is idempotent and non-destructive;
- [x] generated `memories.base` contains exactly the nine required views;
- [x] the transaction state directory must be outside the vault and on the same filesystem;
- [x] concurrent writes serialize;
- [x] lock timeout reports owner metadata;
- [x] any Syncthing conflict artifact blocks writes;
- [x] Syncthing being stopped or unavailable does not block writes;
- [x] exact slug and normalized title duplicates fail;
- [x] one batch changes several concepts in one commit;
- [x] only transaction-owned paths are staged;
- [x] any pre-staged path blocks the transaction;
- [x] unrelated unstaged files remain dirty and uncommitted;
- [x] a dirty target aborts without changing it;
- [x] dirty `index.md` or `log.md` aborts relevant writes;
- [x] incremental index updates do not absorb unrelated dirty concept metadata;
- [x] a synchronized change observed before replacement causes an abort;
- [x] rollback occurs only when the current hash is the known transaction output;
- [x] every fsynced journal phase has deterministic doctor diagnosis and preview-first recovery;
- [x] deletion removes active content but remains recoverable from Git;
- [x] an agent cannot delete a user-owned Note without `human:donald` authorization and a source;
- [x] rename updates vault links;
- [x] remote configuration and availability do not affect local commits;
- [x] managed writes reject secret-bearing filenames and content before staging; and
- [x] secret rejection leaves the Git index and managed targets unchanged.

### Gate

- [x] A newly initialized fixture vault can complete a multi-concept transaction with fault injection at every write/commit boundary without losing user content or staging unrelated files.

## 8. Milestone 4 — Human reconciliation and verification

### Work

1. Implement `memory reconcile` for direct Obsidian edits.
2. Preserve original creation metadata.
3. Attribute reconciled content to `human:donald` without a model.
4. Implement `memory verify`, explicit human authorization provenance, verification invalidation, and OKF trust-tier display.
5. Implement full index rebuild with dirty-corpus safeguards.
6. Add conflict-resolution guidance to error output.

### Tests

- direct body correction reconciles and commits;
- direct description change updates the generated index;
- direct attempt to alter `created` is rejected or restored;
- oversized direct edit fails without overwrite;
- human verification occurs only through explicit command intent and an authorization source or interactive confirmation;
- meaningful content/source changes clear current verification, while rename-only and verification-only changes do not;
- verification appends rather than destroys independent machine checks;
- reconcile does not stage another dirty concept; and
- full rebuild refuses unreconciled edits.

### Gate

A concept can be modified as an external/unmanaged working-tree edit in an integration fixture, then reconciled, logged, and committed without losing attribution. The real Obsidian/Syncthing round trip is reserved for Milestones 8–9.

## 9. Milestone 5 — Native-summary checkpoints and lifecycle drain

### Work

1. Implement durable non-hidden lifecycle `ready/`, `claimed/`, and unwatched `failed/` descriptors with atomic publication and idempotent event identities.
2. Implement `memory worker --once`: on every start acquire one worker lock, recover claimed descriptors first, then atomically claim and process ready descriptors one at a time until both are empty.
3. Delete a descriptor only after committed materialization; make commit-before-delete replay safe by event ID. Run bounded capped-backoff retries in the same invocation, move exhausted work to `failed/`, and make `memory retry` republish it. Do not add a timer or leave delayed work in `ready/`.
4. Keep only configurable `worker.state_dir`; derive non-hidden `ready/`, `claimed/`, and `failed/` beneath it. Render both resolved `DirectoryNotEmpty=` paths into the user's systemd `.path`, target the `Type=oneshot` service with `WantedBy=default.target`, run `systemctl --user daemon-reload`, and enable the path and user lingering; document next-login recovery when lingering is unavailable.
5. Implement session file creation, stable paths, idempotent checkpoint append, and checkpoint index generation.
6. Store Pi's exposed `compactionEntry.summary` and stable compaction entry ID. For Hermes gateway events, bind persisted previous/current message-row high-water boundaries and an unambiguous isolated-summary candidate row ID/SHA-256 at descriptor publication; include those values in the versioned canonical event ID.
7. Make the Hermes worker fetch only the exact bounded candidate row, repeat Hermes 0.20.0 standalone/merged carrier isolation, verify row ID/hash, and store only the isolated segment. When classification, isolation, or verification fails, record lifecycle metadata and `native summary unavailable` without preserved tails, archived/raw conversation rows, `pre_llm_call` history, other dialogue/tool output, synthesized text, or another model.
8. Materialize spooled context-access and lifecycle state at checkpoints and always on reset, `/new`, and finalization.
9. Implement finalization, incomplete-session recovery, status transitions, `system/errors.md`, `system/status.md`, `memory retry`, and `memory doctor` checks. Doctor detects failed/start-limited lifecycle path/service units and stranded queue state. Apply the same secret rejection/redaction policy to descriptors, worker diagnostics, and error state, including lifecycle files outside Git.

### Tests

- one session ID maps to one evolving file;
- repeated native summaries append ordered checkpoints;
- duplicate event ID is a no-op;
- atomic ready-to-claimed handling and one worker lock prevent duplicate concurrent drains;
- a post-claim crash leaves recoverable work in `claimed/`, and the next invocation processes it before `ready/`;
- a commit-before-descriptor-delete crash replays as an idempotent no-op and then deletes the descriptor;
- `memory worker --once` drains claimed and ready backlog and exits;
- retryable work exhausts bounded capped backoff in the same invocation, moves to unwatched `failed/`, and is republished only by `memory retry`;
- failed descriptors remain sanitized and diagnosable without reactivating the path unit;
- configuration exposes only `worker.state_dir`; queue paths are derived, and installation renders both resolved `DirectoryNotEmpty=` values, runs daemon-reload, targets the oneshot service, and activates on each non-empty backlog;
- enabled user lingering recovers backlog after reboot without login, while disabled lingering recovers it at next login;
- repeated hard worker crashes trigger systemd start-limit failure; `memory doctor` reports the failed path/service state and stranded queue, and recovery with `systemctl --user reset-failed agent-memory-lifecycle.path agent-memory-lifecycle.service` followed by `systemctl --user enable --now agent-memory-lifecycle.path` resumes draining after the crash is fixed;
- Pi's exposed native summary/stable entry ID and Hermes's publication-bound boundaries/candidate row ID/isolated-segment hash are stored without a model call;
- absent, ambiguous, or hash-mismatched native summary produces lifecycle metadata and `native summary unavailable` without preserved tails, archived/raw conversation rows, `pre_llm_call` history, or other dialogue/tool output;
- access events are durable before checkpoint commit and materialize on checkpoint, reset, `/new`, and finalization;
- a handler that completes descriptor publication permits recovery after immediate host termination; a handler that never runs is not claimed recoverable;
- managed writes reject secret-bearing filenames/content, and lifecycle descriptors, worker diagnostics, notifications, and `system/errors.md` redact secrets and raw prompts in both vault and outside-Git state;
- materialization failure never blocks the caller;
- editing completed checkpoint text preserves its anchor and Git retains the prior version; and
- no raw transcript is written to the vault.

### Gate

A synthetic session completes publication of native-summary and finalization descriptors, terminates immediately, and is drained by the dual-directory systemd-triggered `memory worker --once` into one readable Markdown file with indexed checkpoints and context access. Post-claim and commit-before-delete crash recovery pass. Exhausted work lands in sanitized unwatched failed state and succeeds only after `memory retry`. A descriptor without a resolvable native summary records `native summary unavailable`; no second model call, raw dialogue, `pre_llm_call` history, or routine tool dump occurs.

## 10. Milestone 6 — Pi adapter

### Work

1. Re-read the installed Pi extension, compaction, session, and environment documentation for the deployed version.
2. Implement the global TypeScript extension using the `memory` CLI as its only memory-system boundary.
3. Inject `memory/index.md` once per logical new session as visible context.
4. Record injection audit in the durable spool.
5. Atomically publish `session_compact` descriptors with stable saved-entry references, exposed native summaries, and host provenance.
6. Publish finalization descriptors on new, resume, fork, and quit; ignore reload as a logical end.
7. Include the active Pi model only as host provenance; do not call a summarizer.
8. Show TUI notifications for publication and persistent worker failures.
9. Add adapter version compatibility checks to `memory doctor`.

### Tests

- startup/new injects once;
- reload does not duplicate injection or finalize;
- two compactions produce two idempotent checkpoints;
- `/new` finalizes the old session before new-session injection;
- model changes are recorded exactly;
- native-summary materialization failure does not cancel native session action;
- immediate host termination after completed descriptor publication still permits worker recovery, without claiming recovery when the handler did not run;
- extension shutdown on quit durably publishes pending audit references within the fixed timeout; and
- no raw session JSONL is copied.

### Gate

Recorded Pi lifecycle events and a controlled Pi test invocation complete the adapter contract against a temporary vault. Production-vault and Obsidian validation are reserved for Milestones 8–9.

## 11. Milestone 7 — Hermes adapter

### Work

1. Re-read the installed Hermes plugin, hooks, context, and sessions documentation for the deployed version.
2. Implement and enable a user plugin for CLI and gateway lifecycle, using the `memory` CLI as its only memory-system boundary.
3. On every `pre_llm_call`, lazily and idempotently bind the current Hermes session and inject the root index only when the persisted injection identity is absent; do not depend on `on_session_start` for continued/resumed sessions.
4. Persist injection identity, old/new compression lineage, and the current message-row high-water boundary across restart/resume. Bind exact model, session, platform, and chat context safely under concurrent gateway sessions, but use model provenance only from a separate reliable plugin context.
5. Implement a gateway `HOOK.yaml` handler for `session:compress`. Treat its installed 0.20.0 payload—only `platform`, `session_id`, `old_session_id`, `in_place`, and `compression_count`—as a committed-compression signal. Under the adapter-state lock, query only bounded message row IDs, summary-classification metadata, and content; atomically publish the five fields, previous/current high-water boundaries, and nullable isolated-summary candidate row ID/SHA-256, with no summary/raw text, then persist the current boundary and lineage before returning.
6. Compute the event ID from a versioned canonical serialization of every published identity field, including boundaries and nullable candidate identity, rather than the five hook fields alone. Make the worker order queued descriptors within each logical lineage by ascending message-row boundary, fetch each exact bounded row, re-isolate a recognized Hermes 0.20.0 standalone or merged summary segment using its prefix, merged delimiter, and end marker, verify row ID/hash, and store only the isolated segment. Never serialize preserved tail/live user content, archived/raw conversation rows, or `pre_llm_call` history.
7. Publish finalization descriptors on `/new`, reset, gateway rotation/GC, and CLI exit so lifecycle/audit state is flushed.
8. For Hermes CLI 0.20.0, record lifecycle metadata and `native summary unavailable` unless a separate reliable host surface exposes a classified native summary. Never reconstruct an intermediate interval or patch Hermes core.
9. Do not invoke host-owned or configured model completion; the gateway hook does not expose model identity.
10. Notify the originating interface and authenticated Telegram owner DM where supported.
11. Add compatibility checks to `memory doctor`.

### Tests

- CLI and Telegram sessions use distinct IDs;
- first-turn injection happens once, and every later `pre_llm_call` rebinds idempotently;
- continued/resumed sessions bind and avoid duplicate injection when `on_session_start` is absent;
- process restart preserves injection identity and old/new compression lineage;
- the gateway payload contract rejects any assumption that summary, model, timestamp, or native event ID is present;
- replaying the same exposed fields, boundaries, and candidate identity produces the same versioned deterministic event ID;
- two in-place compressions published without draining bind successive non-overlapping message-row boundaries and later drain as distinct ordered event IDs/checkpoints;
- after process restart with `compression_count` reset, persisted boundary/lineage produces another distinct ordered event ID/checkpoint rather than colliding with an older event;
- repeated gateway in-place compression increments the same summary file;
- rotated gateway compression maps persisted old/new IDs without orphaning checkpoints;
- the resolver fetches only the descriptor-bound row, re-isolates a recognized standalone carrier, verifies row ID/segment hash, and stores only that segment;
- a recognized merged carrier stores only the isolated summary segment and excludes its preserved tail/live user content;
- duplicate/misordered markers, multiple candidates, changed rows, and hash mismatch record `native summary unavailable`, while non-summary/raw rows and `pre_llm_call` history never enter a descriptor or checkpoint;
- Hermes CLI reset/finalization records `native summary unavailable` when no separate reliable native summary is exposed;
- `/new` finalizes the outgoing ID and binds the new ID;
- concurrent Telegram sessions do not leak session context;
- notifications cannot target a group or unknown user;
- model/provider changes are recorded only when available from reliable session-scoped plugin context;
- failure does not prevent reset; and
- `state.db` and legacy raw sessions remain outside the vault.

### Gate

Recorded Hermes CLI/gateway lifecycle events and controlled local adapter invocations complete the contract against a temporary vault. Live authenticated Telegram and production-vault validation are reserved for Milestone 9.

## 12. Milestone 8 — Syncthing, Obsidian, and manual private backup

### Work

1. Create the server vault at `/home/donald/agent-memory` with `memory init`.
2. Configure Syncthing on server and computer.
3. Exclude `.git/`, machine-local Obsidian workspace files, locks, and transient state from synchronization, while ensuring Syncthing conflict copies remain detectable on the server.
4. Open the local replica as an Obsidian vault.
5. Enable the Bases core plugin and verify the nine required named views.
6. Verify that writes remain available when Syncthing is stopped.
7. Measure normal propagation time and document recovery steps, conflict handling, and the narrow replacement race.
8. Configure and review a separate private Git remote suitable for work content, then perform a manual push outside the `memory` CLI.

### Gate

A managed server concept creation appears in Obsidian without restart, a local Obsidian correction reaches the server and reconciles successfully, writes remain available during a Syncthing outage, and the user can push the resulting local commit manually.

## 13. Milestone 9 — Full acceptance exercise

Run the following live scenario with evidence captured in an acceptance report:

1. Start a new Pi session and show the injected root index.
2. Search for a fixture concept and show the context-access audit.
3. Create a personal project concept from Pi.
4. Observe it in Obsidian.
5. Start Hermes through authenticated Telegram and retrieve the same concept.
6. Update it from Hermes with exact actor/model attribution.
7. Trigger multiple Pi compactions and two queued Hermes gateway compressions; verify Pi's exposed summaries and Hermes's publication-bound row ranges, isolated-summary identities/hashes, ordered distinct event IDs/checkpoints, and available provenance in one evolving session file per agent with no second model call. Restart Hermes, reset its compression count, and verify the persisted boundary still yields a distinct next event.
8. Verify merged-carrier isolation excludes preserved tail/live user content. For ambiguous or hash-mismatched Hermes gateway summaries and Hermes CLI reset/finalization, verify unavailable summaries become `native summary unavailable`, not reconstructed text, archived/raw rows, or `pre_llm_call` history.
9. Run `/new` or reset and verify final lifecycle/audit materialization.
10. Edit a concept in Obsidian and reconcile it.
11. Simulate a dirty targeted file and prove the agent cannot overwrite it.
12. Simulate a Syncthing conflict copy and prove all writes fail closed.
13. Stop Syncthing temporarily and prove ordinary writes remain available.
14. After completed descriptor publication, simulate post-claim crash, commit-before-delete crash, exhausted failure, and immediate host exit; prove native session flow continues, idempotent recovery works, failed work stays unwatched, notifications are redacted, and `memory retry` plus `memory worker --once` succeeds.
15. Verify configured-state rendering of both `DirectoryNotEmpty=` paths, lingering reboot recovery, and next-login recovery without lingering. Force repeated hard worker crashes through the systemd start limit; verify `memory doctor` reports failed units and stranded queue state, diagnose/fix the crash, reset both units with the documented command, and re-enable/start the path.
16. Inspect `memory/log.md`, concept metadata, session audit, and local Git history.
17. Run `memory validate --strict` and `memory doctor`.
18. Verify that no raw sessions or secrets exist in either repository or in lifecycle/error state outside Git.

### Required evidence

- command outputs and exit codes;
- relevant Git commit hashes;
- sanitized screenshots or notes from Obsidian Bases;
- Pi and Hermes session IDs;
- paths to session summaries;
- failure/retry records; and
- final validation and doctor reports.

Focused integration suites separately cover pre-staged-path blocking, Note deletion authorization, substantial-change verification invalidation, managed-write secret rejection, lifecycle descriptor/error redaction outside Git, post-claim and commit-before-delete replay, failed-descriptor republication, restart/resume binding, and absence of second-pass summarization.

## 14. Continuous validation commands

The eventual repository should provide stable commands such as:

```bash
uv sync --frozen
uv run ruff check .
uv run ruff format --check .
uv run pytest
uv run mypy src                 # if adopted
uv run memory --help
```

Adapter checks may add:

```bash
npm test --prefix adapters/pi   # if a local package is needed
uv run pytest tests/integration/test_hermes_adapter.py
```

No implementation milestone is complete merely because code was written; its milestone gate must pass.

## 15. Test strategy

### 15.1 Unit tests

Pure parsing, validation, ranking, rendering, exact duplicate rejection, and state transition behavior.

### 15.2 Integration tests

Temporary Git repositories, subprocess CLI execution, managed-write secret rejection, dirty-file behavior, pre-staged-path rejection, exact-path commit staging, Syncthing conflict detection and outage behavior, lock contention, crash-journal phases, recovery, lifecycle descriptor/error redaction outside Git, and lifecycle descriptor draining.

### 15.3 Adapter contract tests

Recorded/synthetic native lifecycle events are fed to adapters. These tests isolate version-specific event mapping from core session behavior.

### 15.4 End-to-end tests

Live Pi, Hermes CLI, Hermes Telegram DM, Syncthing, and Obsidian. External surfaces are not adequately validated by mocks alone. Private-remote setup and manual push are validated separately as an operational backup step.

### 15.5 Fault injection

At minimum, inject failure:

- before and after every target replacement;
- before and after Git staging;
- before commit;
- after commit;
- during native-summary checkpoint materialization;
- during notification;
- immediately after completed lifecycle descriptor publication and host termination;
- immediately after ready-to-claimed movement;
- after checkpoint commit but before claimed-descriptor deletion;
- through retry exhaustion and failed-descriptor republication;
- while a target is dirty;
- with an unrelated staged path;
- under lock contention;
- when a target hash changes before replacement;
- while Syncthing is unavailable; and
- with a Syncthing conflict artifact present.

## 16. Operational runbook requirements

Before production use, documentation must cover:

- installation and upgrades;
- private remote setup and manual push;
- Syncthing setup and exclusions;
- vault initialization;
- Pi and Hermes adapter installation;
- systemd user `.path` and `Type=oneshot` installation, rendering both `DirectoryNotEmpty=` entries from `worker.state_dir`, daemon-reload, `WantedBy=default.target`, path enablement, user lingering, reboot-without-login and next-login recovery, `memory worker --once`, post-claim/commit-before-delete recovery, worker-lock diagnosis, unwatched failed descriptors, `memory retry` republication, start-limit diagnosis, and exact reset-failed/re-enable recovery commands;
- explicit deferral of agent-file snapshots/migration and managed `agents/`/`skills/` directories;
- search and mutation examples;
- direct edit reconciliation;
- exact duplicate resolution;
- Syncthing conflict resolution;
- transaction recovery;
- lifecycle checkpoint retry and `native summary unavailable` handling;
- backup restoration;
- secret rotation following accidental exposure; and
- complete uninstall without deleting the vault.

For a repeated hard-crash/start-limit incident, the runbook diagnoses and fixes the worker crash before clearing state, then uses exactly:

```bash
systemctl --user reset-failed agent-memory-lifecycle.path agent-memory-lifecycle.service
systemctl --user enable --now agent-memory-lifecycle.path
```

## 17. Feature-creep gates

The following work requires a separate proposal based on observed MVP evidence:

| Deferred feature | Evidence required before starting |
|---|---|
| Embeddings/vector search | documented retrieval misses not solved by metadata/full text |
| Reranking | irrelevant deterministic result sets with measurable task impact |
| Automatic extraction, workflow discovery, and procedure-to-skill pipeline | observed recurring work plus a separately approved design for extraction, use events, counters, eligibility, promotion, generated `SKILL.md`, skill directories, and load paths |
| Typed relations | queries that cannot be expressed reliably through links and prose |
| Direct-edit watcher | reconciliation burden high enough to justify daemon complexity |
| Agent-file snapshots, managed `agents/`/`skills/` directories, migration, and symlink cutover | a separately approved copy/migration, secret-audit, backup, validation, divergence, and rollback design |
| Native Hermes memory logging | meaningful unobserved changes causing audit gaps |
| Hard channel isolation | Hermes begins serving untrusted users or groups |
| Retrieval optimization | baseline access logs and task outcomes sufficient for evaluation |

## 18. Initial risks and mitigations

| Risk | Mitigation |
|---|---|
| Git and Syncthing both react to frequent files | exclude `.git` from Syncthing; server is sole Git writer; recheck target hashes immediately before replacement |
| Syncthing writes during the final replacement race | document the narrow race, abort when hash changes are observed, and reconsider coordination only after real collisions |
| Manual edits leave a dirty tree | block pre-staged files, use exact-path staging, targeted aborts, and explicit reconcile |
| Generated indexes absorb unreconciled changes | incremental index updates; full rebuild dirty check |
| Multi-file process crash | external sibling transaction journals, backups, doctor diagnosis, and preview-first recovery |
| Hermes gateway session context leaks between chats or across resume | lazily bind on every `pre_llm_call`; persist injection identity and old/new compression lineage; avoid process-global context |
| Lifecycle work blocks agent reset/exit | bounded atomic descriptor publication; dual-directory systemd path activation; one worker lock; bounded in-invocation retry; unwatched failed state |
| Lifecycle handler never runs before abrupt exit | document that durability begins only after completed atomic descriptor publication; do not claim impossible recovery |
| Hermes 0.20 compression surfaces are incomplete | treat gateway hook as a committed signal; bind persisted row boundaries and isolated-segment row/hash at publication; verify only that exact bounded candidate; record unavailable on ambiguity; do not reconstruct or patch core |
| Repeated worker crashes exhaust systemd start limits | have `memory doctor` report failed units and stranded queues; diagnose first, reset failed path/service units, then re-enable/start the path |
| Agents create duplicate or low-quality concepts | mandatory duplicate search, 600-word limit, structured metadata, and Git reviewability |
| Work content reaches an unsuitable remote | the user reviews the private remote and pushes manually; the CLI never auto-pushes |
| Native upgrades break adapters | compatibility checks, version pinning during MVP, live upgrade validation |

## 19. Recommended first implementation slice

The first coding slice should stop after Milestone 3 and provide a usable local CLI against a temporary fixture vault:

- initialize and validate OKF concepts;
- deterministic search/show;
- create/update/delete/rename;
- exact duplicate rejection;
- transaction lock and conflict protection;
- generated index and log; and
- exact-path Git commits.

This slice proves the storage and safety model before agent hooks, lifecycle activation, or Syncthing can obscure basic failures.
