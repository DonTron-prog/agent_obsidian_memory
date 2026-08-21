"""Native Linux advisory lock used by every managed vault write."""

from __future__ import annotations

import fcntl
import json
import os
import stat
import time
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import TextIO


class LockTimeoutError(TimeoutError):
    """Raised with current owner metadata when the writer lock times out."""


def _alive(pid: object) -> bool:
    if not isinstance(pid, int) or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _owner(file: TextIO) -> dict[str, object]:
    try:
        file.seek(0)
        value = json.loads(file.read() or "{}")
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


def _open_lock(path: Path) -> TextIO:
    path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_RDWR | os.O_CREAT | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags, 0o600)
    try:
        details = os.fstat(descriptor)
        if not stat.S_ISREG(details.st_mode):
            raise OSError(f"writer lock is not a regular file: {path}")
        os.fchmod(descriptor, 0o600)
        return os.fdopen(descriptor, "r+", encoding="utf-8")
    except BaseException:
        os.close(descriptor)
        raise


@contextmanager
def writer_lock(
    path: Path,
    *,
    timeout: float,
    command: str,
    actor: str,
) -> Iterator[None]:
    """Acquire one no-follow flock and publish owner metadata on its inode."""

    with _open_lock(path) as lock:
        deadline = time.monotonic() + timeout
        while True:
            try:
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    owner = _owner(lock)
                    state = "live" if _alive(owner.get("pid")) else "stale metadata"
                    raise LockTimeoutError(
                        f"writer lock timed out; owner ({state}): "
                        f"{json.dumps(owner, sort_keys=True)}"
                    ) from None
                time.sleep(min(0.05, max(0.0, deadline - time.monotonic())))

        metadata = {
            "pid": os.getpid(),
            "command": command,
            "actor": actor,
            "acquired_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        }
        lock.seek(0)
        lock.truncate()
        json.dump(metadata, lock, sort_keys=True)
        lock.flush()
        os.fsync(lock.fileno())
        try:
            yield
        finally:
            lock.seek(0)
            lock.truncate()
            lock.flush()
            os.fsync(lock.fileno())
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
