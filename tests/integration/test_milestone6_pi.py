from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from agent_memory.pi_adapter import PI_VERSION, pi_adapter_health


def runner(version: str, *, code: int = 0):
    def run(argv):
        return subprocess.CompletedProcess(argv, code, version + "\n", "")

    return run


def test_pi_doctor_reports_structured_compatible_installation(tmp_path: Path) -> None:
    extension = tmp_path / ".pi/agent/extensions/agent-memory/index.ts"
    extension.parent.mkdir(parents=True)
    extension.write_text(f'export const PI_COMPAT_VERSION = "{PI_VERSION}";\n', encoding="utf-8")

    report = pi_adapter_health(
        home=tmp_path,
        executable="/usr/bin/pi",
        runner=runner(PI_VERSION),
    )

    assert report == {
        "compatible": True,
        "expected_host_version": "0.84.2",
        "detected_host_version": "0.84.2",
        "host_executable": "/usr/bin/pi",
        "installation": {
            "status": "compatible",
            "path": str(extension),
            "contract_version": "0.84.2",
            "auto_discovered": True,
        },
        "issues": [],
    }


def test_pi_doctor_has_clear_missing_and_mismatch_diagnostics(tmp_path: Path) -> None:
    missing = pi_adapter_health(
        home=tmp_path,
        executable="/usr/bin/pi",
        runner=runner("0.85.0"),
    )
    assert missing["compatible"] is False
    assert missing["installation"]["status"] == "missing"
    assert missing["issues"] == [
        "Pi host version mismatch: expected 0.84.2, found 0.85.0",
        "Pi adapter is missing from the global auto-discovery path "
        f"{tmp_path}/.pi/agent/extensions/agent-memory/index.ts",
    ]

    extension = tmp_path / ".pi/agent/extensions/agent-memory.ts"
    extension.parent.mkdir(parents=True)
    extension.write_text('export const PI_COMPAT_VERSION = "0.83.0";\n', encoding="utf-8")
    mismatch = pi_adapter_health(
        home=tmp_path,
        executable="/usr/bin/pi",
        runner=runner(PI_VERSION),
    )
    assert mismatch["installation"]["status"] == "mismatch"
    assert mismatch["issues"] == ["Pi adapter contract mismatch: expected 0.84.2, found 0.83.0"]
    assert "0.83.0" in json.dumps(mismatch)


def test_pi_doctor_honors_agent_dir_environment_but_explicit_paths_are_deterministic(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    environment_dir = tmp_path / "environment-agent"
    environment_extension = environment_dir / "extensions/agent-memory/index.ts"
    environment_extension.parent.mkdir(parents=True)
    environment_extension.write_text(
        f'export const PI_COMPAT_VERSION = "{PI_VERSION}";\n', encoding="utf-8"
    )
    monkeypatch.setenv("PI_CODING_AGENT_DIR", str(environment_dir))

    discovered = pi_adapter_health(executable="/usr/bin/pi", runner=runner(PI_VERSION))
    assert discovered["installation"]["path"] == str(environment_extension)
    assert discovered["compatible"] is True

    explicit_home = tmp_path / "explicit-home"
    home_extension = explicit_home / ".pi/agent/extensions/agent-memory.ts"
    home_extension.parent.mkdir(parents=True)
    home_extension.write_text(
        f'export const PI_COMPAT_VERSION = "{PI_VERSION}";\n', encoding="utf-8"
    )
    explicit = pi_adapter_health(
        home=explicit_home,
        executable="/usr/bin/pi",
        runner=runner(PI_VERSION),
    )
    assert explicit["installation"]["path"] == str(home_extension)

    explicit_agent = pi_adapter_health(
        agent_dir=environment_dir,
        home=tmp_path / "ignored-home",
        executable="/usr/bin/pi",
        runner=runner(PI_VERSION),
    )
    assert explicit_agent["installation"]["path"] == str(environment_extension)
