"""Durable per-session access audit spooling outside the synchronized vault."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


class AuditError(ValueError):
    """Raised when explicit retrieval context is absent or invalid."""


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
        "query": query,
        "reason": reason,
        "concepts": concepts,
    }
    payload = (
        json.dumps(event, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode("utf-8")
    path = spool_path(state_dir, context.session_id)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
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
    return path
