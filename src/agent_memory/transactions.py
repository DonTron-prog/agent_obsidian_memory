"""Crash-diagnosable compare-and-replace transactions for managed vault writes."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agent_memory.git import (
    GitError,
    commit,
    commit_message,
    commit_parent,
    committed_paths,
    dirty_paths,
    ensure_repository,
    head,
    index_file,
    stage_paths,
    staged_paths,
    unstage_paths,
)
from agent_memory.markdown import FrontmatterError, parse_frontmatter
from agent_memory.secrets import reject_secret_content, reject_secret_path
from agent_memory.validation import DEFAULT_TYPES, validate_local_profile


class TransactionError(ValueError):
    """Raised when a transaction cannot proceed safely."""


class InjectedCrash(BaseException):
    """Fault-injection signal that deliberately leaves the journal recoverable."""


TRANSACTION_ID = re.compile(r"^[0-9a-f]{32}$")
HASH = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class TransactionResult:
    transaction_id: str
    changed_paths: tuple[str, ...]
    commit_hash: str | None
    dry_run: bool = False


def file_hash(path: Path) -> str | None:
    if not path.exists():
        return None
    if path.is_symlink() or not path.is_file():
        raise TransactionError(f"managed target is not a regular file: {path}")
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _mkdirs_fsynced(path: Path) -> None:
    missing: list[Path] = []
    current = path
    while not current.exists():
        missing.append(current)
        current = current.parent
    if current.is_symlink() or not current.is_dir():
        raise TransactionError(f"unsafe directory path: {current}")
    for directory in reversed(missing):
        directory.mkdir()
        _fsync_directory(directory.parent)


def _write_bytes(path: Path, content: bytes) -> None:
    _mkdirs_fsynced(path.parent)
    with path.open("wb") as file:
        file.write(content)
        file.flush()
        os.fsync(file.fileno())
    _fsync_directory(path.parent)


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    temporary = path.with_suffix(".tmp")
    _write_bytes(
        temporary,
        (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode(),
    )
    os.replace(temporary, path)
    _fsync_directory(path.parent)


def _inside(path: Path, root: Path) -> bool:
    try:
        path.resolve(strict=False).relative_to(root.resolve(strict=False))
    except ValueError:
        return False
    return True


def _safe_relative(path: str) -> str:
    if not isinstance(path, str):
        raise TransactionError("transaction path must be text")
    candidate = Path(path)
    if candidate.is_absolute() or ".." in candidate.parts or path in {"", "."} or "\\" in path:
        raise TransactionError(f"unsafe transaction path: {path}")
    return candidate.as_posix()


def syncthing_conflicts(root: Path) -> tuple[str, ...]:
    conflicts: list[str] = []
    for directory, names, files in os.walk(root):
        for name in (*names, *files):
            if ".sync-conflict-" in name:
                conflicts.append((Path(directory) / name).relative_to(root).as_posix())
    return tuple(sorted(conflicts))


def syncthing_conflict_message(conflicts: tuple[str, ...]) -> str:
    return (
        f"Syncthing conflict artifacts block writes: {', '.join(conflicts)}. "
        "Resolve conflict copies manually, reconcile changed concepts with memory reconcile, "
        "then run memory doctor."
    )


def _validate_outputs(
    outputs: Mapping[str, bytes | None],
    *,
    configured_types: frozenset[str],
    max_words: int,
    allow_long: bool,
) -> None:
    for relative, content in outputs.items():
        reject_secret_path(relative)
        if content is None:
            continue
        try:
            text = content.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise TransactionError(f"managed text must be UTF-8: {relative}") from exc
        reject_secret_content(text, path=relative)
        path = Path(relative)
        if (
            path.parent == Path("memory/concepts")
            and path.suffix == ".md"
            and path.name != "index.md"
        ):
            try:
                result = validate_local_profile(
                    parse_frontmatter(text),
                    configured_types=configured_types,
                    managed=True,
                    max_words=max_words,
                    allow_long=allow_long,
                )
            except FrontmatterError as exc:
                raise TransactionError(f"invalid candidate {relative}: {exc}") from exc
            if result.errors:
                details = "; ".join(f"{item.field}: {item.message}" for item in result.errors)
                raise TransactionError(f"invalid candidate {relative}: {details}")


def _fault(name: str, hook: Callable[[str], None] | None) -> None:
    if hook is not None:
        hook(name)
    configured = {item.strip() for item in os.environ.get("MEMORY_FAULT_AT", "").split(",")}
    if name in configured:
        raise InjectedCrash(name)


def _transaction_directory(state_dir: Path, transaction_id: str) -> Path:
    if not TRANSACTION_ID.fullmatch(transaction_id):
        raise TransactionError("transaction ID must be 32 lowercase hexadecimal characters")
    candidate = state_dir / transaction_id
    if not _inside(candidate, state_dir) or candidate.is_symlink():
        raise TransactionError("transaction directory escapes configured state or is a symlink")
    return candidate


def _member(transaction_dir: Path, value: object, *, prefix: str) -> Path:
    if not isinstance(value, str):
        raise TransactionError(f"journal {prefix} path must be text")
    relative = _safe_relative(value)
    if Path(relative).parts[0] != prefix:
        raise TransactionError(f"journal path must be beneath {prefix}/: {relative}")
    candidate = transaction_dir / relative
    if not _inside(candidate, transaction_dir):
        raise TransactionError(f"journal path escapes transaction directory: {relative}")
    current = transaction_dir
    for part in Path(relative).parts:
        current /= part
        if current.is_symlink() or (
            current.exists() and current != candidate and not current.is_dir()
        ):
            raise TransactionError(f"journal path has an unsafe parent: {relative}")
    return candidate


def _hash_value(value: object, *, nullable: bool = True) -> str | None:
    if value is None and nullable:
        return None
    if not isinstance(value, str) or not HASH.fullmatch(value):
        raise TransactionError("journal contains an invalid content hash")
    return value


def _journal(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise TransactionError(f"cannot read transaction journal {path}: {exc}") from exc
    if not isinstance(value, dict) or value.get("version") != 1:
        raise TransactionError(f"unsupported transaction journal: {path}")
    transaction_id = path.parent.name
    if value.get("id") != transaction_id or not TRANSACTION_ID.fullmatch(transaction_id):
        raise TransactionError(f"journal transaction identity is invalid: {path}")
    targets = value.get("targets")
    if not isinstance(targets, list) or not targets:
        raise TransactionError(f"journal targets are invalid: {path}")
    seen: set[str] = set()
    for target in targets:
        if not isinstance(target, dict):
            raise TransactionError(f"journal target is invalid: {path}")
        relative = _safe_relative(target.get("path", ""))
        if relative in seen:
            raise TransactionError(f"journal target is duplicated: {relative}")
        seen.add(relative)
        _hash_value(target.get("old_hash"))
        new_hash = _hash_value(target.get("new_hash"))
        _member(path.parent, target.get("backup"), prefix="backups")
        candidate = target.get("candidate")
        if new_hash is None:
            if candidate is not None:
                raise TransactionError(f"deleted target has a candidate: {relative}")
        else:
            _member(path.parent, candidate, prefix="candidates")
    changed = value.get("changed_paths")
    if (
        not isinstance(changed, list)
        or not all(isinstance(item, str) for item in changed)
        or set(changed) != seen
    ):
        raise TransactionError(f"journal changed paths do not match targets: {path}")
    return value


def incomplete_transactions(state_dir: Path, vault: Path) -> tuple[str, ...]:
    if not state_dir.exists():
        return ()
    pending: list[str] = []
    for path in sorted(state_dir.glob("*/journal.json")):
        value = _journal(path)
        if value.get("vault") == str(vault.resolve()) and value.get("phase") not in {
            "complete",
            "rolled_back",
        }:
            pending.append(path.parent.name)
    return tuple(pending)


def _prepare_state(state_dir: Path, vault: Path) -> None:
    if state_dir.is_symlink() or _inside(state_dir, vault):
        raise TransactionError(
            "transaction state directory must be outside the vault and not a symlink"
        )
    _mkdirs_fsynced(state_dir)
    if not state_dir.is_dir() or state_dir.stat().st_dev != vault.stat().st_dev:
        raise TransactionError("transaction state directory must be on the same filesystem")


def _staged_outputs_match(
    vault: Path, targets: list[dict[str, Any]], expected_paths: tuple[str, ...]
) -> bool:
    staged = staged_paths(vault)
    if set(staged) != set(expected_paths):
        return False
    expected = {target["path"]: target["new_hash"] for target in targets}
    for path in staged:
        content = index_file(vault, path)
        digest = hashlib.sha256(content).hexdigest() if content is not None else None
        if digest != expected[path]:
            return False
    return True


def _owned_staged_paths(vault: Path, journal: Mapping[str, Any]) -> tuple[str, ...] | None:
    staged = staged_paths(vault)
    targets = {target["path"]: target for target in journal["targets"]}
    if any(path not in targets for path in staged):
        return None
    for path in staged:
        content = index_file(vault, path)
        digest = hashlib.sha256(content).hexdigest() if content is not None else None
        if digest != targets[path]["new_hash"]:
            return None
    return staged


def _staged_outputs_are_owned(vault: Path, journal: Mapping[str, Any]) -> bool:
    return _owned_staged_paths(vault, journal) is not None


def _restore_preflight(
    journal: dict[str, Any], *, vault: Path, transaction_dir: Path
) -> dict[str, str | None] | None:
    current_hashes: dict[str, str | None] = {}
    for target in journal["targets"]:
        relative = target["path"]
        path = vault / relative
        if not _inside(path, vault) or path.is_symlink():
            return None
        current = file_hash(path)
        if current not in {target["old_hash"], target["new_hash"]}:
            return None
        current_hashes[relative] = current
        if target["old_hash"] is not None:
            try:
                backup = _member(transaction_dir, target["backup"], prefix="backups")
                if file_hash(backup) != target["old_hash"]:
                    return None
            except TransactionError:
                return None
    return current_hashes


def _restore(journal: dict[str, Any], *, vault: Path, transaction_dir: Path) -> bool:
    """Preflight every target and backup, then restore recognized transaction outputs."""

    targets = journal["targets"]
    current_hashes = _restore_preflight(journal, vault=vault, transaction_dir=transaction_dir)
    if current_hashes is None:
        return False
    for target in reversed(targets):
        relative = target["path"]
        path = vault / relative
        current = file_hash(path)
        if current != current_hashes[relative] or current not in {
            target["old_hash"],
            target["new_hash"],
        }:
            return False
        if current == target["old_hash"]:
            continue
        if target["old_hash"] is None:
            path.unlink(missing_ok=True)
            _fsync_directory(path.parent)
        else:
            backup = _member(transaction_dir, target["backup"], prefix="backups")
            try:
                candidate = _member(transaction_dir, f"restore/{relative}", prefix="restore")
            except TransactionError:
                return False
            _mkdirs_fsynced(candidate.parent)
            shutil.copyfile(backup, candidate)
            with candidate.open("rb") as file:
                os.fsync(file.fileno())
            _fsync_directory(candidate.parent)
            if file_hash(candidate) != target["old_hash"]:
                return False
            _mkdirs_fsynced(path.parent)
            source_parent = candidate.parent
            os.replace(candidate, path)
            _fsync_directory(source_parent)
            _fsync_directory(path.parent)
        if file_hash(path) != target["old_hash"]:
            return False
    return True


def _rollback(journal: dict[str, Any], *, vault: Path, transaction_dir: Path) -> bool:
    owned = _owned_staged_paths(vault, journal)
    if owned is None or not _restore(journal, vault=vault, transaction_dir=transaction_dir):
        return False
    if _owned_staged_paths(vault, journal) != owned:
        return False
    unstage_paths(vault, owned)
    return True


def _cleanup_backups(transaction_dir: Path) -> None:
    backups = transaction_dir / "backups"
    if backups.is_symlink():
        raise TransactionError("transaction backups directory must not be a symlink")
    if backups.exists():
        if not backups.is_dir():
            raise TransactionError("transaction backups path must be a directory")
        shutil.rmtree(backups)
        _fsync_directory(transaction_dir)


def execute_transaction(
    vault: Path,
    state_dir: Path,
    outputs: Mapping[str, bytes | None],
    *,
    branch: str,
    actor: str,
    model: str | None,
    session_id: str | None,
    summary: str,
    subject: str,
    concept_ids: tuple[str, ...],
    configured_types: frozenset[str] = DEFAULT_TYPES,
    max_words: int = 600,
    allow_long: bool = False,
    dry_run: bool = False,
    fault_hook: Callable[[str], None] | None = None,
    adopted_path: tuple[str, str] | None = None,
) -> TransactionResult:
    vault = vault.resolve()
    requested = {_safe_relative(path): content for path, content in outputs.items()}
    adopted_relative = _safe_relative(adopted_path[0]) if adopted_path else None
    adopted_hash = adopted_path[1] if adopted_path else None
    if adopted_relative is not None and (
        adopted_relative not in requested
        or Path(adopted_relative).parent != Path("memory/concepts")
        or Path(adopted_relative).name == "index.md"
        or Path(adopted_relative).suffix != ".md"
    ):
        raise TransactionError("invalid adopted concept path")
    normalized: dict[str, bytes | None] = {}
    for path, content in requested.items():
        current_hash = file_hash(vault / path)
        new_hash = hashlib.sha256(content).hexdigest() if content is not None else None
        if current_hash != new_hash or path == adopted_relative:
            normalized[path] = content
    changed = tuple(sorted(normalized))
    _validate_outputs(
        normalized,
        configured_types=configured_types,
        max_words=max_words,
        allow_long=allow_long,
    )
    _prepare_state(state_dir, vault)
    ensure_repository(vault, branch)
    conflicts = syncthing_conflicts(vault)
    if conflicts:
        raise TransactionError(syncthing_conflict_message(conflicts))
    pending = incomplete_transactions(state_dir, vault)
    if pending:
        raise TransactionError(f"incomplete transactions require recovery: {', '.join(pending)}")
    staged = staged_paths(vault)
    if staged:
        raise TransactionError(f"pre-existing staged paths block writes: {', '.join(staged)}")
    if adopted_relative is not None and (
        dirty_paths(vault, (adopted_relative,)) != (adopted_relative,)
        or file_hash(vault / adopted_relative) != adopted_hash
    ):
        raise TransactionError(f"adopted target changed before transaction: {adopted_relative}")
    dirty = dirty_paths(vault, tuple(path for path in requested if path != adopted_relative))
    if dirty:
        raise TransactionError(
            f"transaction targets have uncommitted changes: {', '.join(dirty)}. "
            "Reconcile intentional direct concept edits with memory reconcile; "
            "resolve derived-file edits manually."
        )
    if not changed:
        raise TransactionError("transaction has no changes")
    baselines = {
        path: adopted_hash if path == adopted_relative else file_hash(vault / path)
        for path in changed
    }
    if dry_run:
        return TransactionResult("dry-run", changed, None, True)

    transaction_id = uuid.uuid4().hex
    transaction_dir = _transaction_directory(state_dir, transaction_id)
    _mkdirs_fsynced(transaction_dir)
    targets: list[dict[str, Any]] = []
    for relative in changed:
        content = normalized[relative]
        candidate_relative = f"candidates/{relative}"
        backup_relative = f"backups/{relative}"
        if content is not None:
            _write_bytes(transaction_dir / candidate_relative, content)
        current = vault / relative
        if current.exists():
            backup = transaction_dir / backup_relative
            _mkdirs_fsynced(backup.parent)
            shutil.copyfile(current, backup)
            with backup.open("rb") as file:
                os.fsync(file.fileno())
            if file_hash(backup) != baselines[relative]:
                raise TransactionError(f"backup verification failed: {relative}")
            _fsync_directory(backup.parent)
        targets.append(
            {
                "path": relative,
                "old_hash": baselines[relative],
                "new_hash": hashlib.sha256(content).hexdigest() if content is not None else None,
                "candidate": candidate_relative if content is not None else None,
                "backup": backup_relative,
            }
        )
    _fsync_directory(transaction_dir)
    journal_path = transaction_dir / "journal.json"
    journal: dict[str, Any] = {
        "version": 1,
        "id": transaction_id,
        "vault": str(vault),
        "expected_head": head(vault),
        "phase": "prepared",
        "actor": actor,
        "summary": summary,
        "changed_paths": list(changed),
        "targets": targets,
        "written": [],
        "commit_hash": None,
    }
    _write_json(journal_path, journal)
    _fault("after_prepare", fault_hook)

    committed = False
    try:
        for target in targets:
            relative = target["path"]
            destination = vault / relative
            _fault(f"before_replace:{relative}", fault_hook)
            if file_hash(destination) != target["old_hash"]:
                raise TransactionError(f"target changed before replacement: {relative}")
            _mkdirs_fsynced(destination.parent)
            if target["candidate"] is None:
                destination.unlink(missing_ok=True)
                _fsync_directory(destination.parent)
            else:
                candidate = transaction_dir / target["candidate"]
                source_parent = candidate.parent
                os.replace(candidate, destination)
                with destination.open("rb") as file:
                    os.fsync(file.fileno())
                _fsync_directory(source_parent)
                _fsync_directory(destination.parent)
            journal["written"].append(relative)
            journal["phase"] = "replacing"
            _write_json(journal_path, journal)
            _fault(f"after_replace:{relative}", fault_hook)

        journal["phase"] = "replaced"
        _write_json(journal_path, journal)
        for target in targets:
            if file_hash(vault / target["path"]) != target["new_hash"]:
                raise TransactionError(f"target changed after replacement: {target['path']}")
        _fault("before_stage", fault_hook)
        if staged_paths(vault):
            raise TransactionError("Git index changed during transaction")
        stage_paths(vault, changed)
        if not _staged_outputs_match(vault, targets, changed):
            raise TransactionError("staged content differs from transaction-owned outputs")
        journal["phase"] = "staged"
        _write_json(journal_path, journal)
        _fault("after_stage", fault_hook)
        for target in targets:
            if file_hash(vault / target["path"]) != target["new_hash"]:
                raise TransactionError(f"target changed before commit: {target['path']}")
        _fault("before_commit", fault_hook)
        if not _staged_outputs_match(vault, targets, changed):
            raise TransactionError("Git index changed before commit")
        journal["phase"] = "committing"
        _write_json(journal_path, journal)
        _fault("after_committing_journal", fault_hook)
        body = (
            f"Transaction: {transaction_id}\nActor: {actor}\n"
            + (f"Model: {model}\n" if model else "")
            + (f"Session: {session_id}\n" if session_id else "")
            + f"Summary: {summary}\nConcepts: {', '.join(concept_ids)}"
        )
        commit_hash = commit(vault, subject=subject, body=body, paths=changed)
        committed = True
        if set(committed_paths(vault, commit_hash)) != set(changed):
            raise TransactionError("commit contains paths outside the transaction")
        _fault("after_commit", fault_hook)
        journal["commit_hash"] = commit_hash
        journal["phase"] = "committed"
        _write_json(journal_path, journal)
        _fault("after_committed_journal", fault_hook)
        journal["phase"] = "complete"
        _write_json(journal_path, journal)
        _cleanup_backups(transaction_dir)
        return TransactionResult(transaction_id, changed, commit_hash)
    except Exception:
        if committed:
            raise
        try:
            journal["phase"] = (
                "rolled_back"
                if _rollback(journal, vault=vault, transaction_dir=transaction_dir)
                else "manual"
            )
            _write_json(journal_path, journal)
        except (OSError, GitError, TransactionError):
            journal["phase"] = "manual"
            _write_json(journal_path, journal)
        raise


def recovery_plan(state_dir: Path, vault: Path, transaction_id: str) -> dict[str, Any]:
    transaction_dir = _transaction_directory(state_dir, transaction_id)
    journal = _journal(transaction_dir / "journal.json")
    if journal.get("vault") != str(vault.resolve()):
        raise TransactionError("transaction belongs to another vault")
    current_head = head(vault)
    detected_commit = None
    if (
        current_head
        and current_head != journal["expected_head"]
        and commit_parent(vault, current_head) == journal["expected_head"]
        and f"Transaction: {transaction_id}" in commit_message(vault, current_head)
    ):
        detected_commit = current_head
    if journal["phase"] in {"complete", "rolled_back"}:
        action = "none"
    elif (journal.get("commit_hash") and current_head == journal["commit_hash"]) or detected_commit:
        action = "finalize"
    else:
        restorable = (
            _staged_outputs_are_owned(vault, journal)
            and _restore_preflight(journal, vault=vault, transaction_dir=transaction_dir)
            is not None
        )
        action = "rollback" if restorable and current_head == journal["expected_head"] else "manual"
    return {
        "transaction_id": transaction_id,
        "phase": journal["phase"],
        "action": action,
        "changed_paths": journal["changed_paths"],
        "current_head": current_head,
        "expected_head": journal["expected_head"],
        "commit_hash": journal.get("commit_hash") or detected_commit,
    }


def apply_recovery(state_dir: Path, vault: Path, transaction_id: str) -> dict[str, Any]:
    plan = recovery_plan(state_dir, vault, transaction_id)
    if plan["action"] == "manual":
        raise TransactionError("recovery is ambiguous; preserved backups require manual resolution")
    transaction_dir = _transaction_directory(state_dir, transaction_id)
    journal_path = transaction_dir / "journal.json"
    journal = _journal(journal_path)
    if plan["action"] == "rollback":
        if not _rollback(journal, vault=vault, transaction_dir=transaction_dir):
            journal["phase"] = "manual"
            _write_json(journal_path, journal)
            raise TransactionError(
                "target or Git index changed during recovery; manual resolution required"
            )
        journal["phase"] = "rolled_back"
        _write_json(journal_path, journal)
    elif plan["action"] == "finalize":
        journal["commit_hash"] = plan["commit_hash"]
        journal["phase"] = "complete"
        _write_json(journal_path, journal)
    _cleanup_backups(transaction_dir)
    return recovery_plan(state_dir, vault, transaction_id)
