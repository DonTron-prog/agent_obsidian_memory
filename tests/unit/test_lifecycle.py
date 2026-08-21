from __future__ import annotations

import fcntl
import json
import multiprocessing
import os
import stat
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from agent_memory.cli import build_parser
from agent_memory.config import ConfigError, load_config
from agent_memory.lifecycle import (
    LifecycleError,
    build_descriptor,
    descriptor_filename,
    publish_descriptor,
    publish_lifecycle_safely,
    queue_paths,
)

NOW = "2026-01-02T03:04:05Z"


def _publish_in_process(state: str, descriptor: dict, results) -> None:
    try:
        results.put(str(publish_descriptor(state, descriptor)))
    except Exception as exc:
        results.put(type(exc).__name__)


def pi_descriptor(state: Path, *, summary: str = "Native body") -> dict:
    return build_descriptor(
        event_kind="checkpoint",
        agent="pi",
        agent_version="0.84.2",
        session_id="session-1",
        started_at=NOW,
        trigger="compaction",
        occurred_at=NOW,
        state_dir=state,
        summary_source={"kind": "pi", "compaction_entry_id": "entry-1", "summary": summary},
        model="openai/gpt-5",
    )


def test_retry_cli_targets_are_mutually_exclusive() -> None:
    parser = build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["retry", "retry-1", "--all"])
    assert parser.parse_args(["retry", "retry-1"]).retry_id == "retry-1"
    assert parser.parse_args(["retry", "--all"]).all_failed is True


def test_deterministic_identity_atomic_private_publication_and_duplicate(tmp_path: Path) -> None:
    state = tmp_path / "state"
    first = pi_descriptor(state)
    second = pi_descriptor(state)
    assert first["event_id"] == second["event_id"]

    path = publish_descriptor(state, first)
    assert path.parent == state / "ready"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    assert publish_descriptor(state, second) == path
    assert len(list(path.parent.iterdir())) == 1

    conflicting = json.loads(json.dumps(first))
    conflicting["summary_source"]["summary"] = "Changed under same stable identity"
    with pytest.raises(LifecycleError, match="different content"):
        publish_descriptor(state, conflicting)


def test_hermes_identity_includes_boundaries_and_nullable_candidate(tmp_path: Path) -> None:
    state = tmp_path / "state"
    source = {
        "kind": "hermes-0.20.0",
        "platform": "telegram",
        "session_id": "h-1",
        "old_session_id": None,
        "in_place": True,
        "compression_count": 1,
        "previous_message_row_id": 10,
        "current_message_row_id": 20,
        "candidate_row_id": None,
        "candidate_summary_sha256": None,
    }
    one = build_descriptor(
        event_kind="checkpoint",
        agent="hermes",
        agent_version="0.20.0",
        session_id="h-1",
        started_at=NOW,
        trigger="compression",
        occurred_at=NOW,
        state_dir=state,
        summary_source=source,
        platform="telegram",
    )
    source["current_message_row_id"] = 21
    two = build_descriptor(
        event_kind="checkpoint",
        agent="hermes",
        agent_version="0.20.0",
        session_id="h-1",
        started_at=NOW,
        trigger="compression",
        occurred_at="2026-01-02T04:00:00Z",
        state_dir=state,
        summary_source=source,
        model="ignored/for-identity",
        platform="telegram",
    )
    assert one["event_id"] != two["event_id"]
    source.update(
        {
            "old_session_id": "foreign-session",
            "in_place": True,
            "current_message_row_id": 22,
        }
    )
    with pytest.raises(LifecycleError, match="in-place Hermes source lineage"):
        build_descriptor(
            event_kind="checkpoint",
            agent="hermes",
            agent_version="0.20.0",
            session_id="h-1",
            started_at=NOW,
            trigger="compression",
            occurred_at=NOW,
            state_dir=state,
            summary_source=source,
            platform="telegram",
        )
    source.update(
        {
            "old_session_id": None,
            "current_message_row_id": 21,
            "candidate_row_id": 21,
        }
    )
    with pytest.raises(LifecycleError, match="candidate identity"):
        build_descriptor(
            event_kind="checkpoint",
            agent="hermes",
            agent_version="0.20.0",
            session_id="h-1",
            started_at=NOW,
            trigger="compression",
            occurred_at=NOW,
            state_dir=state,
            summary_source=source,
            platform="telegram",
        )


def test_rejects_traversal_raw_fields_secrets_and_independent_queue_config(tmp_path: Path) -> None:
    with pytest.raises(LifecycleError, match="unsafe"):
        build_descriptor(
            event_kind="session_start",
            agent="pi",
            agent_version="1",
            session_id="../escape",
            started_at=NOW,
            trigger="start",
            occurred_at=NOW,
            state_dir=tmp_path,
        )
    with pytest.raises(LifecycleError, match="sensitive"):
        pi_descriptor(tmp_path, summary="api_key=abcdefghijklmnop")
    value = pi_descriptor(tmp_path)
    value["prompt"] = "not persisted"
    with pytest.raises(LifecycleError, match="forbidden"):
        publish_descriptor(tmp_path, value)

    config = tmp_path / "memory.yaml"
    config.write_text("worker:\n  ready_dir: /tmp/ready\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="derived"):
        load_config(config)
    result = publish_lifecycle_safely(
        tmp_path / "safe-state",
        event_kind="checkpoint",
        agent="pi",
        agent_version="1",
        session_id="safe",
        started_at=NOW,
        trigger="compaction",
        occurred_at=NOW,
        summary_source={
            "kind": "pi",
            "compaction_entry_id": "entry",
            "summary": "raw prompt says api_key=abcdefghijklmnop",
        },
    )
    assert result == {"published": False, "error": "sensitive content rejected"}
    assert not (tmp_path / "safe-state/ready").exists()

    paths = queue_paths(tmp_path / "only")
    assert (paths.ready, paths.claimed, paths.failed) == (
        tmp_path / "only/ready",
        tmp_path / "only/claimed",
        tmp_path / "only/failed",
    )


def test_publication_is_concurrently_idempotent_no_clobber_and_orphans_are_unwatched(
    tmp_path: Path,
) -> None:
    state = tmp_path / "state"
    descriptor = pi_descriptor(state)
    with ThreadPoolExecutor(max_workers=8) as executor:
        paths = list(executor.map(lambda _: publish_descriptor(state, descriptor), range(16)))
    assert len(set(paths)) == 1
    assert len(list((state / "ready").iterdir())) == 1

    publish_descriptor(state, descriptor).unlink()
    results = multiprocessing.Queue()
    processes = [
        multiprocessing.Process(target=_publish_in_process, args=(str(state), descriptor, results))
        for _ in range(4)
    ]
    for process in processes:
        process.start()
    for process in processes:
        process.join(10)
        assert process.exitcode == 0
    assert len({results.get(timeout=2) for _ in processes}) == 1
    assert len(list((state / "ready").iterdir())) == 1
    assert not list((state / "ready").glob("*.tmp"))

    conflicting = json.loads(json.dumps(descriptor))
    conflicting["summary_source"]["summary"] = "different bytes under the same identity"
    with pytest.raises(LifecycleError, match="different content"):
        publish_descriptor(state, conflicting)
    assert json.loads(next((state / "ready").iterdir()).read_text()) == descriptor

    next((state / "ready").iterdir()).unlink()
    (state / "publication-tmp/orphan.tmp").parent.mkdir(exist_ok=True)
    (state / "publication-tmp/orphan.tmp").write_text("hard-crash orphan", encoding="utf-8")
    assert not list((state / "ready").iterdir())
    assert not list((state / "claimed").iterdir())


def test_safe_publication_returns_before_held_event_lock_timeout(tmp_path: Path) -> None:
    state = tmp_path / "state"
    descriptor = pi_descriptor(state)
    name = descriptor_filename(descriptor["event_id"])
    lock_dir = state / "publication-locks"
    lock_dir.mkdir(parents=True)
    lock = os.open(lock_dir / f"{Path(name).stem}.lock", os.O_CREAT | os.O_RDWR, 0o600)
    fcntl.flock(lock, fcntl.LOCK_EX)
    started = time.monotonic()
    try:
        result = publish_lifecycle_safely(
            state,
            publish_timeout_ms=50,
            event_kind="checkpoint",
            agent="pi",
            agent_version="0.84.2",
            session_id="session-1",
            started_at=NOW,
            trigger="compaction",
            occurred_at=NOW,
            summary_source={
                "kind": "pi",
                "compaction_entry_id": "entry-1",
                "summary": "Native body",
            },
            model="openai/gpt-5",
        )
    finally:
        fcntl.flock(lock, fcntl.LOCK_UN)
        os.close(lock)

    assert result == {"published": False, "error": "lifecycle publication failed"}
    assert 0.04 <= time.monotonic() - started < 0.5
    assert not list((state / "ready").glob("*.json"))


def test_descriptor_cross_field_exact_types_and_collision_resistance(tmp_path: Path) -> None:
    state = tmp_path / "state"
    base = pi_descriptor(state)
    bypasses = []
    for field, value in (
        ("event_kind", "finalize"),
        ("audit_through_offset", True),
    ):
        candidate = json.loads(json.dumps(base))
        candidate[field] = value
        bypasses.append(candidate)
    bad_model = json.loads(json.dumps(base))
    bad_model["host"]["model"] = "provider-only"
    bypasses.append(bad_model)
    free_text = json.loads(json.dumps(base))
    free_text["summary_source"] = {"kind": "unavailable", "reason": "raw tool output"}
    bypasses.append(free_text)
    for candidate in bypasses:
        with pytest.raises(LifecycleError):
            publish_descriptor(state, candidate)

    with pytest.raises(LifecycleError, match="native_event_id"):
        build_descriptor(
            event_kind="finalize",
            agent="pi",
            agent_version="1",
            session_id="same-session",
            started_at=NOW,
            trigger="finalization",
            occurred_at=NOW,
            state_dir=state,
        )

    hermes = {
        "kind": "hermes-0.20.0",
        "platform": "telegram",
        "session_id": "other-session",
        "old_session_id": None,
        "in_place": True,
        "compression_count": -1,
        "previous_message_row_id": 0,
        "current_message_row_id": 1,
        "candidate_row_id": None,
        "candidate_summary_sha256": None,
    }
    with pytest.raises(LifecycleError):
        build_descriptor(
            event_kind="checkpoint",
            agent="hermes",
            agent_version="0.20.0",
            session_id="logical-session",
            started_at=NOW,
            trigger="compression",
            occurred_at=NOW,
            state_dir=state,
            summary_source=hermes,
            platform="telegram",
        )
