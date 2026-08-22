"""Hermes 0.20.0 user plugin for Agent Obsidian Memory."""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any

HERMES_COMPAT_VERSION = "0.20.0"
COMMAND_TIMEOUT_SECONDS = 1.5
MAX_JSON_BYTES = 1024 * 1024


def _location_args() -> list[str]:
    args: list[str] = []
    if os.getenv("AGENT_MEMORY_CONFIG"):
        args.extend(("--config", os.environ["AGENT_MEMORY_CONFIG"]))
    if os.getenv("AGENT_MEMORY_VAULT"):
        args.extend(("--vault", os.environ["AGENT_MEMORY_VAULT"]))
    return args


def _database() -> str:
    if os.getenv("HERMES_STATE_DB"):
        return str(Path(os.environ["HERMES_STATE_DB"]).expanduser())
    return str(Path(os.getenv("HERMES_HOME", "~/.hermes")).expanduser() / "state.db")


def _run_json(args: list[str]) -> dict[str, Any]:
    result = subprocess.run(
        [os.getenv("AGENT_MEMORY_CLI", "memory"), *_location_args(), *args, "--json"],
        text=True,
        capture_output=True,
        timeout=COMMAND_TIMEOUT_SECONDS,
        check=False,
    )
    if result.returncode or len(result.stdout.encode()) > MAX_JSON_BYTES:
        raise RuntimeError("memory command failed")
    value = json.loads(result.stdout)
    if not isinstance(value, dict):
        raise RuntimeError("memory command returned invalid JSON")
    return value


def _bind(*, defer: bool = False, **kwargs: Any) -> dict[str, Any]:
    session_id = kwargs.get("session_id")
    model = kwargs.get("model")
    platform = kwargs.get("platform") or "cli"
    if not isinstance(session_id, str) or not session_id or not isinstance(platform, str):
        raise ValueError("Hermes session/platform context is unavailable")
    args = [
        "session",
        "hermes-bind",
        "--session-id",
        session_id,
        "--platform",
        platform,
        "--native-store-ref",
        _database(),
    ]
    if isinstance(model, str) and model:
        args.extend(("--model", model))
    sender_id = kwargs.get("sender_id")
    chat_type = kwargs.get("chat_type")
    if isinstance(sender_id, str) and sender_id:
        args.extend(("--sender-id", sender_id))
    if isinstance(chat_type, str) and chat_type:
        args.extend(("--chat-type", chat_type))
    if defer:
        args.append("--defer-injection")
    return _run_json(args)


def _notifications(*, session_id: str, allowed: bool) -> str | None:
    if not allowed:
        return None
    response = _run_json(
        ["session", "notifications", "--agent", "hermes", "--session-id", session_id]
    )
    values = response.get("notifications")
    if not isinstance(values, list) or not values:
        return None
    messages: list[str] = []
    acknowledgements: list[str] = []
    for item in values:
        if not isinstance(item, dict):
            continue
        retry_id = item.get("retry_id")
        message = item.get("message")
        if isinstance(retry_id, str) and isinstance(message, str):
            acknowledgements.append(retry_id)
            messages.append(message[:300])
    if acknowledgements:
        _run_json(
            [
                "session",
                "notifications",
                "--agent",
                "hermes",
                "--session-id",
                session_id,
                *[part for retry_id in acknowledgements for part in ("--ack", retry_id)],
            ]
        )
    return "Agent memory worker warning:\n" + "\n".join(f"- {item}" for item in messages)


def pre_llm_call(**kwargs: Any) -> dict[str, str] | None:
    """Lazily rebind every turn; inject the root index only for a new identity."""

    try:
        result = _bind(**kwargs)
        logical_session = result.get("logical_session_id")
        warning = (
            _notifications(
                session_id=logical_session,
                allowed=result.get("notification_allowed") is True,
            )
            if isinstance(logical_session, str)
            else None
        )
        parts = [item for item in (result.get("content"), warning) if isinstance(item, str)]
        return {"context": "\n\n".join(parts)} if parts else None
    except Exception:
        return {"context": "Agent memory adapter unavailable; run `memory doctor`."}


def on_session_start(**kwargs: Any) -> None:
    try:
        _bind(defer=True, **kwargs)
    except Exception:
        pass


def on_session_reset(**kwargs: Any) -> None:
    try:
        _bind(defer=True, **kwargs)
    except Exception:
        pass


def on_session_finalize(**kwargs: Any) -> None:
    session_id = kwargs.get("session_id")
    platform = kwargs.get("platform") or "cli"
    if not isinstance(session_id, str) or not session_id:
        return
    reason = kwargs.get("reason")
    args = [
        "session",
        "hermes-finalize",
        "--session-id",
        session_id,
        "--platform",
        str(platform),
        "--native-store-ref",
        _database(),
    ]
    if isinstance(reason, str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,199}", reason):
        args.extend(("--reason", reason))
    try:
        _run_json(args)
    except Exception:
        pass


def register(ctx: Any) -> None:
    ctx.register_hook("pre_llm_call", pre_llm_call)
    ctx.register_hook("on_session_start", on_session_start)
    ctx.register_hook("on_session_finalize", on_session_finalize)
    ctx.register_hook("on_session_reset", on_session_reset)
