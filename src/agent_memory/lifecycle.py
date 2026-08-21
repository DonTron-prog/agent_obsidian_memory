"""Durable sanitized lifecycle descriptor contracts and queue operations."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import stat
import time
import uuid
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from agent_memory.audit import capture_offset
from agent_memory.config import validate_local_state_dir
from agent_memory.secrets import contains_secret

SCHEMA = "agent-memory.lifecycle/v1"
UNAVAILABLE = "native summary unavailable"
SESSION_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,199}$")
EVENT_ID = re.compile(r"^(?:session_start|checkpoint|finalize):v1:[0-9a-f]{64}$")
HEX = re.compile(r"^[0-9a-f]{64}$")
MODEL = re.compile(r"^[^/\s]+(?:/[^/\s]+)+$")


class LifecycleError(ValueError):
    """Raised before unsafe or malformed lifecycle state is persisted."""


@dataclass(frozen=True)
class QueuePaths:
    root: Path
    ready: Path
    claimed: Path
    failed: Path
    notifications: Path
    lock: Path


def queue_paths(state_dir: str | Path, *, create: bool = False) -> QueuePaths:
    root = validate_local_state_dir(state_dir)
    paths = QueuePaths(
        root=root,
        ready=root / "ready",
        claimed=root / "claimed",
        failed=root / "failed",
        notifications=root / "notifications",
        lock=root / "worker.lock",
    )
    if create:
        for path in (root, paths.ready, paths.claimed, paths.failed, paths.notifications):
            existed = path.exists()
            path.mkdir(parents=True, exist_ok=True, mode=0o700)
            os.chmod(path, 0o700)
            if not existed:
                _fsync_directory(path)
                _fsync_directory(path.parent)
    return paths


def canonical_json(value: Mapping[str, Any]) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def _hash(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical_json(value)).hexdigest()


def _nonempty(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip() or "\x00" in value:
        raise LifecycleError(f"{name} must be non-empty text")
    return value


def _safe_id(value: object, name: str) -> str:
    text = _nonempty(value, name)
    if not SESSION_ID.fullmatch(text):
        raise LifecycleError(f"{name} is unsafe")
    return text


def _exact_int(value: object, name: str, *, minimum: int = 0) -> int:
    if type(value) is not int or value < minimum:
        raise LifecycleError(f"{name} must be an integer >= {minimum}")
    return value


def _utc(value: object, name: str) -> str:
    text = _nonempty(value, name)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise LifecycleError(f"{name} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise LifecycleError(f"{name} must include a timezone")
    return text


def _identity(descriptor: Mapping[str, Any]) -> dict[str, Any]:
    session = descriptor["session"]
    source = descriptor["summary_source"]
    kind = source["kind"]
    if kind == "pi":
        return {
            "schema": SCHEMA,
            "agent": session["agent"],
            "session_id": session["session_id"],
            "compaction_entry_id": source["compaction_entry_id"],
        }
    if kind == "hermes-0.20.0":
        return {
            "schema": "agent-memory.hermes-compression-identity/v1",
            **{
                key: source[key]
                for key in (
                    "platform",
                    "session_id",
                    "old_session_id",
                    "in_place",
                    "compression_count",
                    "previous_message_row_id",
                    "current_message_row_id",
                    "candidate_row_id",
                    "candidate_summary_sha256",
                )
            },
        }
    lifecycle = descriptor["lifecycle"]
    return {
        "schema": SCHEMA,
        "agent": session["agent"],
        "session_id": session["session_id"],
        "event_kind": descriptor["event_kind"],
        "trigger": lifecycle["trigger"],
        "native_event_id": lifecycle.get("native_event_id"),
    }


def validate_descriptor(value: object) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise LifecycleError("descriptor must be a mapping")
    allowed = {
        "schema",
        "event_id",
        "event_kind",
        "session",
        "lifecycle",
        "host",
        "audit_through_offset",
        "summary_source",
    }
    if set(value) != allowed or value.get("schema") != SCHEMA:
        raise LifecycleError("descriptor fields or schema are invalid")
    event_kind = value.get("event_kind")
    if event_kind not in {"session_start", "checkpoint", "finalize"}:
        raise LifecycleError("event_kind is invalid")
    session = value.get("session")
    if (
        not isinstance(session, dict)
        or not set(session)
        <= {
            "agent",
            "agent_version",
            "session_id",
            "started_at",
            "native_store_ref",
        }
        or not {"agent", "agent_version", "session_id", "started_at"} <= set(session)
    ):
        raise LifecycleError("session fields are invalid")
    if session["agent"] not in {"pi", "hermes"}:
        raise LifecycleError("session agent must be pi or hermes")
    _safe_id(session["agent_version"], "agent_version")
    _safe_id(session["session_id"], "session_id")
    _utc(session["started_at"], "started_at")
    if "native_store_ref" in session:
        native_store_ref = _nonempty(session["native_store_ref"], "native_store_ref")
        if any(character in native_store_ref for character in "\r\n"):
            raise LifecycleError("native_store_ref contains a line break")
    lifecycle = value.get("lifecycle")
    if (
        not isinstance(lifecycle, dict)
        or not set(lifecycle)
        <= {
            "trigger",
            "occurred_at",
            "native_event_id",
        }
        or not {"trigger", "occurred_at"} <= set(lifecycle)
    ):
        raise LifecycleError("lifecycle fields are invalid")
    if lifecycle["trigger"] not in {
        "start",
        "compaction",
        "compression",
        "reset",
        "new",
        "finalization",
    }:
        raise LifecycleError("lifecycle trigger is invalid")
    _utc(lifecycle["occurred_at"], "occurred_at")
    if "native_event_id" in lifecycle:
        _safe_id(lifecycle["native_event_id"], "native_event_id")
    host = value.get("host")
    if not isinstance(host, dict) or not set(host) <= {"model", "platform"}:
        raise LifecycleError("host fields are invalid")
    if "platform" in host:
        _safe_id(host["platform"], "host.platform")
    if "model" in host:
        model = _nonempty(host["model"], "host.model")
        if not MODEL.fullmatch(model):
            raise LifecycleError("host.model must be an exact provider/model identifier")
    offset = value.get("audit_through_offset")
    _exact_int(offset, "audit_through_offset")
    source = value.get("summary_source")
    if not isinstance(source, dict) or source.get("kind") not in {
        "pi",
        "hermes-0.20.0",
        "unavailable",
    }:
        raise LifecycleError("summary_source is invalid")
    if source["kind"] == "pi":
        if set(source) != {"kind", "compaction_entry_id", "summary"}:
            raise LifecycleError("Pi summary fields are invalid")
        _safe_id(source["compaction_entry_id"], "compaction_entry_id")
        if not isinstance(source["summary"], str) or not source["summary"].strip():
            raise LifecycleError("Pi native summary must be non-empty")
    elif source["kind"] == "hermes-0.20.0":
        required = {
            "kind",
            "platform",
            "session_id",
            "old_session_id",
            "in_place",
            "compression_count",
            "previous_message_row_id",
            "current_message_row_id",
            "candidate_row_id",
            "candidate_summary_sha256",
        }
        if set(source) != required:
            raise LifecycleError("Hermes summary fields are invalid")
        _safe_id(source["platform"], "Hermes platform")
        _safe_id(source["session_id"], "Hermes session_id")
        if source["old_session_id"] is not None:
            _safe_id(source["old_session_id"], "Hermes old_session_id")
        if type(source["in_place"]) is not bool:
            raise LifecycleError("Hermes in_place must be a boolean")
        _exact_int(source["compression_count"], "Hermes compression_count")
        previous = source["previous_message_row_id"]
        current = source["current_message_row_id"]
        candidate = source["candidate_row_id"]
        digest = source["candidate_summary_sha256"]
        _exact_int(previous, "Hermes previous_message_row_id")
        _exact_int(current, "Hermes current_message_row_id")
        if current < previous or (candidate is None) != (digest is None):
            raise LifecycleError("Hermes candidate identity is invalid")
        if candidate is not None and (
            type(candidate) is not int
            or not previous < candidate <= current
            or not isinstance(digest, str)
            or not HEX.fullmatch(digest)
        ):
            raise LifecycleError("Hermes candidate is outside its boundary or hash is invalid")
    else:
        if set(source) != {"kind"}:
            raise LifecycleError("unavailable summary fields are invalid")

    trigger = lifecycle["trigger"]
    expected_trigger = {
        "session_start": {"start"},
        "checkpoint": {"compaction", "compression", "reset", "new"},
        "finalize": {"finalization"},
    }[event_kind]
    if trigger not in expected_trigger:
        raise LifecycleError("event_kind and trigger are inconsistent")
    kind = source["kind"]
    if kind == "pi" and not (
        session["agent"] == "pi" and event_kind == "checkpoint" and trigger == "compaction"
    ):
        raise LifecycleError("Pi summary source is inconsistent with the lifecycle event")
    if kind == "hermes-0.20.0":
        if not (
            session["agent"] == "hermes" and event_kind == "checkpoint" and trigger == "compression"
        ):
            raise LifecycleError("Hermes summary source is inconsistent with the lifecycle event")
        if host.get("platform") != source["platform"]:
            raise LifecycleError("Hermes source platform must match host.platform")
        if source["in_place"]:
            if session["session_id"] != source["session_id"] or source["old_session_id"] not in {
                None,
                source["session_id"],
            }:
                raise LifecycleError("in-place Hermes source lineage is invalid")
        elif source["old_session_id"] is None or source["old_session_id"] == source["session_id"]:
            raise LifecycleError("rotated Hermes source lineage is invalid")
    if kind == "unavailable":
        if event_kind != "session_start" and "native_event_id" not in lifecycle:
            raise LifecycleError("unavailable non-start events require native_event_id")
        if trigger == "compaction":
            raise LifecycleError("Pi compaction requires its native summary source")
        if trigger == "compression" and session["agent"] != "hermes":
            raise LifecycleError("compression is valid only for Hermes")
    expected = f"{event_kind}:v1:{_hash(_identity(value))}"
    if value.get("event_id") != expected or not EVENT_ID.fullmatch(expected):
        raise LifecycleError("event_id does not match the canonical identity")
    if contains_secret(canonical_json(value).decode()):
        raise LifecycleError("descriptor contains sensitive content")
    return value


def build_descriptor(
    *,
    event_kind: str,
    agent: str,
    agent_version: str,
    session_id: str,
    started_at: str,
    trigger: str,
    occurred_at: str,
    state_dir: str | Path,
    summary_source: Mapping[str, Any] | None = None,
    native_event_id: str | None = None,
    model: str | None = None,
    platform: str | None = None,
    native_store_ref: str | None = None,
) -> dict[str, Any]:
    session = {
        "agent": agent,
        "agent_version": agent_version,
        "session_id": session_id,
        "started_at": started_at,
    }
    if native_store_ref:
        session["native_store_ref"] = native_store_ref
    lifecycle = {"trigger": trigger, "occurred_at": occurred_at}
    if native_event_id:
        lifecycle["native_event_id"] = native_event_id
    host = {key: value for key, value in {"model": model, "platform": platform}.items() if value}
    value: dict[str, Any] = {
        "schema": SCHEMA,
        "event_id": "",
        "event_kind": event_kind,
        "session": session,
        "lifecycle": lifecycle,
        "host": host,
        "audit_through_offset": capture_offset(state_dir, session_id),
        "summary_source": dict(summary_source or {"kind": "unavailable"}),
    }
    value["event_id"] = f"{event_kind}:v1:{_hash(_identity(value))}"
    return validate_descriptor(value)


def descriptor_filename(event_id: str) -> str:
    return f"{hashlib.sha256(event_id.encode()).hexdigest()}.json"


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _write_atomic(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(path.parent, 0o700)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    descriptor = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        view = memoryview(payload)
        while view:
            written = os.write(descriptor, view)
            if not written:
                raise OSError("atomic lifecycle write wrote zero bytes")
            view = view[written:]
        os.fsync(descriptor)
        os.replace(temporary, path)
        os.chmod(path, 0o600)
        _fsync_directory(path.parent)
    finally:
        os.close(descriptor)
        temporary.unlink(missing_ok=True)


@contextmanager
def _publication_lock(root: Path, *, timeout_ms: int | None = None) -> Iterator[None]:
    # ponytail: one global lock is the MVP ceiling; restore finer locks if throughput matters.
    descriptor = os.open(
        root / "publication.lock",
        os.O_CREAT | os.O_RDWR | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    acquired = False
    try:
        if timeout_ms is None:
            fcntl.flock(descriptor, fcntl.LOCK_EX)
        else:
            if type(timeout_ms) is not int or timeout_ms < 0:
                raise ValueError("publication timeout must be a non-negative integer")
            deadline = time.monotonic() + timeout_ms / 1000
            while True:
                try:
                    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise TimeoutError("lifecycle publication lock timed out") from None
                    time.sleep(min(0.01, remaining))
        acquired = True
        yield
    finally:
        if acquired:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def _publish_payload(root: Path, target: Path, payload: bytes) -> None:
    """Link a durable temp inode into a watched queue without clobbering."""

    temporary_dir = root / "publication-tmp"
    temporary_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(temporary_dir, 0o700)
    temporary = temporary_dir / f"{uuid.uuid4().hex}.tmp"
    descriptor = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        view = memoryview(payload)
        while view:
            written = os.write(descriptor, view)
            if not written:
                raise OSError("lifecycle publication wrote zero bytes")
            view = view[written:]
        os.fsync(descriptor)
        try:
            os.link(temporary, target)
        except FileExistsError:
            if target.read_bytes() != payload:
                raise LifecycleError("event identity already exists with different content")
        else:
            _fsync_directory(target.parent)
    finally:
        os.close(descriptor)
        temporary.unlink(missing_ok=True)


def read_descriptor(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 1024 * 1024:
        raise LifecycleError("queue entry is not a safe descriptor file")
    if stat.S_IMODE(path.stat().st_mode) & 0o077:
        raise LifecycleError("queue descriptor permissions are too broad")
    try:
        return validate_descriptor(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError) as exc:
        if isinstance(exc, LifecycleError):
            raise
        raise LifecycleError("queue descriptor is malformed") from exc


def publish_descriptor(
    state_dir: str | Path,
    descriptor: Mapping[str, Any],
    *,
    timeout_ms: int = 250,
) -> Path:
    value = validate_descriptor(dict(descriptor))
    paths = queue_paths(state_dir, create=True)
    name = descriptor_filename(value["event_id"])
    payload = canonical_json(value) + b"\n"
    with _publication_lock(paths.root, timeout_ms=timeout_ms):
        for directory in (paths.ready, paths.claimed, paths.failed):
            existing = directory / name
            if existing.exists():
                if directory == paths.failed:
                    try:
                        failed = json.loads(existing.read_text(encoding="utf-8"))
                        current = failed.get("descriptor")
                    except (OSError, ValueError):
                        current = None
                else:
                    current = read_descriptor(existing)
                    if existing.read_bytes() != payload:
                        raise LifecycleError("event identity already exists with different content")
                if current == value:
                    return existing
                raise LifecycleError("event identity already exists with different content")
        target = paths.ready / name
        _publish_payload(paths.root, target, payload)
        return target


def claim_descriptor(paths: QueuePaths, ready: Path) -> Path:
    target = paths.claimed / ready.name
    with _publication_lock(paths.root):
        if target.exists():
            if read_descriptor(target) != read_descriptor(ready):
                raise LifecycleError("claimed event conflicts with ready event")
            ready.unlink()
            _fsync_directory(paths.ready)
            return target
        os.replace(ready, target)
        _fsync_directory(paths.ready)
        _fsync_directory(paths.claimed)
        return target


def delete_fsynced(path: Path) -> None:
    parent = path.parent
    if parent.name in {"ready", "claimed", "failed"} and path.suffix == ".json":
        with _publication_lock(parent.parent):
            path.unlink(missing_ok=True)
            _fsync_directory(parent)
    else:
        path.unlink(missing_ok=True)
        _fsync_directory(parent)


def now_utc() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")
