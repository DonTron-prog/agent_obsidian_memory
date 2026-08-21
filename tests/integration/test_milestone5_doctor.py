from __future__ import annotations

import json
import subprocess
from pathlib import Path

from agent_memory.systemd import lifecycle_health


def test_doctor_health_reports_stranded_failed_start_limited_and_exact_recovery(
    tmp_path: Path,
) -> None:
    state = tmp_path / "state"
    for name in ("ready", "claimed", "failed"):
        (state / name).mkdir(parents=True)
    (state / "claimed/broken.json").write_text("{}", encoding="utf-8")
    (state / "failed/failure.json").write_text(
        json.dumps(
            {
                "schema": "agent-memory.lifecycle-failure/v1",
                "retry_id": "retry-1",
                "descriptor": None,
            }
        ),
        encoding="utf-8",
    )

    def runner(argv):
        if argv[:3] == ["systemctl", "--user", "show"]:
            output = (
                "LoadState=loaded\nActiveState=failed\nSubState=failed\nResult=start-limit-hit\n"
            )
            return subprocess.CompletedProcess(argv, 0, output, "")
        return subprocess.CompletedProcess(argv, 0, "no\n", "")

    report = lifecycle_health(state, runner=runner, user="donald")
    assert report["queues"]["claimed"]["count"] == 1
    assert report["queues"]["failed"]["ids"] == ["retry-1"]
    assert report["start_limited_units"] == [
        "agent-memory-lifecycle.path",
        "agent-memory-lifecycle.service",
    ]
    assert report["recovery"].splitlines() == [
        "systemctl --user reset-failed agent-memory-lifecycle.path agent-memory-lifecycle.service",
        "systemctl --user enable --now agent-memory-lifecycle.path",
    ]


def test_doctor_quarantines_malformed_failed_json_and_redacts_retry_ids(
    tmp_path: Path,
) -> None:
    state = tmp_path / "state"
    (state / "failed").mkdir(parents=True)
    (state / "failed/list.json").write_text('["not", "a mapping"]', encoding="utf-8")
    secret = "api_key=abcdefghijklmnop"
    (state / "failed/secret.json").write_text(
        json.dumps(
            {
                "schema": "agent-memory.lifecycle-failure/v1",
                "retry_id": secret,
                "descriptor": None,
            }
        ),
        encoding="utf-8",
    )
    (state / "worker.lock").write_text(
        json.dumps(
            {
                "pid": 123,
                "command": {"api_key": "abcdefghijklmnop"},
                "actor": "access_token=abcdefghijklmnop",
                "acquired_at": "2026-01-02T03:04:05Z",
                "hostile_unknown": secret,
            }
        ),
        encoding="utf-8",
    )

    def runner(argv):
        if argv[:3] == ["systemctl", "--user", "show"]:
            return subprocess.CompletedProcess(
                argv,
                0,
                "LoadState=loaded\nActiveState=inactive\nSubState=dead\nResult=success\n",
                "",
            )
        return subprocess.CompletedProcess(argv, 0, "yes\n", "")

    report = lifecycle_health(state, runner=runner, user="donald")
    rendered = json.dumps(report)
    assert report["queues"]["failed"]["malformed"] == ["list.json"]
    assert report["queues"]["failed"]["ids"] == ["[redacted sensitive content]"]
    assert report["worker_lock_owner"] == {
        "pid": 123,
        "actor": "[redacted sensitive content]",
        "acquired_at": "2026-01-02T03:04:05Z",
    }
    assert "abcdefghijklmnop" not in rendered


def test_doctor_reports_unavailable_and_missing_unit_probes(tmp_path: Path) -> None:
    def unavailable(argv):
        return subprocess.CompletedProcess(argv, 1, "", "Failed to connect to bus")

    report = lifecycle_health(tmp_path / "unavailable", runner=unavailable, user="donald")
    assert report["unavailable_unit_probes"] == [
        "agent-memory-lifecycle.path",
        "agent-memory-lifecycle.service",
    ]
    assert any("unavailable lifecycle unit probes" in issue for issue in report["issues"])

    def missing(argv):
        if argv[:3] == ["systemctl", "--user", "show"]:
            return subprocess.CompletedProcess(
                argv,
                0,
                "LoadState=not-found\nActiveState=inactive\nSubState=dead\nResult=success\n",
                "",
            )
        return subprocess.CompletedProcess(argv, 0, "yes\n", "")

    report = lifecycle_health(tmp_path / "missing", runner=missing, user="donald")
    assert report["missing_units"] == [
        "agent-memory-lifecycle.path",
        "agent-memory-lifecycle.service",
    ]
    assert any("missing lifecycle units" in issue for issue in report["issues"])
