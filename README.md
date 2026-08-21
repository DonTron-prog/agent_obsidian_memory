# Agent Obsidian Memory

A local-first, observable memory system shared by Pi and Hermes Agent. The system stores durable knowledge as short, human-readable Markdown, exposes it through an Obsidian vault, and gives both agents a deterministic CLI for retrieval and controlled updates.

## Status

The project is currently specified but not yet implemented. The MVP baseline incorporates the design decisions from the completed specification review.

- [Canonical memory-system specification](MEMORY_SYSTEM_SPECIFICATION.md)
- [Product specification](docs/product-specification.md)
- [Technical specification](docs/technical-specification.md)
- [Implementation and validation plan](docs/implementation-plan.md)

## Repository boundaries

- This repository contains the memory-system code, tests, adapters, and documentation.
- The runtime vault will be a separate private Git repository at `/home/donald/agent-memory`.
- Transaction journals and backups live outside it at `/home/donald/.agent-memory-txn/`.
- The vault will be synchronized to the computer running Obsidian with Syncthing.
- Local Git commits are automatic; the user pushes them to the private remote manually.

## Governing principles

1. Markdown is the source of truth.
2. Stored memory must remain inspectable and editable in Obsidian.
3. Retrieval must be observable: the user can determine what context was loaded, when, and why.
4. Agents may create and update memory proactively, but every managed mutation is attributable and recoverable.
5. The MVP uses deterministic, agentic search. Semantic retrieval is deferred until measured failures justify it.
6. The memory system must not become a second task scheduler, secret store, or opaque agent database.

## MVP design decisions

- Search uses one fixed deterministic ordering; ranking optimization is deferred.
- Both Pi and Hermes adapters call the shared `memory` CLI.
- A durable worker and queue handle checkpoints, extraction, automatic procedure promotion, notifications, and retries without blocking agent lifecycle events.
- Procedures record minimal timestamp/outcome/source use events and promote automatically after three successful uses, stable steps, a clear verification method, and known compatibility. The source remains `type: Procedure`.
- Notes require `content_owner: user|agent`; user-owned Notes need explicit authorization before deletion.
- Substantial concept changes clear active verification, while Git and `memory/log.md` preserve review history.
- Managed writes do not pause Syncthing and remain available when it is down. Conflict copies still block writes, and target hashes are checked immediately before replacement.
- Any pre-existing staged Git path blocks managed writes; unrelated unstaged files remain untouched.
- The vault is the source of truth for selected non-secret agent files. Pi and Hermes load them through direct paths or symlinks after a backed-up migration.
- Vault-managed files include Pi `AGENTS.md`, `settings.json`, optional system-prompt files, Hermes `SOUL.md`, `USER.md`, `MEMORY.md`, and `config.yaml`.
- User-created Pi skills and user-created or modified Hermes skills are migrated into `shared`, `pi-only`, or `hermes-only`; untouched bundled skills remain in native package-managed locations.
- Hermes gateway and Telegram compression checkpoints are immediate. Hermes CLI 0.20.0 compressed intervals are captured at reset or finalization; no core patch is included.
- `memory status`, automatic Git push, remote fingerprinting, and extra Bases views are deferred.
