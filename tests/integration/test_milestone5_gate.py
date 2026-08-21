from __future__ import annotations

import multiprocessing
import os
import subprocess
from pathlib import Path

from agent_memory.cli import main
from agent_memory.config import load_config
from agent_memory.initialization import initialize_vault
from agent_memory.lifecycle import (
    build_descriptor,
    publish_descriptor,
    queue_paths,
    read_descriptor,
)
from agent_memory.systemd import render_units
from agent_memory.worker import drain_once

NOW = "2026-01-02T03:04:05Z"


def publish_then_exit(state: str) -> None:
    descriptor = build_descriptor(
        event_kind="finalize",
        agent="hermes",
        agent_version="0.20.0",
        session_id="synthetic-hermes",
        started_at=NOW,
        trigger="finalization",
        occurred_at=NOW,
        state_dir=state,
        summary_source={"kind": "unavailable"},
        native_event_id="native-final",
    )
    publish_descriptor(state, descriptor)
    raise SystemExit(0)


def test_synthetic_publication_exit_dual_path_drain_gate(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    initialize_vault(vault)
    config = load_config(vault / "system/memory.yaml")
    state = tmp_path / "state"
    config["worker"]["state_dir"] = str(state)

    process = multiprocessing.Process(target=publish_then_exit, args=(str(state),))
    process.start()
    process.join(10)
    assert process.exitcode == 0
    paths = queue_paths(state)
    assert len(list(paths.ready.iterdir())) == 1

    path_unit, service_unit = render_units(state, executable="/bin/true")
    assert str(paths.ready) in path_unit and str(paths.claimed) in path_unit
    assert "Type=oneshot" in service_unit
    assert drain_once(vault, config) == {"processed": 1, "failed": 0, "noop": 0}

    session = vault / "sessions/hermes/2026/synthetic-hermes.md"
    text = session.read_text()
    assert "native summary unavailable" in text
    assert "status: closed" in text
    assert not list(paths.ready.iterdir()) and not list(paths.claimed.iterdir())


def test_cli_worker_once_drains_simultaneous_claimed_before_ready(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    initialize_vault(vault)
    state = tmp_path / "state"
    config_path = vault / "system/memory.yaml"
    config_path.write_text(
        config_path.read_text().replace(
            "state_dir: ~/.local/state/agent-memory/lifecycle",
            f"state_dir: {state}",
        )
    )
    subprocess.run(["git", "-C", str(vault), "add", "system/memory.yaml"], check=True)
    subprocess.run(
        ["git", "-C", str(vault), "commit", "-m", "test lifecycle config"],
        check=True,
        capture_output=True,
    )
    config = load_config(config_path)
    claimed = build_descriptor(
        event_kind="checkpoint",
        agent="pi",
        agent_version="0.84.2",
        session_id="cli-session",
        started_at=NOW,
        trigger="compaction",
        occurred_at=NOW,
        state_dir=state,
        summary_source={"kind": "pi", "compaction_entry_id": "first", "summary": "First"},
    )
    ready = build_descriptor(
        event_kind="finalize",
        agent="pi",
        agent_version="0.84.2",
        session_id="cli-session",
        started_at=NOW,
        trigger="finalization",
        occurred_at="2026-01-02T04:00:00Z",
        state_dir=state,
        summary_source={"kind": "unavailable"},
        native_event_id="final-cli",
    )
    claimed_path = publish_descriptor(state, claimed)
    queues = queue_paths(state)
    os.replace(claimed_path, queues.claimed / claimed_path.name)
    publish_descriptor(state, ready)

    assert main(["--vault", str(vault), "worker", "--once", "--json"]) == 0
    text = (vault / "sessions/pi/2026/cli-session.md").read_text()
    assert text.index("First") < text.index("native summary unavailable")
    assert "status: closed" in text
    assert not list(queues.claimed.iterdir()) and not list(queues.ready.iterdir())
    assert config["worker"]["state_dir"] == str(state)


def test_cli_event_id_alias_and_checkpoint_finalization_contract(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    initialize_vault(vault)
    state = tmp_path / "state"
    config_path = vault / "system/memory.yaml"
    config_path.write_text(
        config_path.read_text().replace(
            "state_dir: ~/.local/state/agent-memory/lifecycle",
            f"state_dir: {state}",
        )
    )
    subprocess.run(["git", "-C", str(vault), "add", "system/memory.yaml"], check=True)
    subprocess.run(
        ["git", "-C", str(vault), "commit", "-m", "test lifecycle config"],
        check=True,
        capture_output=True,
    )

    assert (
        main(
            [
                "--vault",
                str(vault),
                "session",
                "checkpoint",
                "--agent",
                "pi",
                "--agent-version",
                "0.84.2",
                "--session-id",
                "cli-finalization",
                "--started-at",
                NOW,
                "--occurred-at",
                NOW,
                "--trigger",
                "finalization",
                "--event-id",
                "native-finalization",
                "--json",
            ]
        )
        == 0
    )
    queued = read_descriptor(next(queue_paths(state).ready.iterdir()))
    assert queued["event_kind"] == "finalize"
    assert queued["lifecycle"]["native_event_id"] == "native-finalization"


def test_never_ran_publisher_leaves_no_watched_state(tmp_path: Path) -> None:
    paths = queue_paths(tmp_path / "never-ran")
    assert not paths.root.exists()
