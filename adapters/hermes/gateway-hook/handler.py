"""Hermes 0.20.0 gateway compression signal bridge."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any

HERMES_COMPAT_VERSION = "0.20.0"
COMMAND_TIMEOUT_SECONDS = 1.5
PAYLOAD_FIELDS = {
    "platform",
    "session_id",
    "old_session_id",
    "in_place",
    "compression_count",
}


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


async def handle(event_type: str, context: dict[str, Any]) -> None:
    """Publish only the five-field committed signal; failures never block Hermes."""

    if event_type != "session:compress" or set(context) != PAYLOAD_FIELDS:
        return
    platform = context.get("platform")
    session_id = context.get("session_id")
    old_session_id = context.get("old_session_id")
    in_place = context.get("in_place")
    compression_count = context.get("compression_count")
    if (
        not isinstance(platform, str)
        or not platform
        or not isinstance(session_id, str)
        or not session_id
        or not isinstance(old_session_id, str)
        or type(in_place) is not bool
        or type(compression_count) is not int
        or compression_count < 0
    ):
        return
    args = [
        os.getenv("AGENT_MEMORY_CLI", "memory"),
        *_location_args(),
        "session",
        "hermes-compress",
        "--platform",
        platform,
        "--session-id",
        session_id,
        "--compression-count",
        str(compression_count),
        "--native-store-ref",
        _database(),
        "--json",
    ]
    if old_session_id:
        args.extend(("--old-session-id", old_session_id))
    if in_place:
        args.append("--in-place")
    process = None
    try:
        process = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        stdout, _ = await asyncio.wait_for(process.communicate(), timeout=COMMAND_TIMEOUT_SECONDS)
        if process.returncode or len(stdout) > 1024 * 1024:
            return
    except Exception:
        if process is not None and process.returncode is None:
            process.kill()
            await process.wait()
