"""Hermes 0.20.0 adapter state, bounded compression publication, and health checks."""

from __future__ import annotations

import ast
import fcntl
import hashlib
import hmac
import json
import os
import re
import shutil
import sqlite3
import subprocess
import time
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from agent_memory.audit import RetrievalContext, append_access_event
from agent_memory.config import validate_local_state_dir
from agent_memory.hermes import isolate_summary
from agent_memory.lifecycle import (
    MODEL,
    SESSION_ID,
    _write_atomic,
    build_descriptor,
    canonical_json,
    now_utc,
    publish_descriptor,
)
from agent_memory.secrets import contains_secret

HERMES_VERSION = "0.20.0"
HERMES_BUILD = "2026.8.3"
STATE_SCHEMA = "agent-memory.hermes-adapter/v1"
PLUGIN_CONTRACT = re.compile(r'HERMES_COMPAT_VERSION\s*=\s*["\']([^"\']+)["\']')
Runner = Callable[[Sequence[str]], subprocess.CompletedProcess[str]]


class HermesAdapterError(ValueError):
    """Raised when Hermes adapter state or a host payload is unsafe."""


def _run(argv: Sequence[str]) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(argv, text=True, capture_output=True, check=False, timeout=2)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return subprocess.CompletedProcess(argv, 1, "", type(exc).__name__)


def _adapter_paths(state_dir: str | Path) -> tuple[Path, Path]:
    root = validate_local_state_dir(state_dir) / "adapter-state"
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(root, 0o700)
    return root / "hermes-v0.20.0.json", root / "hermes-v0.20.0.lock"


@contextmanager
def _state_lock(state_dir: str | Path, timeout_ms: int) -> Iterator[Path]:
    state_path, lock_path = _adapter_paths(state_dir)
    descriptor = os.open(
        lock_path,
        os.O_CREAT | os.O_RDWR | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    acquired = False
    try:
        deadline = time.monotonic() + timeout_ms / 1000
        while True:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
                break
            except BlockingIOError:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("Hermes adapter state lock timed out") from None
                time.sleep(min(0.01, remaining))
        yield state_path
    finally:
        if acquired:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def _empty_state() -> dict[str, Any]:
    return {"schema": STATE_SCHEMA, "sessions": {}}


def _load_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return _empty_state()
    if path.is_symlink() or path.stat().st_size > 1024 * 1024:
        raise HermesAdapterError("Hermes adapter state file is unsafe")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise HermesAdapterError("Hermes adapter state is malformed") from exc
    if (
        not isinstance(value, dict)
        or value.get("schema") != STATE_SCHEMA
        or not isinstance(value.get("sessions"), dict)
    ):
        raise HermesAdapterError("Hermes adapter state schema is invalid")
    return value


def _save_state(path: Path, state: Mapping[str, Any]) -> None:
    payload = canonical_json(state) + b"\n"
    if contains_secret(payload.decode()):
        raise HermesAdapterError("Hermes adapter state contains sensitive content")
    _write_atomic(path, payload)


def _safe_session(value: object, name: str = "Hermes session ID") -> str:
    if not isinstance(value, str) or not SESSION_ID.fullmatch(value):
        raise HermesAdapterError(f"{name} is unsafe")
    return value


def _safe_platform(value: object) -> str:
    if not isinstance(value, str) or not SESSION_ID.fullmatch(value):
        raise HermesAdapterError("Hermes platform is unsafe")
    return value


def _safe_model(value: object) -> str:
    if not isinstance(value, str) or not MODEL.fullmatch(value):
        raise HermesAdapterError("Hermes model is not an exact provider/model identifier")
    return value


def _safe_chat_type(value: object) -> str:
    if value in {None, ""}:
        return ""
    if value not in {"dm", "group", "channel", "thread", "forum"}:
        raise HermesAdapterError("Hermes chat type is invalid")
    return str(value)


def _sender_digest(value: object) -> str | None:
    if value in {None, ""}:
        return None
    if not isinstance(value, str) or len(value) > 300 or any(c in value for c in "\r\n\x00"):
        raise HermesAdapterError("Hermes sender identity is invalid")
    return hashlib.sha256(value.encode()).hexdigest()


def _connect(database: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True)
    columns = {row[1] for row in connection.execute("PRAGMA table_info(messages)").fetchall()}
    required = {"id", "session_id", "role", "content", "active", "compacted"}
    if not required <= columns:
        connection.close()
        raise HermesAdapterError("Hermes state database message schema is incompatible")
    return connection


def _host_metadata(
    database: Path, session_id: str
) -> tuple[str | None, str | None, str | None, str | None, str]:
    """Return reliable persisted model, start, and sanitized origin binding."""

    try:
        connection = _connect(database)
        try:
            columns = {row[1] for row in connection.execute("PRAGMA table_info(sessions)")}
            required = {
                "id",
                "model",
                "billing_provider",
                "started_at",
                "model_config",
                "source",
                "user_id",
                "chat_type",
            }
            if not required <= columns:
                return None, None, None, None, ""
            row = connection.execute(
                "SELECT model, billing_provider, started_at, model_config, "
                "source, user_id, chat_type FROM sessions WHERE id = ?",
                (session_id,),
            ).fetchone()
        finally:
            connection.close()
    except (OSError, sqlite3.Error, TypeError, ValueError) as exc:
        if isinstance(exc, HermesAdapterError):
            raise
        raise HermesAdapterError("Hermes session metadata is unavailable") from exc
    if row is None:
        return None, None, None, None, ""
    model, provider, started_at, model_config, platform, user_id, chat_type = row
    if not isinstance(provider, str) or not provider.strip():
        try:
            parsed_config = json.loads(model_config) if isinstance(model_config, str) else {}
            gateway_runtime = parsed_config.get("gateway_runtime", {})
            provider = gateway_runtime.get("provider")
        except (AttributeError, TypeError, ValueError):
            provider = None
    exact_model = None
    if isinstance(model, str) and model.strip():
        if isinstance(provider, str) and provider.strip():
            exact_model = _safe_model(f"{provider.strip()}/{model.strip()}")
        elif MODEL.fullmatch(model.strip()):
            exact_model = model.strip()
    started = None
    if isinstance(started_at, int | float):
        try:
            started = datetime.fromtimestamp(started_at, UTC).isoformat().replace("+00:00", "Z")
        except (OverflowError, OSError, ValueError):
            pass
    host_platform = _safe_platform(platform) if isinstance(platform, str) and platform else None
    host_sender = _sender_digest(user_id)
    return exact_model, started, host_platform, host_sender, _safe_chat_type(chat_type)


def _high_water(database: Path, sessions: tuple[str, ...]) -> int:
    placeholders = ",".join("?" for _ in sessions)
    try:
        connection = _connect(database)
        try:
            return int(
                connection.execute(
                    f"SELECT COALESCE(MAX(id), 0) FROM messages "
                    f"WHERE session_id IN ({placeholders})",
                    sessions,
                ).fetchone()[0]
            )
        finally:
            connection.close()
    except (OSError, sqlite3.Error, TypeError, ValueError) as exc:
        if isinstance(exc, HermesAdapterError):
            raise
        raise HermesAdapterError("Hermes state database is unavailable") from exc


def _candidates(
    database: Path,
    *,
    sessions: tuple[str, ...],
    previous: int,
    current: int,
) -> list[tuple[int, str]]:
    placeholders = ",".join("?" for _ in sessions)
    try:
        connection = _connect(database)
        try:
            rows = connection.execute(
                "SELECT id, role, content, active, compacted FROM messages "
                f"WHERE id > ? AND id <= ? AND session_id IN ({placeholders}) ORDER BY id",
                (previous, current, *sessions),
            ).fetchall()
        finally:
            connection.close()
    except (OSError, sqlite3.Error, TypeError, ValueError) as exc:
        if isinstance(exc, HermesAdapterError):
            raise
        raise HermesAdapterError("Hermes bounded message query failed") from exc
    found: list[tuple[int, str]] = []
    for row_id, role, content, active, compacted in rows:
        body = isolate_summary(content, role=role, active=active, compacted=compacted)
        if body is not None:
            found.append((int(row_id), body))
    return found


def _record(
    *,
    native_session_id: str,
    logical_session_id: str,
    started_at: str,
    platform: str,
    model: str | None,
    message_row_id: int,
    sender_digest: str | None = None,
    chat_type: str = "",
    injected: bool = False,
    start_published: bool = False,
) -> dict[str, Any]:
    return {
        "native_session_id": native_session_id,
        "logical_session_id": logical_session_id,
        "started_at": started_at,
        "platform": platform,
        "model": model,
        "message_row_id": message_row_id,
        "sender_digest": sender_digest,
        "chat_type": chat_type,
        "injected": injected,
        "start_published": start_published,
        "last_compression": None,
        "pending_compression": None,
        "pending_finalizations": {},
        "finalizations": {},
    }


def bind_session(
    vault: Path,
    config: Mapping[str, Any],
    *,
    session_id: str,
    model: str | None,
    platform: str,
    native_store_ref: str | Path,
    sender_id: str | None = None,
    chat_type: str | None = None,
    defer_injection: bool = False,
) -> dict[str, Any]:
    """Bind every pre-LLM call and return the root index only once per native session."""

    native_session = _safe_session(session_id)
    explicit_model = (
        _safe_model(model) if isinstance(model, str) and MODEL.fullmatch(model) else None
    )
    exact_platform = _safe_platform(platform)
    exact_chat_type = _safe_chat_type(chat_type)
    exact_sender_digest = _sender_digest(sender_id)
    database = Path(native_store_ref).expanduser().resolve(strict=False)
    state_dir = config["worker"]["state_dir"]
    timeout = config["worker"]["publish_timeout_ms"]
    with _state_lock(state_dir, timeout) as state_path:
        state = _load_state(state_path)
        sessions = state["sessions"]
        for pending_record in list(sessions.values()):
            if isinstance(pending_record, dict):
                _complete_pending(state_dir, timeout, state_path, state, pending_record)
        record = sessions.get(native_session)
        (
            persisted_model,
            persisted_started_at,
            persisted_platform,
            persisted_sender_digest,
            persisted_chat_type,
        ) = _host_metadata(database, native_session)
        if persisted_platform is not None and persisted_platform != exact_platform:
            raise HermesAdapterError("Hermes platform does not match persisted session context")
        if (
            exact_sender_digest is not None
            and persisted_sender_digest is not None
            and not hmac.compare_digest(exact_sender_digest, persisted_sender_digest)
        ):
            raise HermesAdapterError("Hermes sender does not match persisted session context")
        if exact_chat_type and persisted_chat_type and exact_chat_type != persisted_chat_type:
            raise HermesAdapterError("Hermes chat type does not match persisted session context")
        bound_sender_digest = exact_sender_digest or persisted_sender_digest
        bound_chat_type = exact_chat_type or persisted_chat_type
        existing_model = record.get("model") if isinstance(record, dict) else None
        exact_model = persisted_model or explicit_model or existing_model
        if exact_model is None and not defer_injection:
            raise HermesAdapterError("Hermes model context is unavailable for index injection")
        if not isinstance(record, dict):
            record = _record(
                native_session_id=native_session,
                logical_session_id=native_session,
                started_at=persisted_started_at or now_utc(),
                platform=exact_platform,
                model=exact_model,
                message_row_id=_high_water(database, (native_session,)),
                sender_digest=bound_sender_digest,
                chat_type=bound_chat_type,
            )
            sessions[native_session] = record
            _save_state(state_path, state)
        else:
            if record.get("platform") not in {None, exact_platform}:
                raise HermesAdapterError("Hermes platform conflicts with adapter state")
            previous_sender = record.get("sender_digest")
            if (
                previous_sender is not None
                and bound_sender_digest is not None
                and not hmac.compare_digest(previous_sender, bound_sender_digest)
            ):
                raise HermesAdapterError("Hermes sender conflicts with adapter state")
            previous_chat_type = record.get("chat_type")
            if previous_chat_type and bound_chat_type and previous_chat_type != bound_chat_type:
                raise HermesAdapterError("Hermes chat type conflicts with adapter state")
            record.update(
                {
                    "platform": exact_platform,
                    "sender_digest": bound_sender_digest or previous_sender,
                    "chat_type": bound_chat_type or previous_chat_type or "",
                }
            )
            if exact_model is not None:
                record["model"] = exact_model
            _save_state(state_path, state)

        notification_allowed = exact_platform != "telegram" or bool(
            persisted_platform == "telegram"
            and persisted_chat_type == "dm"
            and exact_sender_digest is not None
            and persisted_sender_digest is not None
            and hmac.compare_digest(exact_sender_digest, persisted_sender_digest)
        )

        if not defer_injection and not record.get("start_published"):
            descriptor = build_descriptor(
                event_kind="session_start",
                agent="hermes",
                agent_version=HERMES_VERSION,
                session_id=record["logical_session_id"],
                started_at=record["started_at"],
                trigger="start",
                occurred_at=record["started_at"],
                state_dir=state_dir,
                model=record.get("model"),
                platform=exact_platform,
                native_store_ref=str(database),
            )
            publish_descriptor(state_dir, descriptor, timeout_ms=timeout)
            record["start_published"] = True
            _save_state(state_path, state)

        payload: dict[str, Any] = {
            "session_id": native_session,
            "logical_session_id": record["logical_session_id"],
            "injected": False,
            "notification_allowed": notification_allowed,
        }
        if defer_injection or record.get("injected"):
            return payload
        content = (vault / "memory/index.md").read_text(encoding="utf-8")
        append_access_event(
            state_dir,
            RetrievalContext(record["logical_session_id"], "hermes-agent", record["model"]),
            mode="injected",
            reason="new session",
            resource="memory/index.md",
            concepts=[],
        )
        record["injected"] = True
        _save_state(state_path, state)
        return {**payload, "injected": True, "content": content}


def _complete_pending(
    state_dir: str | Path,
    timeout: int,
    state_path: Path,
    state: dict[str, Any],
    record: dict[str, Any],
) -> dict[str, Any] | None:
    pending_finalizations = record.setdefault("pending_finalizations", {})
    finalizations = record.setdefault("finalizations", {})
    if not isinstance(pending_finalizations, dict) or not isinstance(finalizations, dict):
        raise HermesAdapterError("Hermes pending finalization state is malformed")
    for key, descriptor in list(pending_finalizations.items()):
        if not isinstance(key, str) or not isinstance(descriptor, dict):
            raise HermesAdapterError("Hermes pending finalization state is malformed")
        publish_descriptor(state_dir, descriptor, timeout_ms=timeout)
        finalizations[key] = descriptor["event_id"]
        pending_finalizations.pop(key)
        _save_state(state_path, state)

    pending = record.get("pending_compression")
    if not isinstance(pending, dict):
        return None
    descriptor = pending.get("descriptor")
    if not isinstance(descriptor, dict):
        raise HermesAdapterError("Hermes pending compression state is malformed")
    publish_descriptor(state_dir, descriptor, timeout_ms=timeout)
    boundary = descriptor["summary_source"]["current_message_row_id"]
    logical = record["logical_session_id"]
    for current in state["sessions"].values():
        if isinstance(current, dict) and current.get("logical_session_id") == logical:
            current["message_row_id"] = boundary
    record["last_compression"] = {
        "hook": pending["hook"],
        "event_id": descriptor["event_id"],
    }
    source = descriptor["summary_source"]
    if not source["in_place"] and source["session_id"] not in state["sessions"]:
        state["sessions"][source["session_id"]] = _record(
            native_session_id=source["session_id"],
            logical_session_id=logical,
            started_at=record["started_at"],
            platform=source["platform"],
            model=record.get("model"),
            message_row_id=boundary,
            injected=bool(record.get("injected")),
            start_published=bool(record.get("start_published")),
        )
        state["sessions"][source["session_id"]]["last_compression"] = record["last_compression"]
    record["pending_compression"] = None
    _save_state(state_path, state)
    return descriptor


def publish_compression(
    config: Mapping[str, Any],
    *,
    platform: str,
    session_id: str,
    old_session_id: str | None,
    in_place: bool,
    compression_count: int,
    native_store_ref: str | Path,
) -> dict[str, Any]:
    """Bind a committed gateway compression to one durable bounded candidate identity."""

    exact_platform = _safe_platform(platform)
    current_session = _safe_session(session_id)
    old_session = _safe_session(old_session_id, "Hermes old session ID") if old_session_id else None
    if type(in_place) is not bool or type(compression_count) is not int or compression_count < 0:
        raise HermesAdapterError("Hermes compression payload types are invalid")
    if in_place and old_session not in {None, current_session}:
        raise HermesAdapterError("Hermes in-place lineage is invalid")
    if not in_place and (old_session is None or old_session == current_session):
        raise HermesAdapterError("Hermes rotated lineage is invalid")
    hook_identity = {
        "platform": exact_platform,
        "session_id": current_session,
        "old_session_id": old_session,
        "in_place": in_place,
        "compression_count": compression_count,
    }
    database = Path(native_store_ref).expanduser().resolve(strict=False)
    state_dir = config["worker"]["state_dir"]
    timeout = config["worker"]["publish_timeout_ms"]
    with _state_lock(state_dir, timeout) as state_path:
        state = _load_state(state_path)
        sessions = state["sessions"]
        for pending_record in list(sessions.values()):
            if isinstance(pending_record, dict):
                _complete_pending(state_dir, timeout, state_path, state, pending_record)
        record = sessions.get(current_session if in_place else old_session)
        if not isinstance(record, dict):
            raise HermesAdapterError("Hermes compression has no persisted session binding")
        candidate_sessions = (
            (current_session,) if in_place else tuple(dict.fromkeys((old_session, current_session)))
        )
        current_boundary = _high_water(database, candidate_sessions)
        previous_boundary = record.get("message_row_id")
        if type(previous_boundary) is not int or current_boundary < previous_boundary:
            raise HermesAdapterError("Hermes message-row boundary regressed")
        last = record.get("last_compression")
        if (
            current_boundary == previous_boundary
            and isinstance(last, dict)
            and last.get("hook") == hook_identity
        ):
            return {
                "event_id": last["event_id"],
                "replayed": True,
                "candidate_row_id": None,
            }

        candidates = _candidates(
            database,
            sessions=candidate_sessions,
            previous=previous_boundary,
            current=current_boundary,
        )
        candidate_row_id: int | None = None
        candidate_hash: str | None = None
        if len(candidates) == 1:
            candidate_row_id, body = candidates[0]
            candidate_hash = hashlib.sha256(body.encode()).hexdigest()
        source = {
            "kind": "hermes-0.20.0",
            **hook_identity,
            "previous_message_row_id": previous_boundary,
            "current_message_row_id": current_boundary,
            "candidate_row_id": candidate_row_id,
            "candidate_summary_sha256": candidate_hash,
        }
        descriptor = build_descriptor(
            event_kind="checkpoint",
            agent="hermes",
            agent_version=HERMES_VERSION,
            session_id=record["logical_session_id"],
            started_at=record["started_at"],
            trigger="compression",
            occurred_at=now_utc(),
            state_dir=state_dir,
            summary_source=source,
            model=record.get("model"),
            platform=exact_platform,
            native_store_ref=str(database),
        )
        record["pending_compression"] = {"hook": hook_identity, "descriptor": descriptor}
        _save_state(state_path, state)
        publish_descriptor(state_dir, descriptor, timeout_ms=timeout)
        logical = record["logical_session_id"]
        for current in sessions.values():
            if isinstance(current, dict) and current.get("logical_session_id") == logical:
                current["message_row_id"] = current_boundary
        record["last_compression"] = {
            "hook": hook_identity,
            "event_id": descriptor["event_id"],
        }
        record["pending_compression"] = None
        if not in_place:
            sessions[current_session] = _record(
                native_session_id=current_session,
                logical_session_id=logical,
                started_at=record["started_at"],
                platform=exact_platform,
                model=record.get("model"),
                message_row_id=current_boundary,
                injected=bool(record.get("injected")),
                start_published=bool(record.get("start_published")),
            )
            sessions[current_session]["last_compression"] = record["last_compression"]
        _save_state(state_path, state)
        return {
            "event_id": descriptor["event_id"],
            "replayed": False,
            "candidate_row_id": candidate_row_id,
        }


def finalize_session(
    config: Mapping[str, Any],
    *,
    session_id: str,
    platform: str,
    native_store_ref: str | Path,
    reason: str = "finalization",
) -> dict[str, Any]:
    """Publish an unavailable native-summary finalization for one bound Hermes lineage."""

    native_session = _safe_session(session_id)
    exact_platform = _safe_platform(platform)
    safe_reason = _safe_session(reason, "Hermes finalization reason")
    database = Path(native_store_ref).expanduser().resolve(strict=False)
    state_dir = config["worker"]["state_dir"]
    timeout = config["worker"]["publish_timeout_ms"]
    with _state_lock(state_dir, timeout) as state_path:
        state = _load_state(state_path)
        record = state["sessions"].get(native_session)
        if not isinstance(record, dict):
            return {"published": False, "reason": "session is not bound"}
        key = f"{safe_reason}-{native_session}"
        finalizations = record.setdefault("finalizations", {})
        if key in finalizations:
            return {"published": False, "event_id": finalizations[key]}
        pending = record.setdefault("pending_finalizations", {})
        descriptor = pending.get(key)
        if not isinstance(descriptor, dict):
            descriptor = build_descriptor(
                event_kind="finalize",
                agent="hermes",
                agent_version=HERMES_VERSION,
                session_id=record["logical_session_id"],
                started_at=record["started_at"],
                trigger="finalization",
                occurred_at=now_utc(),
                state_dir=state_dir,
                native_event_id=f"hermes-{key}",
                model=record.get("model"),
                platform=exact_platform,
                native_store_ref=str(database),
            )
            pending[key] = descriptor
            _save_state(state_path, state)
        publish_descriptor(state_dir, descriptor, timeout_ms=timeout)
        finalizations[key] = descriptor["event_id"]
        pending.pop(key, None)
        _save_state(state_path, state)
        return {"published": True, "event_id": descriptor["event_id"]}


def _python_contract(path: Path, required_functions: set[str]) -> tuple[str | None, bool]:
    try:
        text = path.read_text(encoding="utf-8")[: 256 * 1024]
        tree = ast.parse(text, filename=str(path))
    except (OSError, SyntaxError, UnicodeError):
        return None, False
    match = PLUGIN_CONTRACT.search(text)
    functions = {
        node.name for node in tree.body if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
    }
    return (match.group(1) if match else None), required_functions <= functions


def _manifest_contract(path: Path, *, required_hooks: set[str]) -> bool:
    try:
        from ruamel.yaml import YAML

        value = YAML(typ="safe", pure=True).load(path.read_text(encoding="utf-8"))
    except Exception:
        return False
    if not isinstance(value, dict) or value.get("name") != "agent-memory":
        return False
    declared = value.get("provides_hooks", value.get("events"))
    return isinstance(declared, list) and required_hooks <= set(declared)


def hermes_adapter_health(
    *,
    home: str | Path | None = None,
    executable: str | None = None,
    runner: Runner = _run,
) -> dict[str, Any]:
    """Report pinned Hermes host plus enabled user plugin and gateway hook evidence."""

    root = (
        Path(home).expanduser()
        if home is not None
        else Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes")).expanduser()
    )
    plugin = root / "plugins/agent-memory"
    hook = root / "hooks/agent-memory"
    command = executable or shutil.which("hermes")
    detected_version = None
    detected_build = None
    if command:
        result = runner([command, "--version"])
        match = re.search(r"Hermes Agent v([^\s]+) \(([^)]+)\)", result.stdout)
        if result.returncode == 0 and match:
            detected_version, detected_build = match.groups()

    required_plugin_hooks = {
        "pre_llm_call",
        "on_session_start",
        "on_session_finalize",
        "on_session_reset",
    }
    plugin_contract, plugin_python = _python_contract(
        plugin / "__init__.py", required_plugin_hooks | {"register"}
    )
    hook_contract, hook_python = _python_contract(hook / "handler.py", {"handle"})
    plugin_valid = (
        plugin_contract == HERMES_VERSION
        and plugin_python
        and _manifest_contract(plugin / "plugin.yaml", required_hooks=required_plugin_hooks)
    )
    hook_valid = (
        hook_contract == HERMES_VERSION
        and hook_python
        and _manifest_contract(hook / "HOOK.yaml", required_hooks={"session:compress"})
    )
    enabled = False
    config_path = root / "config.yaml"
    if config_path.is_file():
        try:
            from ruamel.yaml import YAML

            value = YAML(typ="safe", pure=True).load(config_path.read_text(encoding="utf-8")) or {}
            plugins = value.get("plugins", {}) if isinstance(value, dict) else {}
            enabled = (
                "agent-memory" in plugins.get("enabled", []) if isinstance(plugins, dict) else False
            )
        except Exception:
            enabled = False

    issues: list[str] = []
    if command is None or detected_version is None:
        issues.append("Hermes host is missing or its version cannot be determined")
    elif (detected_version, detected_build) != (HERMES_VERSION, HERMES_BUILD):
        issues.append(
            "Hermes host version mismatch: expected "
            f"{HERMES_VERSION} ({HERMES_BUILD}), found {detected_version} ({detected_build})"
        )
    if not plugin_valid:
        issues.append(f"Hermes agent-memory user plugin is missing or incompatible at {plugin}")
    elif not enabled:
        issues.append("Hermes agent-memory user plugin is installed but not enabled")
    if not hook_valid:
        issues.append(f"Hermes agent-memory gateway hook is missing or incompatible at {hook}")

    return {
        "compatible": not issues,
        "expected_host_version": HERMES_VERSION,
        "expected_host_build": HERMES_BUILD,
        "detected_host_version": detected_version,
        "detected_host_build": detected_build,
        "host_executable": command,
        "plugin": {
            "path": str(plugin),
            "installed": plugin_valid,
            "contract_version": plugin_contract,
            "enabled": enabled,
        },
        "gateway_hook": {
            "path": str(hook),
            "installed": hook_valid,
            "contract_version": hook_contract,
        },
        "issues": issues,
    }
