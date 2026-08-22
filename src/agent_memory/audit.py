"""Durable per-session access audit spooling outside the synchronized vault."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from agent_memory.config import validate_local_state_dir
from agent_memory.secrets import contains_secret, redact_sensitive_text


class AuditError(ValueError):
    """Raised when explicit retrieval context is absent or invalid."""


MAX_AUDIT_INTERVAL = 1024 * 1024
MAX_AUDIT_RECORD = 64 * 1024
MAX_TEXT = 4096
MAX_CONCEPTS = 100


@dataclass(frozen=True)
class RetrievalContext:
    session_id: str
    agent: str
    model: str

    def __post_init__(self) -> None:
        for name, value in (
            ("session ID", self.session_id),
            ("agent", self.agent),
            ("model", self.model),
        ):
            if not isinstance(value, str) or not value.strip() or "\x00" in value:
                raise AuditError(f"{name} must be a non-empty string")
            if contains_secret(value):
                raise AuditError(f"{name} contains sensitive content")
        if (
            self.model.strip() != self.model
            or "/" not in self.model
            or any(not part for part in self.model.split("/"))
        ):
            raise AuditError("model must be an exact provider/model identifier")


def spool_path(state_dir: str | Path, session_id: str) -> Path:
    digest = hashlib.sha256(session_id.encode("utf-8")).hexdigest()
    return Path(state_dir) / "audit" / f"{digest}.jsonl"


def append_access_event(
    state_dir: str | Path,
    context: RetrievalContext,
    *,
    mode: str,
    concepts: list[str],
    query: str | None = None,
    reason: str | None = None,
    resource: str | None = None,
    timestamp: str | None = None,
) -> Path:
    """Append and fsync one complete locked JSONL record."""

    if mode not in {"injected", "search", "show"}:
        raise AuditError(f"unsupported access mode: {mode}")
    event: dict[str, Any] = {
        "timestamp": timestamp or datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "mode": mode,
        "agent": context.agent,
        "model": context.model,
        "session_id": context.session_id,
        "event_id": str(uuid.uuid4()),
        "query": redact_sensitive_text(query, limit=MAX_TEXT) if query is not None else None,
        "reason": redact_sensitive_text(reason, limit=MAX_TEXT) if reason is not None else None,
        "concepts": concepts,
    }
    if resource is not None:
        event["resource"] = redact_sensitive_text(resource, limit=300)
    event = _validate_access_record(
        event,
        session_id=context.session_id,
        raw=b"",
        position=0,
    )
    payload = (
        json.dumps(event, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode("utf-8")
    validate_local_state_dir(state_dir)
    path = spool_path(state_dir, context.session_id)
    parent_existed = path.parent.exists()
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(path.parent, 0o700)
    existed = path.exists()
    descriptor = os.open(path, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        remaining = memoryview(payload)
        while remaining:
            written = os.write(descriptor, remaining)
            if written == 0:
                raise OSError("audit append wrote zero bytes")
            remaining = remaining[written:]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    if not existed:
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    if not parent_existed:
        directory = os.open(path.parent.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    return path


def capture_offset(state_dir: str | Path, session_id: str) -> int:
    """Return the complete fsynced JSONL boundary while excluding concurrent appends."""

    path = spool_path(state_dir, session_id)
    if not path.exists():
        return 0
    descriptor = os.open(path, os.O_RDONLY)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_SH)
        return os.fstat(descriptor).st_size
    finally:
        os.close(descriptor)


def _validate_access_record(
    value: object, *, session_id: str, raw: bytes, position: int
) -> dict[str, Any]:
    required = {
        "timestamp",
        "mode",
        "agent",
        "model",
        "session_id",
        "query",
        "reason",
        "concepts",
    }
    if (
        not isinstance(value, dict)
        or not required <= set(value)
        or (set(value) - required) - {"event_id", "resource"}
    ):
        raise AuditError("audit spool contains an invalid event")
    if value["session_id"] != session_id or value["mode"] not in {"injected", "search", "show"}:
        raise AuditError("audit event session or mode is invalid")
    for key in ("timestamp", "agent", "model", "session_id"):
        item = value[key]
        if not isinstance(item, str) or not item or len(item) > MAX_TEXT or "\x00" in item:
            raise AuditError(f"audit event {key} is invalid")
    try:
        parsed = datetime.fromisoformat(value["timestamp"].replace("Z", "+00:00"))
    except ValueError as exc:
        raise AuditError("audit event timestamp is invalid") from exc
    if parsed.tzinfo is None:
        raise AuditError("audit event timestamp is invalid")
    RetrievalContext(value["session_id"], value["agent"], value["model"])
    for key in ("query", "reason"):
        item = value[key]
        if item is not None and (
            not isinstance(item, str) or len(item) > MAX_TEXT or "\x00" in item
        ):
            raise AuditError(f"audit event {key} is invalid")
        if item is not None:
            value[key] = redact_sensitive_text(item, limit=MAX_TEXT)
    resource = value.get("resource")
    if resource is not None and (
        not isinstance(resource, str) or not resource or len(resource) > 300 or "\x00" in resource
    ):
        raise AuditError("audit event resource is invalid")
    if resource is not None:
        value["resource"] = redact_sensitive_text(resource, limit=300)
    concepts = value["concepts"]
    if (
        not isinstance(concepts, list)
        or len(concepts) > MAX_CONCEPTS
        or any(
            not isinstance(item, str) or not item or len(item) > 300 or "\x00" in item
            for item in concepts
        )
    ):
        raise AuditError("audit event concepts are invalid")
    value["concepts"] = [redact_sensitive_text(item, limit=300) for item in concepts]
    event_id = value.get("event_id")
    if event_id is None:
        digest = hashlib.sha256(
            b"agent-memory.audit-legacy/v1\0"
            + session_id.encode()
            + b"\0"
            + str(position).encode()
            + b"\0"
            + raw
        ).hexdigest()
        value["event_id"] = f"legacy:{digest}"
    elif (
        not isinstance(event_id, str)
        or not event_id
        or len(event_id) > 200
        or any(character in event_id for character in "\r\n\x00")
    ):
        raise AuditError("audit event ID is invalid")
    return value


def read_access_events(
    state_dir: str | Path, session_id: str, *, start: int, end: int
) -> list[dict[str, Any]]:
    """Read only complete records in a publication-bound byte interval."""

    if type(start) is not int or type(end) is not int or start < 0 or end < start:
        raise AuditError("invalid audit byte boundary")
    if end - start > MAX_AUDIT_INTERVAL:
        raise AuditError("audit byte interval exceeds the materialization limit")
    path = spool_path(state_dir, session_id)
    if end == start or not path.exists():
        return []
    with path.open("rb") as file:
        fcntl.flock(file.fileno(), fcntl.LOCK_SH)
        if end > os.fstat(file.fileno()).st_size:
            raise AuditError("audit spool is shorter than its published boundary")
        file.seek(start)
        payload = file.read(end - start)
    if payload and not payload.endswith(b"\n"):
        raise AuditError("audit boundary does not end at a complete record")
    records: list[dict[str, Any]] = []
    position = start
    for raw_line in payload.splitlines(keepends=True):
        if len(raw_line) > MAX_AUDIT_RECORD:
            raise AuditError("audit spool record exceeds the materialization limit")
        line = raw_line.removesuffix(b"\n")
        try:
            value = json.loads(line)
        except ValueError as exc:
            raise AuditError("audit spool contains malformed JSON") from exc
        records.append(
            _validate_access_record(value, session_id=session_id, raw=line, position=position)
        )
        position += len(raw_line)
    return records


def cursor_path(state_dir: str | Path, session_id: str) -> Path:
    digest = hashlib.sha256(session_id.encode("utf-8")).hexdigest()
    return Path(state_dir) / "audit-cursors" / f"{digest}.json"


def read_cursor(state_dir: str | Path, session_id: str) -> int:
    path = cursor_path(state_dir, session_id)
    if not path.exists():
        return 0
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        offset = value["offset"]
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise AuditError("audit cursor is invalid") from exc
    if type(offset) is not int or offset < 0:
        raise AuditError("audit cursor offset is invalid")
    return offset


def advance_cursor(state_dir: str | Path, session_id: str, offset: int) -> None:
    """Atomically acknowledge a materialized audit boundary after its Git commit."""

    path = cursor_path(state_dir, session_id)
    parent_existed = path.parent.exists()
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(path.parent, 0o700)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    descriptor = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        payload = json.dumps({"offset": offset}, separators=(",", ":")).encode() + b"\n"
        remaining = memoryview(payload)
        while remaining:
            written = os.write(descriptor, remaining)
            if not written:
                raise OSError("audit cursor write wrote zero bytes")
            remaining = remaining[written:]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    os.replace(temporary, path)
    directory = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)
    if not parent_existed:
        directory = os.open(path.parent.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
