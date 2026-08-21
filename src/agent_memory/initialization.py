"""Idempotent, non-destructive vault initialization."""

from __future__ import annotations

from pathlib import Path

from agent_memory.git import ensure_repository, init_repository, staged_paths
from agent_memory.locking import writer_lock
from agent_memory.transactions import (
    TransactionError,
    execute_transaction,
    incomplete_transactions,
    syncthing_conflicts,
)

ROOT_INDEX = """---
okf_version: "0.2"
---
# Agent Memory

This vault stores concise OKF concepts for Pi and Hermes. Search with
`memory search`, then open selected concepts with `memory show`.

- [Concept index](concepts/index.md)
- [Managed log](log.md)
- [Obsidian views](memories.base)
- [Sessions](../sessions/)
"""

BASES = """filters:
  and:
    - file.inFolder("memory/concepts")
views:
  - type: table
    name: Work
    filters:
      and: ['scope == "work"']
  - type: table
    name: Projects
    filters:
      and: ['type == "Project"']
  - type: table
    name: People
    filters:
      and: ['type == "Person"']
  - type: table
    name: Preferences
    filters:
      and: ['type == "Preference"']
  - type: table
    name: Procedures
    filters:
      and: ['type == "Procedure"']
  - type: table
    name: Notes
    filters:
      and: ['type == "Note"']
  - type: table
    name: Tasks
    filters:
      and: ['type == "Task"']
  - type: table
    name: Decisions
    filters:
      and: ['type == "Decision"']
  - type: table
    name: References
    filters:
      and: ['type == "Reference"']
"""

GITIGNORE = """# Obsidian machine-local state
.obsidian/workspace*.json
.obsidian/cache/

# Syncthing and conflict artifacts
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
"""


def _skeleton(root: Path, state_dir: Path, branch: str) -> dict[str, bytes]:
    config = f"""version: 1
vault: {root}
identity:
  human: human:donald
limits:
  concept_words: 600
locking:
  timeout_seconds: 10
search:
  default_limit: 10
transactions:
  state_dir: {state_dir}
git:
  branch: {branch}
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
"""
    values = {
        ".gitignore": GITIGNORE,
        "README.md": "# Agent Memory Vault\n\nManaged by the `memory` CLI.\n",
        "memory/index.md": ROOT_INDEX,
        "memory/log.md": "# Log\n",
        "memory/memories.base": BASES,
        "memory/concepts/index.md": "# Concepts\n",
        "system/memory.yaml": config,
        "system/errors.md": "# Errors\n",
        "system/status.md": "# Status\n",
    }
    return {path: content.encode() for path, content in values.items()}


def _verify_existing_layout(root: Path, relatives: tuple[str, ...]) -> None:
    if root.is_symlink() or (root.exists() and not root.is_dir()):
        raise TransactionError(f"vault root must be a real directory: {root}")
    if not root.exists():
        return
    for relative in relatives:
        current = root
        parts = Path(relative).parts
        for part in parts[:-1]:
            current /= part
            if current.is_symlink() or (current.exists() and not current.is_dir()):
                raise TransactionError(f"unsafe initialization parent: {current}")
        leaf = root / relative
        if leaf.is_symlink() or (leaf.exists() and not leaf.is_file()):
            raise TransactionError(f"unsafe initialization target: {leaf}")
    git_dir = root / ".git"
    if git_dir.is_symlink() or (git_dir.exists() and not git_dir.is_dir()):
        raise TransactionError(f"unsafe Git directory: {git_dir}")


def initialize_vault(
    root: Path,
    *,
    state_dir: Path | None = None,
    timeout: float = 10,
    branch: str = "main",
) -> tuple[str, ...]:
    """Create only missing skeleton files through one managed transaction."""

    requested_root = root.expanduser()
    state_dir = (state_dir or requested_root.parent / ".agent-memory-txn").expanduser()
    skeleton_names = tuple(_skeleton(requested_root.absolute(), state_dir.absolute(), branch))
    required_dirs = ("memory/concepts", "sessions/pi", "sessions/hermes", "system")
    _verify_existing_layout(requested_root, skeleton_names)
    if requested_root.exists():
        for relative in required_dirs:
            current = requested_root
            for part in Path(relative).parts:
                current /= part
                if current.is_symlink() or (current.exists() and not current.is_dir()):
                    raise TransactionError(f"unsafe initialization directory: {current}")
    requested_root.mkdir(parents=True, exist_ok=True)
    root = requested_root.resolve()
    if state_dir.is_symlink():
        raise TransactionError("transaction state directory must not be a symlink")
    state_dir = state_dir.resolve(strict=False)
    try:
        state_dir.relative_to(root)
    except ValueError:
        pass
    else:
        raise TransactionError("transaction state directory must be outside the vault")
    state_parent = state_dir
    while not state_parent.exists() and state_parent != state_parent.parent:
        state_parent = state_parent.parent
    if state_parent.stat().st_dev != root.stat().st_dev:
        raise TransactionError("transaction state directory must be on the same filesystem")
    conflicts = syncthing_conflicts(root)
    if conflicts:
        raise TransactionError(f"Syncthing conflict artifacts block writes: {', '.join(conflicts)}")

    with writer_lock(
        state_dir / "writer.lock",
        timeout=timeout,
        command="init",
        actor="process:memory-cli",
    ):
        if not (root / ".git").exists():
            init_repository(root, branch)
        ensure_repository(root, branch)
        staged = staged_paths(root)
        if staged:
            raise TransactionError(f"pre-existing staged paths block writes: {', '.join(staged)}")
        conflicts = syncthing_conflicts(root)
        if conflicts:
            raise TransactionError(
                f"Syncthing conflict artifacts block writes: {', '.join(conflicts)}"
            )
        pending = incomplete_transactions(state_dir, root)
        if pending:
            raise TransactionError(
                f"incomplete transactions require recovery: {', '.join(pending)}"
            )

        skeleton = _skeleton(root, state_dir, branch)
        missing = {
            path: content for path, content in skeleton.items() if not (root / path).exists()
        }
        created = tuple(sorted(missing))
        if missing:
            execute_transaction(
                root,
                state_dir,
                missing,
                branch=branch,
                actor="process:memory-cli",
                model=None,
                session_id=None,
                summary="Initialize vault skeleton",
                subject="memory(process): initialize vault",
                concept_ids=(),
            )
        for directory in required_dirs:
            path = root / directory
            if path.is_symlink() or (path.exists() and not path.is_dir()):
                raise TransactionError(f"unsafe initialization directory: {path}")
            path.mkdir(parents=True, exist_ok=True)
        return created
