# Main User Stories

These stories summarize the primary MVP user outcomes described in `MEMORY_SYSTEM_SPECIFICATION.md` and the documents in `docs/`.

## 1. Inspect and organize memory in Obsidian

**As Donald, I want** durable knowledge stored as concise Markdown concepts with clear metadata, **so that** I can inspect, filter, link, and correct everything the agents know using Obsidian.

**Acceptance criteria:**
- Each concept has one type and scope, a title, description, body, and attribution.
- Concepts are normally limited to 600 words and use stable, readable filenames.
- Obsidian Bases provides Work, Projects, People, Preferences, Procedures, Notes, Tasks, Decisions, and References views.
- No proprietary database is required to understand the memory corpus.

## 2. Share context between Pi and Hermes

**As Donald, I want** Pi and Hermes to use the same canonical concept store, **so that** I do not need to repeat project context, preferences, decisions, or procedures when switching agents or interfaces.

**Acceptance criteria:**
- Both agents receive the compact root index when a new session starts.
- Both agents can search, open, create, and update the same concepts through the `memory` CLI.
- Work memories are available through authenticated Hermes Telegram direct messages.
- The complete corpus is not automatically injected into every session.

## 3. Retrieve relevant context just in time

**As Donald, I want** agents to retrieve only the memory needed for the current task, **so that** context remains focused, explainable, and efficient.

**Acceptance criteria:**
- Search covers concept IDs, titles, metadata, tags, descriptions, links, and body text.
- Results follow a fixed deterministic order and explain why each result matched.
- Agents open detailed concepts only when needed.
- The MVP works without embeddings, vector search, or configurable ranking.

## 4. Capture and refine durable knowledge proactively

**As Donald, I want** agents to create or refine useful memories without requiring approval for every change, **so that** the system becomes more helpful during normal work.

**Acceptance criteria:**
- Agents search for duplicates before creating a concept.
- Existing concepts are updated in place when they represent the same knowledge.
- Every managed change records the creator, latest editor, exact model when applicable, timestamp, and available source.
- Incorrect or obsolete knowledge is removed from the active bundle while remaining recoverable through Git.

## 5. Review and correct memories directly

**As Donald, I want** to edit concepts directly in Obsidian and explicitly reconcile those edits, **so that** I retain control over memory accuracy without losing provenance or history.

**Acceptance criteria:**
- `memory reconcile` validates and commits a selected direct edit.
- Original creation metadata is preserved, while the latest editor becomes `human:donald`.
- Meaningful edits clear active verification until the concept is reviewed again.
- `memory verify` records explicit human review, and agents may assert it only with user authorization and provenance.

## 6. Understand what context each agent used

**As Donald, I want** each session to show which memory was automatically injected, searched, and opened, **so that** I can understand how stored context influenced an agent's work.

**Acceptance criteria:**
- `memory search` and `memory show` durably record access events outside the synchronized vault.
- Events include timestamp, query or reason, concepts, agent, and exact model.
- Access records are added to the session summary at the next checkpoint or finalization.
- Ordinary retrieval does not create a Git commit for every read.

## 7. Preserve concise session continuity

**As Donald, I want** each Pi and Hermes session represented by one evolving Markdown summary, **so that** important objectives, decisions, outcomes, and next steps survive compaction, reset, and session changes without storing raw transcripts.

**Acceptance criteria:**
- Compaction, compression, reset, `/new`, and finalization append idempotent checkpoints when supported.
- Summaries include essential context, decisions, actions, changed files, memory changes, unresolved items, and context access.
- Reusable knowledge is merged into concepts at checkpoint boundaries.
- Raw Pi JSONL and Hermes session databases remain outside the vault.

## 8. Turn proven procedures into reusable skills

**As Donald, I want** repeated successful procedures promoted into native agent skills, **so that** reliable workflows become executable without creating speculative or duplicate instructions.

**Acceptance criteria:**
- Each procedure use records a timestamp, outcome, and source checkpoint.
- Promotion requires at least three successful uses, stable steps, a verification method, and known agent compatibility.
- Shared skills are preferred unless agent-specific capabilities are required.
- The source Procedure remains concise, retains provenance and use history, and links to the promoted `SKILL.md`.

## 9. Protect edits with atomic history and conflict checks

**As Donald, I want** managed writes to be transactional, versioned, and conflict-aware, **so that** agents cannot overwrite my work or accidentally commit unrelated changes.

**Acceptance criteria:**
- Writes are serialized and commit all related concept, link, index, and log changes together.
- Dirty target files, pre-staged Git changes, changed target hashes, and Syncthing conflict copies block writes clearly.
- Only transaction-owned paths are staged in one local Git commit.
- Interrupted transactions can be diagnosed and previewed before recovery.
- Reads remain available during write conflicts.

## 10. Synchronize safely and recover from failures

**As Donald, I want** memory changes synchronized to my local Obsidian vault and operational failures surfaced without blocking agent work, **so that** the system remains visible, resilient, and private.

**Acceptance criteria:**
- Syncthing propagates vault content while excluding `.git`, machine-local state, secrets, and transient files.
- Git provides local history; pushes to a reviewed private remote remain manual.
- Summary, extraction, or promotion failures are queued for retry and do not block compaction, reset, `/new`, or exit.
- Errors appear through agent notifications, `system/errors.md`, and `memory doctor` without exposing prompts, credentials, or raw sessions.
