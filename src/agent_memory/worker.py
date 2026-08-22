"""Claimed-first one-shot lifecycle drain, bounded retries, and manual republication."""

from __future__ import annotations

import json
import time
import uuid
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from agent_memory.lifecycle import (
    LifecycleError,
    _fsync_directory,
    _publication_lock,
    _publish_payload,
    _write_atomic,
    canonical_json,
    claim_descriptor,
    delete_fsynced,
    descriptor_filename,
    now_utc,
    queue_paths,
    read_descriptor,
    validate_descriptor,
)
from agent_memory.locking import LockTimeoutError, writer_lock
from agent_memory.secrets import redact_sensitive_text
from agent_memory.sessions import MaterializationResult, materialize_descriptor
from agent_memory.transactions import TransactionError, execute_transaction

ATTEMPTS = 3
BACKOFF = (0.1, 0.2)


class WorkerError(ValueError):
    """Raised for worker state that cannot be safely processed."""


def _order(path: Path) -> tuple[object, ...]:
    try:
        value = read_descriptor(path)
        source = value["summary_source"]
        if source["kind"] == "hermes-0.20.0":
            lineage = value["session"]["session_id"]
            return (0, lineage, source["current_message_row_id"], path.name)
        return (1, value["lifecycle"]["occurred_at"], path.name)
    except (OSError, ValueError):
        return (2, path.name)


def _failed_record(
    descriptor: Mapping[str, Any] | None,
    *,
    message: object,
    attempts: int,
    retryable: bool,
    retry_id: str | None = None,
) -> dict[str, Any]:
    return {
        "schema": "agent-memory.lifecycle-failure/v1",
        "retry_id": retry_id or uuid.uuid4().hex,
        "descriptor": dict(descriptor) if descriptor is not None else None,
        "error_class": "retryable" if retryable else "non-retryable",
        "attempt_count": attempts,
        "failed_at": now_utc(),
        "message": redact_sensitive_text(message),
        "retry_state": "exhausted" if retryable else "blocked",
    }


def _store_failed(path: Path, record: Mapping[str, Any]) -> Path:
    target = queue_paths(path.parent.parent, create=True).failed / path.name
    _write_atomic(target, canonical_json(record) + b"\n")
    if path != target:
        delete_fsynced(path)
    return target


def _delete_matching_failed(path: Path, descriptor: Mapping[str, Any]) -> None:
    """Delete only a stale failure whose embedded descriptor is byte-equivalent."""

    failed = queue_paths(path.parent.parent).failed / path.name
    if not failed.exists():
        return
    try:
        record = json.loads(failed.read_text(encoding="utf-8"))
        current = record["descriptor"] if isinstance(record, dict) else None
        value = validate_descriptor(current)
    except (OSError, KeyError, TypeError, ValueError):
        return
    if canonical_json(value) == canonical_json(descriptor):
        delete_fsynced(failed)


def _retryable(exc: Exception) -> bool:
    return isinstance(exc, OSError | LockTimeoutError) and not isinstance(
        exc, LifecycleError | TransactionError
    )


def _record_error(
    vault: Path,
    config: Mapping[str, Any],
    descriptor: Mapping[str, Any] | None,
    record: Mapping[str, Any],
) -> None:
    """Best-effort sanitized vault diagnostic; failed state remains authoritative."""

    path = vault / "system/errors.md"
    marker = f"<!-- error:{record['retry_id']} -->"
    agent = descriptor["session"]["agent"] if descriptor else "unknown"
    session = descriptor["session"]["session_id"] if descriptor else "unknown"
    event = descriptor["event_id"] if descriptor else "quarantined descriptor"
    block = (
        f"{marker}\n## {record['failed_at']} — Lifecycle materialization\n\n"
        f"- **Severity:** error\n- **Agent/session:** {agent}/{session}\n"
        f"- **Failure:** {record['message']}\n- **Affected event:** `{event}`\n"
        f"- **Retry state:** {record['retry_state']} (`{record['retry_id']}`)\n"
    )
    state = Path(config["transactions"]["state_dir"])
    try:
        with writer_lock(
            state / "writer.lock",
            timeout=float(config["locking"]["timeout_seconds"]),
            command="record lifecycle error",
            actor="process:memory-worker",
        ):
            current = path.read_text(encoding="utf-8") if path.exists() else "# Errors\n"
            if marker in current:
                return
            execute_transaction(
                vault,
                state,
                {
                    "system/errors.md": (
                        "# Errors\n\n" + block + current.removeprefix("# Errors\n").lstrip()
                    ).encode()
                },
                branch=str(config["git"]["branch"]),
                actor="process:memory-worker",
                model=None,
                session_id=session if descriptor else None,
                summary="Record sanitized lifecycle failure",
                subject="memory(process): record lifecycle failure",
                concept_ids=(),
            )
    except Exception:
        pass


def _notify_failure(
    state_dir: Path, descriptor: Mapping[str, Any] | None, record: Mapping[str, Any]
) -> None:
    session_id = descriptor["session"]["session_id"] if descriptor else "unknown"
    agent = descriptor["session"]["agent"] if descriptor else "unknown"
    target = queue_paths(state_dir, create=True).notifications / f"{record['retry_id']}.json"
    notification = {
        "schema": "agent-memory.notification/v1",
        "retry_id": record["retry_id"],
        "agent": agent,
        "session_id": session_id,
        "severity": "error",
        "message": record["message"],
        "created_at": record["failed_at"],
    }
    _write_atomic(target, canonical_json(notification) + b"\n")


def drain_once(
    vault: Path,
    config: Mapping[str, Any],
    *,
    sleep: Callable[[float], None] = time.sleep,
    fault_hook: Callable[[str], None] | None = None,
    materializer: Callable[..., MaterializationResult] = materialize_descriptor,
) -> dict[str, int]:
    """Drain claimed before ready under one non-waiting worker lock."""

    state_dir = Path(config["worker"]["state_dir"])
    paths = queue_paths(state_dir, create=True)
    counts = {"processed": 0, "failed": 0, "noop": 0}
    with writer_lock(
        paths.lock,
        timeout=0,
        command="worker --once",
        actor="process:memory-worker",
    ):
        while True:
            claimed = sorted(paths.claimed.glob("*.json"), key=_order)
            if claimed:
                path = claimed[0]
            else:
                ready = sorted(paths.ready.glob("*.json"), key=_order)
                if not ready:
                    break
                path = claim_descriptor(paths, ready[0])
                if fault_hook:
                    fault_hook("after_claim")
            try:
                descriptor = read_descriptor(path)
            except Exception:
                record = _failed_record(
                    None, message="invalid lifecycle descriptor", attempts=0, retryable=False
                )
                _store_failed(path, record)
                _notify_failure(state_dir, None, record)
                _record_error(vault, config, None, record)
                counts["failed"] += 1
                continue

            last_error: Exception | None = None
            result: MaterializationResult | None = None
            attempts = 0
            for attempts in range(1, ATTEMPTS + 1):
                try:
                    result = materializer(vault, config, descriptor)
                    last_error = None
                    break
                except Exception as exc:
                    last_error = exc
                    if not _retryable(exc) or attempts == ATTEMPTS:
                        break
                    sleep(BACKOFF[attempts - 1])
            if last_error is not None:
                can_retry = _retryable(last_error)
                record = _failed_record(
                    descriptor,
                    message=type(last_error).__name__,
                    attempts=attempts,
                    retryable=can_retry,
                )
                _store_failed(path, record)
                _notify_failure(state_dir, descriptor, record)
                _record_error(vault, config, descriptor, record)
                counts["failed"] += 1
                continue
            if fault_hook:
                fault_hook("after_materialization_before_delete")
            delete_fsynced(path)
            _delete_matching_failed(path, descriptor)
            counts["processed"] += 1
            if result is not None and not result.changed:
                counts["noop"] += 1
    return counts


def retry_failed(
    state_dir: str | Path,
    *,
    retry_id: str | None = None,
    all_failed: bool = False,
    fault_hook: Callable[[str], None] | None = None,
) -> tuple[str, ...]:
    """Atomically republish eligible failed work; no timer or delayed-ready state exists."""

    if all_failed == bool(retry_id):
        raise WorkerError("specify exactly one retry ID or --all")
    paths = queue_paths(state_dir, create=True)
    republished: list[str] = []
    with writer_lock(
        paths.lock,
        timeout=0,
        command="retry",
        actor="process:memory-cli",
    ):
        for path in sorted(paths.failed.glob("*.json")):
            try:
                record = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if not isinstance(record, dict) or (
                not all_failed and record.get("retry_id") != retry_id
            ):
                continue
            if record.get("retry_state") not in {"exhausted", "blocked"}:
                continue
            descriptor = record.get("descriptor")
            try:
                value = validate_descriptor(descriptor)
            except (TypeError, ValueError):
                continue
            target = paths.ready / descriptor_filename(value["event_id"])
            payload = canonical_json(value) + b"\n"
            with _publication_lock(paths.root):
                if target.exists() and target.read_bytes() != payload:
                    raise WorkerError("ready descriptor conflicts with failed retry")
                if not target.exists():
                    _publish_payload(paths.root, target, payload)
                if fault_hook:
                    fault_hook("after_publish_before_failed_delete")
                path.unlink()
                _fsync_directory(paths.failed)
            republished.append(str(record["retry_id"]))
    return tuple(republished)
