# Implementation and Validation Plan

## 1. Delivery strategy

The system will be delivered in serial milestones. Each milestone has one write path, focused automated tests, and an explicit acceptance gate. Semantic retrieval, workflow optimization, and other deferred features will not be mixed into the MVP.

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
├── duplicates.py
├── indexes.py
├── log.py
├── transactions.py
├── locking.py
├── git.py
├── audit.py
├── sessions.py
├── procedures.py
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

The user confirms that the specification captures the intended system, including the Hermes CLI compression compatibility decision. The baseline is then committed. Material architecture changes after this gate are recorded as specification amendments rather than silently implemented.

## 5. Milestone 1 — Project skeleton and pure domain model

### Work

1. Create `pyproject.toml`, `uv.lock`, package entry point, lint, and test configuration.
2. Implement configuration loading with defaults and path normalization.
3. Implement frontmatter parsing and safe YAML round-trip behavior.
4. Define concept, actor, source, verification, and session models.
5. Implement concept ID and slug validation.
6. Implement body word counting and the default 600-word limit.
7. Implement OKF v0.2 and local-profile validation.
8. Add deterministic fixture builders for temporary vaults.

### Tests

- parses minimal conformant OKF;
- rejects missing or empty `type`;
- requires local title, description, scope, and attribution;
- preserves unknown frontmatter fields;
- accepts bare and list-form `verified` values;
- rejects scalar `sources` entries and requires mappings containing `resource`;
- requires `content_owner` for Notes;
- distinguishes human, agent, and process actors;
- requires exact model field for agent-generated content;
- omits model requirement for human/process actors;
- enforces one scope;
- rejects unknown types for managed creation while accepting them as base-OKF-conformant imported content with a local warning;
- counts body words consistently;
- rejects invalid slugs and traversal paths; and
- never constructs arbitrary YAML objects.

### Gate

`uv run pytest tests/unit` and lint pass. A concept can round-trip without losing unknown metadata or changing content unexpectedly.

## 6. Milestone 2 — Read-only vault and search

### Work

1. Implement vault discovery and the OKF conformance boundary.
2. Implement deterministic root/concept index parsing.
3. Implement metadata and full-text scanning over Markdown.
4. Implement the fixed explainable result ordering and filters.
5. Implement `memory search`, `memory show`, `memory validate`, and JSON output.
6. Implement read-time staleness and trust-tier presentation.
7. Implement locked atomic append to the durable per-session audit spool with explicit session context. Session Markdown materialization remains in Milestone 5.

### Tests

- exact slug/title outranks body-only matches;
- type and scope filters are exact;
- stale and unverified filters behave as specified;
- result order is stable across runs;
- search explains matched fields;
- search does not require a generated index to be correct;
- show resolves full and unambiguous short IDs;
- path traversal is rejected;
- access events record agent, model, query/reason, and concepts in the non-vault audit spool;
- concurrent append and process-exit tests preserve complete JSONL records;
- direct human reads may opt out of session audit; and
- concurrent readers do not block each other.

### Gate

Given a fixture vault, an agent can discover a concept through the root index, search it deterministically, open it, and durably spool a correct context-access event without modifying a synchronized vault file.

## 7. Milestone 3 — Transaction engine and managed writes

### Work

1. Implement `memory init`, the vault skeleton, initial root files, and the nine-view `memories.base`.
2. Implement global writer lock and stale-lock diagnostics.
3. Implement Syncthing conflict-file detection without a REST API dependency.
4. Implement Git status parsing by exact path and block any pre-existing staged entry.
5. Implement `/home/donald/.agent-memory-txn/`, same-filesystem verification, fsynced phase journals, backups, compare-and-replace, rollback, and explicit recovery.
6. Recheck target hashes immediately before replacement and abort on detected concurrent changes.
7. Implement incremental concept index updates.
8. Implement OKF `log.md` prepending.
9. Implement exact-path staging and commit metadata.
10. Implement create, update, delete, rename, and batch apply.
11. Implement duplicate checks and distinct-concept override.
12. Implement user-owned Note deletion authorization.
13. Keep local commits independent of Syncthing availability and remote Git state.

### Tests

- init is idempotent and non-destructive;
- generated `memories.base` contains exactly the nine required views;
- the transaction state directory must be outside the vault and on the same filesystem;
- concurrent writes serialize;
- lock timeout reports owner metadata;
- any Syncthing conflict artifact blocks writes;
- Syncthing being stopped or unavailable does not block writes;
- exact slug and normalized title duplicates fail;
- similar title requires explicit distinct confirmation;
- one batch changes several concepts in one commit;
- only transaction-owned paths are staged;
- any pre-staged path blocks the transaction;
- unrelated unstaged files remain dirty and uncommitted;
- a dirty target aborts without changing it;
- dirty `index.md` or `log.md` aborts relevant writes;
- incremental index updates do not absorb unrelated dirty concept metadata;
- a synchronized change observed before replacement causes an abort;
- rollback occurs only when the current hash is the known transaction output;
- every fsynced journal phase has deterministic doctor diagnosis and preview-first recovery;
- deletion removes active content but remains recoverable from Git;
- an agent cannot delete a user-owned Note without `human:donald` authorization and a source;
- rename updates vault links; and
- remote configuration and availability do not affect local commits.

### Gate

A newly initialized fixture vault can complete a multi-concept transaction with fault injection at every write/commit boundary without losing user content or staging unrelated files.

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

A concept can be modified as an external/unmanaged working-tree edit in an integration fixture, then reconciled, logged, and committed without losing attribution. The real Obsidian/Syncthing round trip is reserved for Milestones 10–11.

## 9. Milestone 5 — Session summaries and retries

### Work

1. Implement the supervised durable event worker and queue.
2. Implement session file creation and stable path rules.
3. Materialize spooled context-access events into session Markdown.
4. Implement idempotent checkpoint append and checkpoint index generation.
5. Implement finalization, incomplete-session recovery, and status transitions.
6. Define structured summarizer input/output schemas.
7. Implement sanitized event/retry descriptors and retry policies.
8. Implement `system/errors.md`, `system/status.md`, `memory retry`, and `memory doctor` session checks.
9. Implement reusable-knowledge candidate validation and handoff to ordinary transactions.

### Tests

- one session ID maps to one evolving file;
- repeated compactions append ordered checkpoints;
- duplicate event ID is a no-op;
- retry replaces a failure placeholder without duplicating the checkpoint;
- minimal tool output policy is enforced in structured output validation;
- access events are durable before checkpoint commit and become Obsidian-visible at the checkpoint;
- finalization commits pending audit events;
- editing completed checkpoint text preserves its anchor and Git retains the prior version;
- abrupt session recovery marks status correctly;
- lifecycle enqueue returns within its timeout and host termination immediately afterward does not lose the event;
- summary failure never blocks the caller;
- retry does not apply stale output over a newer checkpoint; and
- no raw transcript is written to the vault.

### Gate

A synthetic session with two compactions and finalization can terminate immediately after each enqueue and still produces, through the worker, one readable Markdown file with three indexed checkpoints, context-access history, exact models, and no routine tool dump.

## 10. Milestone 6 — Procedures and automatic skill promotion

### Work

1. Implement minimal use events containing timestamp, outcome, and source checkpoint.
2. Derive successful use count only from explicit successful outcomes.
3. Record failed-use lessons without incrementing success.
4. Implement promotion eligibility checks for three successes, stable steps, a clear verification method, and target compatibility.
5. Have the durable worker trigger promotion automatically when eligibility is reached.
6. Generate Agent Skills-compliant `SKILL.md` packages.
7. Select shared, Pi-only, or Hermes-only target, defaulting to shared.
8. Keep the source concept as `type: Procedure`, shorten it, and link it bidirectionally with the skill in the same transaction.
9. Validate resulting skill discovery metadata.

### Tests

- fewer than three successes cannot promote;
- missing verification method cannot promote;
- failed use does not increment success;
- every use has timestamp, outcome, and stable source checkpoint;
- actor, model, and detailed evidence remain discoverable through the source checkpoint;
- successful count is derived rather than independently editable;
- reaching eligibility queues automatic promotion exactly once;
- omitted promotion target defaults to shared;
- agent-specific tools require an agent-specific target;
- skill frontmatter satisfies the Agent Skills standard;
- the source remains a concise `Procedure` and no longer duplicates executable steps;
- skill and procedure link to each other; and
- promotion is one Git transaction and failure does not block session lifecycle.

### Gate

Focused integration tests show that a procedure with three fixture uses is promoted automatically to a loadable shared skill while retaining a concise source Procedure and provenance.

## 11. Milestone 7 — Initial agent-file copy

### Work

1. Copy Pi global `AGENTS.md` to `agents/pi/AGENTS.md` when present.
2. Copy Hermes `SOUL.md`, `memories/USER.md`, and `memories/MEMORY.md` to their vault paths.
3. Refuse non-regular files, secret-bearing content, ambiguous case variants, or existing vault targets that would be overwritten.
4. Leave every native source unchanged and document that vault copies are snapshots.

### Deferred work

- symlink creation;
- configuration and settings migration;
- skill migration and bundled-skill classification;
- reusable migration commands;
- divergence handling and rollback tooling.

### Tests

- selected text files are copied byte-for-byte;
- missing optional `AGENTS.md` is reported without failure;
- existing vault targets are not overwritten;
- `.env`, authentication files, state databases, and locks are never copied;
- case-variant `SOUL.md` ambiguity requires resolution; and
- native files remain unchanged.

### Gate

A temporary fake Pi/Hermes home copies only the four approved context files without changing native files, importing secrets, or creating symlinks.

## 12. Milestone 8 — Pi adapter

### Work

1. Re-read the installed Pi extension, compaction, session, skills, and environment documentation for the deployed version.
2. Implement the global TypeScript extension using the `memory` CLI as its only memory-system boundary.
3. Inject `memory/index.md` once per logical new session as visible context.
4. Record injection audit in the durable spool.
5. Enqueue `session_compact` events using stable saved-entry references.
6. Enqueue finalization on new, resume, fork, and quit; ignore reload as a logical end.
7. Have the worker use the captured active Pi model by default and support configured override.
8. Show TUI notifications for enqueue failures and persistent worker failures.
9. Add adapter version compatibility checks to `memory doctor`.

### Tests

- startup/new injects once;
- reload does not duplicate injection or finalize;
- two compactions produce two idempotent checkpoints;
- `/new` finalizes the old session before new-session injection;
- model changes are recorded exactly;
- summary failure does not cancel native session action;
- immediate host termination after shutdown enqueue still permits worker recovery;
- extension shutdown on quit durably enqueues pending audit references within the fixed timeout; and
- no raw session JSONL is copied.

### Gate

Recorded Pi lifecycle events and a controlled Pi test invocation complete the adapter contract against a temporary vault. Production-vault and Obsidian validation are reserved for Milestones 10–11.

## 13. Milestone 9 — Hermes adapter

### Work

1. Re-read the installed Hermes plugin, hooks, LLM access, context, sessions, and skills documentation for the deployed version.
2. Implement and enable a user plugin for CLI and gateway lifecycle, using the `memory` CLI as its only memory-system boundary.
3. Inject the root index on the first turn of a new session and spool the injection audit.
4. Bind exact model, session, platform, and chat context safely under concurrent gateway sessions.
5. Implement a gateway `HOOK.yaml` handler for `session:compress` and enqueue stable event references.
6. Enqueue finalization on `/new`, reset, gateway rotation/GC, and CLI exit.
7. For Hermes CLI 0.20.0, capture reset/finalization and incorporate intermediate CLI compression at the next detectable checkpoint; do not patch Hermes core in the MVP.
8. Use host-owned active-model completion by default and support configured override; record the provider/model actually returned.
9. Notify the originating interface and authenticated Telegram owner DM where supported.
10. Add compatibility checks to `memory doctor`.

### Tests

- CLI and Telegram sessions use distinct IDs;
- first-turn injection happens once;
- repeated gateway in-place compression increments the same summary file;
- rotated gateway compression maps old/new IDs without orphaning checkpoints;
- Hermes CLI finalization incorporates any otherwise unobservable compressed interval without claiming an exact intermediate timestamp;
- `/new` finalizes the outgoing ID and binds the new ID;
- concurrent Telegram sessions do not leak session context;
- notifications cannot target a group or unknown user;
- model/provider changes are recorded exactly;
- failure does not prevent reset; and
- `state.db` and legacy raw sessions remain outside the vault.

### Gate

Recorded Hermes CLI/gateway lifecycle events and controlled local adapter invocations complete the contract against a temporary vault. Live authenticated Telegram and production-vault validation are reserved for Milestone 11.

## 14. Milestone 10 — Syncthing, Obsidian, and manual private backup

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

## 15. Milestone 11 — Full acceptance exercise

Run the following live scenario with evidence captured in an acceptance report:

1. Start a new Pi session and show the injected root index.
2. Search for a fixture concept and show the context-access audit.
3. Create a personal project concept from Pi.
4. Observe it in Obsidian.
5. Start Hermes through authenticated Telegram and retrieve the same concept.
6. Update it from Hermes with exact actor/model attribution.
7. Trigger multiple Pi compactions and Hermes gateway compressions and verify one evolving session file per agent.
8. For Hermes CLI, verify reset/finalization capture and the documented 0.20.0 intermediate-compression limitation.
9. Run `/new` or reset and verify final checkpoints.
10. Edit a concept in Obsidian and reconcile it.
11. Simulate a dirty targeted file and prove the agent cannot overwrite it.
12. Simulate a Syncthing conflict copy and prove all writes fail closed.
13. Stop Syncthing temporarily and prove ordinary writes remain available.
14. Simulate summarizer failure and immediate host exit; prove native session flow continues, notifications appear, and `memory retry` succeeds.
15. Inspect `memory/log.md`, concept metadata, session audit, and local Git history.
16. Run `memory validate --strict` and `memory doctor`.
17. Verify that no raw sessions or secrets exist in either repository.

### Required evidence

- command outputs and exit codes;
- relevant Git commit hashes;
- sanitized screenshots or notes from Obsidian Bases;
- Pi and Hermes session IDs;
- paths to session summaries;
- failure/retry records; and
- final validation and doctor reports.

Focused integration suites separately cover pre-staged-path blocking, Note deletion authorization, substantial-change verification invalidation, procedure-use events, and automatic skill promotion.

## 16. Continuous validation commands

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

## 17. Test strategy

### 17.1 Unit tests

Pure parsing, validation, ranking, rendering, duplicate detection, and state transition behavior.

### 17.2 Integration tests

Temporary Git repositories, subprocess CLI execution, dirty-file behavior, pre-staged-path rejection, exact-path commit staging, Syncthing conflict detection and outage behavior, lock contention, crash-journal phases, recovery, and initial agent-file copying.

### 17.3 Adapter contract tests

Recorded/synthetic native lifecycle events are fed to adapters. These tests isolate version-specific event mapping from core session behavior.

### 17.4 End-to-end tests

Live Pi, Hermes CLI, Hermes Telegram DM, Syncthing, and Obsidian. External surfaces are not adequately validated by mocks alone. Private-remote setup and manual push are validated separately as an operational backup step.

### 17.5 Fault injection

At minimum, inject failure:

- before and after every target replacement;
- before and after Git staging;
- before commit;
- after commit;
- during summary generation;
- during notification;
- immediately after lifecycle enqueue and host termination;
- while a target is dirty;
- with an unrelated staged path;
- under lock contention;
- when a target hash changes before replacement;
- while Syncthing is unavailable; and
- with a Syncthing conflict artifact present.

## 18. Operational runbook requirements

Before production use, documentation must cover:

- installation and upgrades;
- private remote setup and manual push;
- Syncthing setup and exclusions;
- vault initialization;
- Pi and Hermes adapter installation;
- initial agent-file copy and its snapshot limitation;
- search and mutation examples;
- direct edit reconciliation;
- duplicate resolution;
- Syncthing conflict resolution;
- transaction recovery;
- summary retry;
- skill promotion;
- backup restoration;
- secret rotation following accidental exposure; and
- complete uninstall without deleting the vault.

## 19. Feature-creep gates

The following work requires a separate proposal based on observed MVP evidence:

| Deferred feature | Evidence required before starting |
|---|---|
| Embeddings/vector search | documented retrieval misses not solved by metadata/full text |
| Reranking | irrelevant deterministic result sets with measurable task impact |
| Automatic workflow learning | repeated procedures and a way to score successful outcomes |
| Typed relations | queries that cannot be expressed reliably through links and prose |
| Direct-edit watcher | reconciliation burden high enough to justify daemon complexity |
| Native Hermes memory logging | meaningful unobserved changes causing audit gaps |
| Hard channel isolation | Hermes begins serving untrusted users or groups |
| Retrieval optimization | baseline access logs and task outcomes sufficient for evaluation |

## 20. Initial risks and mitigations

| Risk | Mitigation |
|---|---|
| Git and Syncthing both react to frequent files | exclude `.git` from Syncthing; server is sole Git writer; recheck target hashes immediately before replacement |
| Syncthing writes during the final replacement race | document the narrow race, abort when hash changes are observed, and reconsider coordination only after real collisions |
| Manual edits leave a dirty tree | block pre-staged files, use exact-path staging, targeted aborts, and explicit reconcile |
| Generated indexes absorb unreconciled changes | incremental index updates; full rebuild dirty check |
| Multi-file process crash | external sibling transaction journals, backups, doctor diagnosis, and preview-first recovery |
| Hermes gateway session context leaks between chats | use native context variables/explicit adapter context, not global environment alone |
| Session summarization adds latency or cost | enqueue-first lifecycle callbacks; supervised worker; checkpoint boundaries only; active model configurable |
| Hermes CLI 0.20 lacks a compression event | capture gateway compression and CLI reset/finalization; do not patch core in the MVP |
| Agents create duplicate or low-quality concepts | mandatory duplicate search, 600-word limit, structured metadata, and Git reviewability |
| Work content reaches an unsuitable remote | the user reviews the private remote and pushes manually; the CLI never auto-pushes |
| Skills duplicate procedures | automatic promotion keeps a concise source Procedure linked to the canonical executable skill |
| Native upgrades break adapters | compatibility checks, version pinning during MVP, live upgrade validation |

## 21. Recommended first implementation slice

The first coding slice should stop after Milestone 3 and provide a usable local CLI against a temporary fixture vault:

- initialize and validate OKF concepts;
- deterministic search/show;
- create/update/delete/rename;
- duplicate detection;
- transaction lock and conflict protection;
- generated index and log; and
- exact-path Git commits.

This slice proves the storage and safety model before agent hooks, model calls, Syncthing, or the initial agent-file copy can obscure basic failures.
