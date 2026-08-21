"""Rendered systemd user path activation and lifecycle health inspection."""

from __future__ import annotations

import getpass
import json
import shutil
import subprocess
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from agent_memory.lifecycle import _write_atomic, queue_paths, read_descriptor
from agent_memory.secrets import redact_sensitive_text

PATH_UNIT = "agent-memory-lifecycle.path"
SERVICE_UNIT = "agent-memory-lifecycle.service"
RESET_RECOVERY = (
    "systemctl --user reset-failed agent-memory-lifecycle.path "
    "agent-memory-lifecycle.service\n"
    "systemctl --user enable --now agent-memory-lifecycle.path"
)
Runner = Callable[[Sequence[str]], subprocess.CompletedProcess[str]]


def _unit_value(value: str | Path) -> str:
    text = str(value)
    if any(character in text for character in "\r\n\x00"):
        raise ValueError("systemd unit values must not contain CR, LF, or NUL")
    return text.replace("%", "%%")


def _path_value(value: str | Path) -> str:
    return _unit_value(value).replace("\\", "\\x5c").replace("\t", "\\x09").replace(" ", "\\x20")


def _exec_argument(value: str | Path) -> str:
    text = _unit_value(value).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{text}"'


def _resolve_executable(executable: str) -> str:
    _unit_value(executable)
    path = Path(executable).expanduser()
    if path.is_absolute():
        return str(path.resolve(strict=False))
    resolved = shutil.which(executable)
    if not resolved:
        raise ValueError(f"lifecycle executable is not available on PATH: {executable}")
    return str(Path(resolved).resolve(strict=False))


def render_units(
    state_dir: str | Path,
    *,
    executable: str = "memory",
    config_path: str | Path | None = None,
    vault: str | Path | None = None,
) -> tuple[str, str]:
    _unit_value(state_dir)
    paths = queue_paths(state_dir)
    ready = _path_value(paths.ready)
    claimed = _path_value(paths.claimed)
    argv = [_resolve_executable(executable)]
    if config_path is not None:
        _unit_value(config_path)
        argv.extend(("--config", str(Path(config_path).expanduser().resolve(strict=False))))
    if vault is not None:
        _unit_value(vault)
        argv.extend(("--vault", str(Path(vault).expanduser().resolve(strict=False))))
    argv.extend(("worker", "--once"))
    exec_start = " ".join(_exec_argument(item) for item in argv)
    path_unit = f"""[Unit]
Description=Activate Agent Memory lifecycle drain

[Path]
DirectoryNotEmpty={ready}
DirectoryNotEmpty={claimed}
Unit={SERVICE_UNIT}

[Install]
WantedBy=default.target
"""
    service_unit = f"""[Unit]
Description=Drain Agent Memory lifecycle queue

[Service]
Type=oneshot
ExecStart={exec_start}
"""
    return path_unit, service_unit


def _run(argv: Sequence[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(argv, text=True, capture_output=True, check=False)


def install_units(
    state_dir: str | Path,
    *,
    unit_dir: str | Path | None = None,
    executable: str = "memory",
    config_path: str | Path | None = None,
    vault: str | Path | None = None,
    runner: Runner = _run,
    user: str | None = None,
) -> dict[str, Any]:
    directory = Path(unit_dir or "~/.config/systemd/user").expanduser().resolve(strict=False)
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    path_text, service_text = render_units(
        state_dir, executable=executable, config_path=config_path, vault=vault
    )
    _write_atomic(directory / PATH_UNIT, path_text.encode())
    _write_atomic(directory / SERVICE_UNIT, service_text.encode())
    commands = [
        ["systemctl", "--user", "daemon-reload"],
        ["systemctl", "--user", "enable", "--now", PATH_UNIT],
    ]
    for command in commands:
        result = runner(command)
        if result.returncode:
            raise OSError(f"lifecycle installation command failed: {' '.join(command)}")
    linger = runner(["loginctl", "enable-linger", user or getpass.getuser()])
    warning = None
    if linger.returncode:
        warning = "user lingering could not be enabled; queued work recovers at next login"
    return {
        "path_unit": str(directory / PATH_UNIT),
        "service_unit": str(directory / SERVICE_UNIT),
        "lingering_enabled": linger.returncode == 0,
        "warning": warning,
    }


def _show(runner: Runner, unit: str) -> dict[str, str]:
    properties = ("LoadState", "ActiveState", "SubState", "Result")
    result = runner(["systemctl", "--user", "show", unit, "--property=" + ",".join(properties)])
    values = {key: "unknown" for key in properties}
    values["ProbeState"] = "available" if result.returncode == 0 else "unavailable"
    if result.returncode == 0:
        for line in result.stdout.splitlines():
            if "=" in line:
                key, value = line.split("=", 1)
                if key in properties:
                    values[key] = redact_sensitive_text(value)
    return values


def _redacted_tree(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _redacted_tree(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_redacted_tree(item) for item in value]
    return redact_sensitive_text(value) if isinstance(value, str) else value


def lifecycle_health(
    state_dir: str | Path,
    *,
    runner: Runner = _run,
    user: str | None = None,
) -> dict[str, Any]:
    paths = queue_paths(state_dir, create=True)
    queues: dict[str, Any] = {}
    for name in ("ready", "claimed", "failed"):
        directory = getattr(paths, name)
        entries = sorted(directory.glob("*.json"))
        malformed: list[str] = []
        ids: list[str] = []
        for path in entries:
            try:
                if name == "failed":
                    value = json.loads(path.read_text(encoding="utf-8"))
                    if (
                        not isinstance(value, Mapping)
                        or value.get("schema") != "agent-memory.lifecycle-failure/v1"
                        or not isinstance(value.get("retry_id"), str)
                        or not value["retry_id"]
                        or value.get("descriptor") is not None
                        and not isinstance(value["descriptor"], Mapping)
                    ):
                        raise ValueError("failed record schema is invalid")
                    ids.append(redact_sensitive_text(value["retry_id"]))
                else:
                    ids.append(redact_sensitive_text(read_descriptor(path)["event_id"]))
            except (OSError, ValueError, TypeError):
                malformed.append(redact_sensitive_text(path.name))
        queues[name] = {"count": len(entries), "ids": ids, "malformed": malformed}
    units = {unit: _show(runner, unit) for unit in (PATH_UNIT, SERVICE_UNIT)}
    linger_result = runner(
        ["loginctl", "show-user", user or getpass.getuser(), "-p", "Linger", "--value"]
    )
    lingering = linger_result.returncode == 0 and linger_result.stdout.strip().casefold() == "yes"
    unavailable_units = [
        unit
        for unit, value in units.items()
        if value["ProbeState"] != "available"
        or any(value[key] == "unknown" for key in ("LoadState", "ActiveState", "Result"))
    ]
    missing_units = [unit for unit, value in units.items() if value["LoadState"] == "not-found"]
    failed_units = [
        unit
        for unit, value in units.items()
        if value["ActiveState"] == "failed" or value["Result"] not in {"success", "unknown", ""}
    ]
    start_limited = [
        unit for unit, value in units.items() if "start-limit" in value["Result"].casefold()
    ]
    watched = queues["ready"]["count"] + queues["claimed"]["count"]
    issues: list[str] = []
    if unavailable_units:
        issues.append("unavailable lifecycle unit probes: " + ", ".join(unavailable_units))
    if missing_units:
        issues.append("missing lifecycle units: " + ", ".join(missing_units))
    if failed_units:
        issues.append("failed lifecycle units: " + ", ".join(failed_units))
    if start_limited:
        issues.append("systemd start-limit reached: " + ", ".join(start_limited))
    if watched:
        issues.append("stranded watched lifecycle queue entries")
    if queues["failed"]["count"]:
        issues.append("unwatched failed lifecycle entries require memory retry")
    lock_owner = None
    if paths.lock.is_file() and paths.lock.stat().st_size:
        try:
            lock_owner = _redacted_tree(json.loads(paths.lock.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            lock_owner = {"state": "unreadable"}
    return {
        "queues": queues,
        "units": units,
        "lingering_enabled": lingering,
        "worker_lock_owner": lock_owner,
        "failed_units": failed_units,
        "start_limited_units": start_limited,
        "unavailable_unit_probes": unavailable_units,
        "missing_units": missing_units,
        "issues": issues,
        "recovery": RESET_RECOVERY,
    }
