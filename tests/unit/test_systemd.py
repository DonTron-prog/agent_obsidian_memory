from __future__ import annotations

import subprocess
from pathlib import Path

from agent_memory.systemd import install_units, lifecycle_health, render_units


def completed(argv, code=0, output=""):
    return subprocess.CompletedProcess(argv, code, output, "")


def test_renders_both_paths_oneshot_and_install_command_order(tmp_path: Path) -> None:
    state = tmp_path / "state with spaces"
    path_unit, service_unit = render_units(state, executable="/opt/memory cli")
    escaped_state = str(state.resolve()).replace(" ", "\\x20")
    assert f"DirectoryNotEmpty={escaped_state}/ready" in path_unit
    assert f"DirectoryNotEmpty={escaped_state}/claimed" in path_unit
    assert 'DirectoryNotEmpty="' not in path_unit
    assert "Unit=agent-memory-lifecycle.service" in path_unit
    assert "WantedBy=default.target" in path_unit
    assert "Type=oneshot" in service_unit
    assert 'ExecStart="/opt/memory cli" "worker" "--once"' in service_unit

    calls = []

    def runner(argv):
        calls.append(list(argv))
        return completed(argv)

    result = install_units(
        state,
        unit_dir=tmp_path / "units",
        executable="/bin/true",
        runner=runner,
        user="donald",
    )
    assert calls == [
        ["systemctl", "--user", "daemon-reload"],
        ["systemctl", "--user", "enable", "--now", "agent-memory-lifecycle.path"],
        ["loginctl", "enable-linger", "donald"],
    ]
    assert result["lingering_enabled"] is True


def test_render_preserves_config_vault_and_rejects_hostile_unit_values(tmp_path: Path) -> None:
    path_unit, service_unit = render_units(
        tmp_path / "state",
        executable="/opt/memory cli",
        config_path=tmp_path / 'cfg "quoted".yaml',
        vault=tmp_path / "alternate vault",
    )
    assert str((tmp_path / "state/ready").resolve()) in path_unit
    assert f'"--config" "{tmp_path}/cfg \\"quoted\\".yaml"' in service_unit
    assert f'"--vault" "{tmp_path}/alternate vault" "worker" "--once"' in service_unit
    for value in ("bad\nvalue", "bad\rvalue", "bad\x00value"):
        try:
            render_units(tmp_path / value, executable="/bin/true")
        except ValueError as exc:
            assert "CR, LF, or NUL" in str(exc)
        else:
            raise AssertionError("hostile systemd value was accepted")


def test_linger_fallback_and_start_limit_queue_diagnosis(tmp_path: Path) -> None:
    state = tmp_path / "state"
    (state / "ready").mkdir(parents=True)
    (state / "ready/x.json").write_text("bad", encoding="utf-8")

    def runner(argv):
        if argv[:3] == ["systemctl", "--user", "show"]:
            return completed(
                argv,
                output="LoadState=loaded\nActiveState=failed\nSubState=failed\nResult=start-limit-hit\n",
            )
        return completed(argv, code=1)

    health = lifecycle_health(state, runner=runner, user="donald")
    assert health["start_limited_units"]
    assert health["queues"]["ready"]["count"] == 1
    assert health["queues"]["ready"]["malformed"] == ["x.json"]
    assert health["lingering_enabled"] is False
    assert (
        "reset-failed agent-memory-lifecycle.path agent-memory-lifecycle.service"
        in health["recovery"]
    )
