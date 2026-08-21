from __future__ import annotations

import json
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from agent_memory.audit import RetrievalContext, append_access_event
from agent_memory.config import load_config
from agent_memory.initialization import initialize_vault
from agent_memory.lifecycle import build_descriptor, publish_descriptor, queue_paths
from agent_memory.locking import LockTimeoutError, writer_lock
from agent_memory.sessions import MaterializationResult
from agent_memory.worker import _record_error, drain_once, retry_failed

NOW = "2026-01-02T03:04:05Z"


class Crash(BaseException):
    pass


def setup(tmp_path: Path):
    vault = tmp_path / "vault"
    initialize_vault(vault)
    config = load_config(vault / "system/memory.yaml")
    config["worker"]["state_dir"] = str(tmp_path / "lifecycle")
    return vault, config


def descriptor(config, *, entry="entry-1", summary="Native checkpoint", final=False):
    return build_descriptor(
        event_kind="finalize" if final else "checkpoint",
        agent="pi",
        agent_version="0.84.2",
        session_id="session-1",
        started_at=NOW,
        trigger="finalization" if final else "compaction",
        occurred_at="2026-01-02T04:00:00Z" if final else NOW,
        state_dir=config["worker"]["state_dir"],
        summary_source=(
            {"kind": "unavailable"}
            if final
            else {"kind": "pi", "compaction_entry_id": entry, "summary": summary}
        ),
        native_event_id="native-final" if final else None,
        model="openai/gpt-5",
    )


def test_one_worker_lock_prevents_duplicate_concurrent_drain(tmp_path: Path) -> None:
    vault, config = setup(tmp_path)
    publish_descriptor(config["worker"]["state_dir"], descriptor(config))
    entered = threading.Event()
    release = threading.Event()
    calls = []

    def slow(*args, **kwargs):
        calls.append(1)
        entered.set()
        release.wait(5)
        return MaterializationResult(True, "synthetic", 1, "commit")

    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(drain_once, vault, config, materializer=slow)
        assert entered.wait(5)
        second = executor.submit(drain_once, vault, config, materializer=slow)
        with pytest.raises(LockTimeoutError):
            second.result(5)
        release.set()
        assert first.result(5)["processed"] == 1
    assert calls == [1]


def test_post_claim_and_commit_before_delete_recover_idempotently(tmp_path: Path) -> None:
    vault, config = setup(tmp_path)
    publish_descriptor(config["worker"]["state_dir"], descriptor(config))

    def crash_after_claim(point):
        if point == "after_claim":
            raise Crash

    with pytest.raises(Crash):
        drain_once(vault, config, fault_hook=crash_after_claim)
    queues = queue_paths(config["worker"]["state_dir"])
    assert not list(queues.ready.iterdir())
    assert len(list(queues.claimed.iterdir())) == 1

    def crash_after_commit(point):
        if point == "after_materialization_before_delete":
            raise Crash

    with pytest.raises(Crash):
        drain_once(vault, config, fault_hook=crash_after_commit)
    session = vault / "sessions/pi/2026/session-1.md"
    assert session.read_text().count("<!-- lifecycle-event:") == 1
    before = subprocess.run(
        ["git", "-C", str(vault), "rev-list", "--count", "HEAD"],
        check=True,
        text=True,
        capture_output=True,
    ).stdout.strip()
    result = drain_once(vault, config)
    after = subprocess.run(
        ["git", "-C", str(vault), "rev-list", "--count", "HEAD"],
        check=True,
        text=True,
        capture_output=True,
    ).stdout.strip()
    assert result == {"processed": 1, "failed": 0, "noop": 1}
    assert before == after
    assert not list(queues.claimed.iterdir())


def test_bounded_retry_moves_unwatched_failed_and_manual_retry_republishes(tmp_path: Path) -> None:
    vault, config = setup(tmp_path)
    publish_descriptor(config["worker"]["state_dir"], descriptor(config))
    calls = []
    sleeps = []

    def broken(*args, **kwargs):
        calls.append(1)
        raise OSError("api_key=must-not-survive")

    result = drain_once(vault, config, sleep=sleeps.append, materializer=broken)
    queues = queue_paths(config["worker"]["state_dir"])
    assert result["failed"] == 1
    assert len(calls) == 3
    assert sleeps == [0.1, 0.2]
    assert not list(queues.ready.iterdir()) and not list(queues.claimed.iterdir())
    failed = next(queues.failed.iterdir())
    record = json.loads(failed.read_text())
    assert "must-not-survive" not in failed.read_text()
    assert record["attempt_count"] == 3
    errors = (vault / "system/errors.md").read_text()
    assert record["retry_id"] in errors
    assert "must-not-survive" not in errors
    notification = next(queues.notifications.iterdir()).read_text()
    assert "must-not-survive" not in notification

    def crash_after_publish(point: str) -> None:
        if point == "after_publish_before_failed_delete":
            raise Crash

    with pytest.raises(Crash):
        retry_failed(
            config["worker"]["state_dir"],
            retry_id=record["retry_id"],
            fault_hook=crash_after_publish,
        )
    assert len(list(queues.failed.iterdir())) == len(list(queues.ready.iterdir())) == 1
    assert drain_once(vault, config)["processed"] == 1
    assert not list(queues.failed.iterdir()) and not list(queues.ready.iterdir())

    publish_descriptor(config["worker"]["state_dir"], descriptor(config, entry="entry-2"))
    drain_once(vault, config, sleep=lambda _: None, materializer=broken)
    failed = next(queues.failed.iterdir())
    second = json.loads(failed.read_text())
    with pytest.raises(Crash):
        retry_failed(
            config["worker"]["state_dir"],
            retry_id=second["retry_id"],
            fault_hook=crash_after_publish,
        )
    assert retry_failed(config["worker"]["state_dir"], retry_id=second["retry_id"]) == (
        second["retry_id"],
    )
    assert not list(queues.failed.iterdir()) and len(list(queues.ready.iterdir())) == 1


def test_hermes_queue_orders_stable_outer_lineage_by_row_boundary(tmp_path: Path) -> None:
    vault, config = setup(tmp_path)
    state = config["worker"]["state_dir"]

    def hermes_source(old: str, current_session: str, row: int) -> dict:
        return {
            "kind": "hermes-0.20.0",
            "platform": "telegram",
            "session_id": current_session,
            "old_session_id": old,
            "in_place": False,
            "compression_count": 1,
            "previous_message_row_id": row - 1,
            "current_message_row_id": row,
            "candidate_row_id": None,
            "candidate_summary_sha256": None,
        }

    for source in (hermes_source("a", "b", 100), hermes_source("b", "c", 20)):
        publish_descriptor(
            state,
            build_descriptor(
                event_kind="checkpoint",
                agent="hermes",
                agent_version="0.20.0",
                session_id="logical-root",
                started_at=NOW,
                trigger="compression",
                occurred_at=NOW,
                state_dir=state,
                summary_source=source,
                platform="telegram",
            ),
        )
    order = []

    def capture(*args):
        order.append(args[2]["summary_source"]["current_message_row_id"])
        return MaterializationResult(True, "synthetic", len(order), "commit")

    assert drain_once(vault, config, materializer=capture)["processed"] == 2
    assert order == [20, 100]


def test_persistent_error_update_reads_only_after_writer_lock(tmp_path: Path) -> None:
    vault, config = setup(tmp_path)
    record = {
        "retry_id": "retry-concurrent",
        "failed_at": NOW,
        "message": "OSError",
        "retry_state": "exhausted",
    }
    state = Path(config["transactions"]["state_dir"])
    errors = vault / "system/errors.md"
    with writer_lock(state / "writer.lock", timeout=1, command="test update", actor="process:test"):
        thread = threading.Thread(target=lambda: _record_error(vault, config, None, record))
        thread.start()
        time.sleep(0.1)
        errors.write_text("# Errors\n\nConcurrent committed diagnostic.\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(vault), "add", str(errors)], check=True)
        subprocess.run(
            ["git", "-C", str(vault), "commit", "-m", "concurrent diagnostic"],
            check=True,
            capture_output=True,
        )
    thread.join(5)
    text = errors.read_text()
    assert "Concurrent committed diagnostic." in text
    assert "retry-concurrent" in text


def test_reset_materializes_new_audit_without_synthesizing_summary(tmp_path: Path) -> None:
    vault, config = setup(tmp_path)
    state = config["worker"]["state_dir"]
    publish_descriptor(state, descriptor(config))
    drain_once(vault, config)
    append_access_event(
        state,
        RetrievalContext("session-1", "pi/0.84.2", "openai/gpt-5"),
        mode="show",
        reason="before reset",
        concepts=["concepts/example"],
        timestamp="2026-01-02T04:30:00Z",
    )
    reset = build_descriptor(
        event_kind="checkpoint",
        agent="pi",
        agent_version="0.84.2",
        session_id="session-1",
        started_at=NOW,
        trigger="reset",
        occurred_at="2026-01-02T05:00:00Z",
        state_dir=state,
        summary_source={"kind": "unavailable"},
        native_event_id="native-reset",
    )
    publish_descriptor(state, reset)
    drain_once(vault, config)
    text = (vault / "sessions/pi/2026/session-1.md").read_text()
    assert "before reset" in text
    assert "native summary unavailable" in text
    assert "status: closed" in text


def test_claimed_first_backlog_audit_finalization_and_no_raw_transcript(tmp_path: Path) -> None:
    vault, config = setup(tmp_path)
    state = config["worker"]["state_dir"]
    append_access_event(
        state,
        RetrievalContext("session-1", "pi/0.84.2", "openai/gpt-5"),
        mode="search",
        query="deployment",
        concepts=["concepts/example"],
        timestamp=NOW,
    )
    checkpoint = descriptor(config)
    final = descriptor(config, final=True)
    publish_descriptor(state, checkpoint)
    publish_descriptor(state, final)
    result = drain_once(vault, config)
    text = (vault / "sessions/pi/2026/session-1.md").read_text()
    assert result["processed"] == 2
    assert text.count("## Checkpoint ") == 3  # index heading plus two checkpoints
    assert "Native checkpoint" in text
    assert "native summary unavailable" in text
    assert "deployment" in text and "concepts/example" in text
    assert "raw transcript" not in text.casefold()
    assert "status: closed" in text
    queues = queue_paths(state)
    assert not list(queues.ready.iterdir()) and not list(queues.claimed.iterdir())
