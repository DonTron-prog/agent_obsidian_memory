"""Pi 0.84.2 global-extension compatibility evidence for ``memory doctor``."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

PI_VERSION = "0.84.2"
CONTRACT = re.compile(r'PI_COMPAT_VERSION\s*=\s*["\']([^"\']+)["\']')
Runner = Callable[[Sequence[str]], subprocess.CompletedProcess[str]]


def _run(argv: Sequence[str]) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(argv, text=True, capture_output=True, check=False, timeout=2)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return subprocess.CompletedProcess(argv, 1, "", type(exc).__name__)


def pi_adapter_health(
    *,
    home: str | Path | None = None,
    agent_dir: str | Path | None = None,
    executable: str | None = None,
    runner: Runner = _run,
) -> dict[str, Any]:
    """Report pinned host and auto-discovered adapter evidence without importing Pi."""

    if agent_dir is not None:
        root = Path(agent_dir).expanduser()
    elif home is not None:
        root = Path(home).expanduser() / ".pi/agent"
    else:
        configured = os.environ.get("PI_CODING_AGENT_DIR")
        root = Path(configured).expanduser() if configured else Path.home() / ".pi/agent"
    candidates = (
        root / "extensions/agent-memory/index.ts",
        root / "extensions/agent-memory.ts",
    )
    installed = next((path for path in candidates if path.is_file()), None)
    contract_version = None
    if installed is not None:
        try:
            text = installed.read_text(encoding="utf-8")
            match = CONTRACT.search(text[: 256 * 1024])
            contract_version = match.group(1) if match else None
        except OSError:
            contract_version = None

    command = executable or shutil.which("pi")
    detected_version = None
    if command:
        result = runner([command, "--version"])
        if result.returncode == 0:
            detected_version = (
                result.stdout.strip().splitlines()[0] if result.stdout.strip() else None
            )

    issues: list[str] = []
    if command is None or detected_version is None:
        issues.append("Pi host is missing or its version cannot be determined")
    elif detected_version != PI_VERSION:
        issues.append(f"Pi host version mismatch: expected {PI_VERSION}, found {detected_version}")
    if installed is None:
        issues.append(
            "Pi adapter is missing from the global auto-discovery path "
            f"{root}/extensions/agent-memory/index.ts"
        )
    elif contract_version != PI_VERSION:
        found = contract_version or "unrecognized"
        issues.append(f"Pi adapter contract mismatch: expected {PI_VERSION}, found {found}")

    return {
        "compatible": not issues,
        "expected_host_version": PI_VERSION,
        "detected_host_version": detected_version,
        "host_executable": command,
        "installation": {
            "status": "missing"
            if installed is None
            else "compatible"
            if contract_version == PI_VERSION
            else "mismatch",
            "path": str(installed) if installed else None,
            "contract_version": contract_version,
            "auto_discovered": installed is not None,
        },
        "issues": issues,
    }
