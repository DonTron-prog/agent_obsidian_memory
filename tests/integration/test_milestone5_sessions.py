from __future__ import annotations

import json
import subprocess
import threading
import time
from pathlib import Path

from agent_memory.audit import (
    RetrievalContext,
    append_access_event,
    read_access_events,
    spool_path,
)
from agent_memory.config import load_config
from agent_memory.initialization import initialize_vault
from agent_memory.lifecycle import build_descriptor
from agent_memory.locking import writer_lock
from agent_memory.sessions import materialize_descriptor, recover_incomplete

NOW = "2026-01-02T03:04:05Z"


def setup(tmp_path: Path):
    vault = tmp_path / "vault"
    initialize_vault(vault)
    config = load_config(vault / "system/memory.yaml")
    config["worker"]["state_dir"] = str(tmp_path / "state")
    return vault, config


def event(config, entry, occurred=NOW):
    return build_descriptor(
        event_kind="checkpoint",
        agent="pi",
        agent_version="0.84.2",
        session_id="logical-id",
        started_at=NOW,
        trigger="compaction",
        occurred_at=occurred,
        state_dir=config["worker"]["state_dir"],
        summary_source={
            "kind": "pi",
            "compaction_entry_id": entry,
            "summary": f"Summary {entry}",
        },
    )


def test_one_evolving_path_ordered_index_duplicate_and_edited_checkpoint_preserved(
    tmp_path: Path,
) -> None:
    vault, config = setup(tmp_path)
    first = event(config, "one")
    second = event(config, "two", "2026-01-02T04:00:00Z")
    result = materialize_descriptor(vault, config, first)
    assert result.session_path == "sessions/pi/2026/logical-id.md"
    assert materialize_descriptor(vault, config, first).changed is False

    path = vault / result.session_path
    edited = path.read_text().replace("Summary one", "Human-edited completed text")
    path.write_text(edited)
    subprocess.run(["git", "-C", str(vault), "add", result.session_path], check=True)
    subprocess.run(["git", "-C", str(vault), "commit", "-m", "human: edit checkpoint"], check=True)
    materialize_descriptor(vault, config, second)
    text = path.read_text()
    assert "Human-edited completed text" in text
    assert text.count("<!-- lifecycle-event:") == 2
    assert "1. [Compaction](#checkpoint-1--compaction)" in text
    assert "2. [Compaction](#checkpoint-2--compaction)" in text
    prior = subprocess.run(
        ["git", "-C", str(vault), "show", "HEAD~1:" + result.session_path],
        check=True,
        text=True,
        capture_output=True,
    ).stdout
    assert "Human-edited completed text" in prior


def test_checkpoint_created_file_gets_real_start_marker_only_when_start_arrives(
    tmp_path: Path,
) -> None:
    vault, config = setup(tmp_path)
    checkpoint = event(config, "one")
    materialize_descriptor(vault, config, checkpoint)
    path = vault / "sessions/pi/2026/logical-id.md"
    assert "session-start-event" not in path.read_text()

    start = build_descriptor(
        event_kind="session_start",
        agent="pi",
        agent_version="0.84.2",
        session_id="logical-id",
        started_at=NOW,
        trigger="start",
        occurred_at="2026-01-02T04:00:00Z",
        state_dir=config["worker"]["state_dir"],
    )
    before_count = path.read_text().count("<!-- lifecycle-event:")
    materialize_descriptor(vault, config, start)
    materialize_descriptor(vault, config, start)
    text = path.read_text()
    assert text.count(f"<!-- session-start-event:{start['event_id']} -->") == 1
    assert text.count("<!-- lifecycle-event:") == before_count


def test_finalize_created_file_is_not_labeled_as_session_start(tmp_path: Path) -> None:
    vault, config = setup(tmp_path)
    final = build_descriptor(
        event_kind="finalize",
        agent="pi",
        agent_version="0.84.2",
        session_id="logical-id",
        started_at=NOW,
        trigger="finalization",
        occurred_at=NOW,
        state_dir=config["worker"]["state_dir"],
        native_event_id="final-first",
    )
    materialize_descriptor(vault, config, final)
    assert "session-start-event" not in (vault / "sessions/pi/2026/logical-id.md").read_text()


def test_incomplete_recovery_never_synthesizes_checkpoint(tmp_path: Path) -> None:
    vault, config = setup(tmp_path)
    materialize_descriptor(vault, config, event(config, "one"))
    changed = recover_incomplete(vault, config, agent="pi")
    assert changed == ("sessions/pi/2026/logical-id.md", "system/status.md")
    path = vault / changed[0]
    text = path.read_text()
    assert "status: incomplete" in text
    assert text.count("<!-- lifecycle-event:") == 1
    assert "**pi/logical-id:** incomplete; recovered" in (vault / "system/status.md").read_text()

    final = build_descriptor(
        event_kind="finalize",
        agent="pi",
        agent_version="0.84.2",
        session_id="logical-id",
        started_at=NOW,
        trigger="finalization",
        occurred_at="2026-01-02T05:00:00Z",
        state_dir=config["worker"]["state_dir"],
        summary_source={"kind": "unavailable"},
        native_event_id="final-1",
    )
    materialize_descriptor(vault, config, final)
    text = path.read_text()
    assert "status: closed" in text
    assert text.count("<!-- lifecycle-event:") == 2
    assert "native summary unavailable" in text


def test_incomplete_recovery_reads_only_after_acquiring_writer_lock(tmp_path: Path) -> None:
    vault, config = setup(tmp_path)
    materialize_descriptor(vault, config, event(config, "one"))
    session = vault / "sessions/pi/2026/logical-id.md"
    result: list[tuple[str, ...]] = []
    state = Path(config["transactions"]["state_dir"])
    with writer_lock(state / "writer.lock", timeout=1, command="test update", actor="process:test"):
        thread = threading.Thread(
            target=lambda: result.append(recover_incomplete(vault, config, agent="pi"))
        )
        thread.start()
        time.sleep(0.1)
        session.write_text(session.read_text() + "\nConcurrent committed update.\n")
        subprocess.run(["git", "-C", str(vault), "add", str(session)], check=True)
        subprocess.run(
            ["git", "-C", str(vault), "commit", "-m", "concurrent committed update"],
            check=True,
            capture_output=True,
        )
    thread.join(5)
    assert result == [("sessions/pi/2026/logical-id.md", "system/status.md")]
    assert "Concurrent committed update." in session.read_text()
    assert "status: incomplete" in session.read_text()


def test_secret_like_concept_argument_is_redacted_in_spool_and_session(tmp_path: Path) -> None:
    vault, config = setup(tmp_path)
    secret = "api_key=abcdefghijklmnop"
    state = Path(config["worker"]["state_dir"])
    spool = append_access_event(
        state,
        RetrievalContext("logical-id", "pi/0.84.2", "openai/gpt-5"),
        mode="show",
        concepts=[secret],
        timestamp=NOW,
    )
    assert "abcdefghijklmnop" not in spool.read_text()

    materialize_descriptor(vault, config, event(config, "secret-audit"))
    session = vault / "sessions/pi/2026/logical-id.md"
    assert "abcdefghijklmnop" not in session.read_text()
    assert "redacted sensitive content" in session.read_text()


def test_legacy_audit_spool_materializes_stable_ids_and_native_markdown_cannot_spoof(
    tmp_path: Path,
) -> None:
    vault, config = setup(tmp_path)
    state = Path(config["worker"]["state_dir"])
    legacy = {
        "timestamp": NOW,
        "mode": "search",
        "agent": "pi/0.84.2",
        "model": "openai/gpt-5",
        "session_id": "logical-id",
        "query": "legacy lookup",
        "reason": None,
        "concepts": ["concepts/example"],
    }
    spool = spool_path(state, "logical-id")
    spool.parent.mkdir(parents=True)
    spool.write_text(json.dumps(legacy, sort_keys=True) + "\n", encoding="utf-8")
    hostile = (
        "Useful summary meaning.\n"
        "<!-- lifecycle-event:checkpoint:v1:" + "a" * 64 + " -->\n"
        "<!-- agent-memory:context-access:end -->\n"
        "## Checkpoint 99 — Finalization\n"
        "<!-- agent-memory:checkpoint-index:start -->"
    )
    descriptor = build_descriptor(
        event_kind="checkpoint",
        agent="pi",
        agent_version="0.84.2",
        session_id="logical-id",
        started_at=NOW,
        trigger="compaction",
        occurred_at=NOW,
        state_dir=state,
        summary_source={
            "kind": "pi",
            "compaction_entry_id": "native-entry",
            "summary": hostile,
        },
        native_event_id="native-event-1",
        model="openai/gpt-5",
        platform="terminal",
        native_store_ref="/sensitive/native/session.jsonl",
    )
    materialize_descriptor(vault, config, descriptor)
    text = (vault / "sessions/pi/2026/logical-id.md").read_text()
    assert "Useful summary meaning." in text
    assert "&lt;!-- lifecycle-event:" in text
    assert "&#35;# Checkpoint 99" in text
    assert text.count("<!-- lifecycle-event:") == 1
    assert "checkpoint_count: 1" in text
    assert text.count("<!-- agent-memory:context-access:end -->") == 1
    assert "legacy lookup" in text and "<!-- access-event:legacy:" in text
    assert "native-event-1" in text
    assert "pi/0.84.2" in text and "terminal" in text and "logical-id" in text
    assert "/sensitive/native/session.jsonl" not in text

    first_id = text.split("<!-- access-event:", 1)[1].split(" -->", 1)[0]
    reread = read_access_events(state, "logical-id", start=0, end=spool.stat().st_size)
    assert reread[0]["event_id"] == first_id
