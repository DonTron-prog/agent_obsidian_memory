from __future__ import annotations

import asyncio
import importlib.util
import json
import sqlite3
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import ModuleType

import pytest

import agent_memory.hermes_adapter as hermes_adapter
from agent_memory.config import load_config
from agent_memory.hermes_adapter import (
    HERMES_BUILD,
    HERMES_VERSION,
    HermesAdapterError,
    bind_session,
    finalize_session,
    hermes_adapter_health,
    publish_compression,
)
from agent_memory.initialization import initialize_vault
from agent_memory.lifecycle import queue_paths, read_descriptor
from agent_memory.worker import drain_once
from tests.fixtures.hermes_020 import (
    MERGED_PRIOR_CONTEXT_HEADER,
    MERGED_SUMMARY_DELIMITER,
    SUMMARY_END_MARKER,
    SUMMARY_PREFIX,
)


def frame(body: str) -> str:
    return f"{SUMMARY_PREFIX}\n{body}\n{SUMMARY_END_MARKER}"


def setup(tmp_path: Path) -> tuple[Path, dict, Path, Path]:
    vault = tmp_path / "vault"
    initialize_vault(vault)
    state = tmp_path / "lifecycle"
    config_path = vault / "system/memory.yaml"
    config_path.write_text(
        config_path.read_text().replace(
            "state_dir: ~/.local/state/agent-memory/lifecycle", f"state_dir: {state}"
        ),
        encoding="utf-8",
    )
    config = load_config(config_path)
    database = tmp_path / "state.db"
    connection = sqlite3.connect(database)
    connection.execute(
        "CREATE TABLE messages ("
        "id INTEGER PRIMARY KEY, session_id TEXT NOT NULL, role TEXT NOT NULL, "
        "content TEXT, active INTEGER NOT NULL DEFAULT 1, compacted INTEGER NOT NULL DEFAULT 0)"
    )
    connection.execute(
        "CREATE TABLE sessions (id TEXT PRIMARY KEY, model TEXT, billing_provider TEXT, "
        "started_at REAL NOT NULL, model_config TEXT, source TEXT, user_id TEXT, chat_type TEXT)"
    )
    connection.commit()
    connection.close()
    return vault, config, config_path, database


def insert(database: Path, row_id: int, session: str, content: str, *, role: str = "assistant"):
    connection = sqlite3.connect(database)
    connection.execute(
        "INSERT INTO messages VALUES (?, ?, ?, ?, 1, 0)",
        (row_id, session, role, content),
    )
    connection.commit()
    connection.close()


def host_session(
    database: Path,
    session_id: str,
    *,
    model: str = "gpt-5.6-sol",
    provider: str = "openai-codex",
    started_at: float = 1780000000.0,
    use_model_config: bool = False,
    source: str = "cli",
    user_id: str | None = None,
    chat_type: str | None = None,
) -> None:
    connection = sqlite3.connect(database)
    connection.execute(
        "INSERT OR REPLACE INTO sessions VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            session_id,
            model,
            None if use_model_config else provider,
            started_at,
            json.dumps({"gateway_runtime": {"provider": provider}}),
            source,
            user_id,
            chat_type,
        ),
    )
    connection.commit()
    connection.close()


def module(path: Path, name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


def compression_descriptors(state: str) -> list[dict]:
    return sorted(
        (
            read_descriptor(path)
            for path in queue_paths(state).ready.glob("*.json")
            if read_descriptor(path)["summary_source"]["kind"] == "hermes-0.20.0"
        ),
        key=lambda item: item["summary_source"]["current_message_row_id"],
    )


def test_lazy_binding_restart_model_changes_and_concurrent_session_isolation(
    tmp_path: Path,
) -> None:
    vault, config, _, database = setup(tmp_path)

    def bind(session_id: str):
        return bind_session(
            vault,
            config,
            session_id=session_id,
            model="openrouter/anthropic/claude-sonnet-4",
            platform="telegram" if session_id.startswith("t") else "cli",
            sender_id=f"owner-{session_id}",
            chat_type="dm",
            native_store_ref=database,
        )

    with ThreadPoolExecutor(max_workers=3) as pool:
        first, second, third = list(pool.map(bind, ("cli-1", "telegram-1", "telegram-2")))
    assert first["injected"] is second["injected"] is third["injected"] is True
    assert bind("cli-1")["injected"] is False
    changed = bind_session(
        vault,
        config,
        session_id="cli-1",
        model="anthropic/claude-sonnet-4-6",
        platform="cli",
        native_store_ref=database,
    )
    assert changed["injected"] is False

    state_file = Path(config["worker"]["state_dir"]) / "adapter-state/hermes-v0.20.0.json"
    state = json.loads(state_file.read_text())
    assert set(state["sessions"]) == {"cli-1", "telegram-1", "telegram-2"}
    assert state["sessions"]["cli-1"]["model"] == "anthropic/claude-sonnet-4-6"
    assert state["sessions"]["cli-1"]["sender_digest"]
    assert "owner-cli-1" not in state_file.read_text()
    assert "owner-telegram-1" not in state_file.read_text()
    assert len(list(queue_paths(config["worker"]["state_dir"]).ready.glob("*.json"))) == 3


def test_gateway_compressions_are_bounded_ordered_restart_safe_and_native_only(
    tmp_path: Path,
) -> None:
    vault, config, _, database = setup(tmp_path)
    bind_session(
        vault,
        config,
        session_id="gateway-1",
        model="openrouter/anthropic/claude-sonnet-4",
        platform="telegram",
        native_store_ref=database,
    )
    first_body = "## First native summary\nOnly the isolated body."
    insert(database, 1, "gateway-1", frame(first_body))
    insert(database, 2, "gateway-1", "SECRET raw live conversation")
    first = publish_compression(
        config,
        platform="telegram",
        session_id="gateway-1",
        old_session_id=None,
        in_place=True,
        compression_count=1,
        native_store_ref=database,
    )
    merged_body = "Second native body"
    merged = (
        f"{MERGED_PRIOR_CONTEXT_HEADER}\nSECRET PRESERVED TAIL\n"
        f"{MERGED_SUMMARY_DELIMITER}\n{frame(merged_body)}"
    )
    insert(database, 3, "gateway-1", merged, role="user")
    second = publish_compression(
        config,
        platform="telegram",
        session_id="gateway-1",
        old_session_id=None,
        in_place=True,
        compression_count=2,
        native_store_ref=database,
    )
    reset_body = "Count reset after process restart"
    insert(database, 4, "gateway-1", frame(reset_body))
    third = publish_compression(
        config,
        platform="telegram",
        session_id="gateway-1",
        old_session_id=None,
        in_place=True,
        compression_count=1,
        native_store_ref=database,
    )
    replay = publish_compression(
        config,
        platform="telegram",
        session_id="gateway-1",
        old_session_id=None,
        in_place=True,
        compression_count=1,
        native_store_ref=database,
    )
    forced_body = "Forced user-leading native body"
    live_request = "SECRET LIVE USER REQUEST"
    insert(
        database,
        5,
        "gateway-1",
        f"{SUMMARY_PREFIX}\n{forced_body}\n{SUMMARY_END_MARKER}\n\n{live_request}",
        role="user",
    )
    fourth = publish_compression(
        config,
        platform="telegram",
        session_id="gateway-1",
        old_session_id=None,
        in_place=True,
        compression_count=2,
        native_store_ref=database,
    )
    assert len({first["event_id"], second["event_id"], third["event_id"], fourth["event_id"]}) == 4
    assert replay["event_id"] == third["event_id"] and replay["replayed"] is True

    descriptors = compression_descriptors(config["worker"]["state_dir"])
    assert [
        (
            item["summary_source"]["previous_message_row_id"],
            item["summary_source"]["current_message_row_id"],
        )
        for item in descriptors
    ] == [(0, 2), (2, 3), (3, 4), (4, 5)]
    serialized = json.dumps(descriptors)
    assert first_body not in serialized
    assert merged_body not in serialized
    assert fourth["candidate_row_id"] is None
    assert forced_body not in serialized
    assert "PRESERVED TAIL" not in serialized
    assert "raw live conversation" not in serialized
    assert live_request not in serialized

    assert drain_once(vault, config)["failed"] == 0
    text = (vault / "sessions/hermes/2026/gateway-1.md").read_text()
    assert first_body in text and merged_body in text and reset_body in text
    assert forced_body not in text
    assert "PRESERVED TAIL" not in text and "raw live conversation" not in text
    assert live_request not in text
    assert text.index(first_body) < text.index(merged_body) < text.index(reset_body)


def test_rotated_lineage_ambiguity_changed_row_and_finalization_fail_closed(
    tmp_path: Path,
) -> None:
    vault, config, _, database = setup(tmp_path)
    bind_session(
        vault,
        config,
        session_id="old-1",
        model="openrouter/anthropic/claude-sonnet-4",
        platform="telegram",
        native_store_ref=database,
    )
    insert(database, 1, "new-1", frame("Rotated native summary"))
    rotated = publish_compression(
        config,
        platform="telegram",
        session_id="new-1",
        old_session_id="old-1",
        in_place=False,
        compression_count=1,
        native_store_ref=database,
    )
    rebound = bind_session(
        vault,
        config,
        session_id="new-1",
        model="anthropic/claude-sonnet-4-6",
        platform="telegram",
        native_store_ref=database,
    )
    assert rebound["logical_session_id"] == "old-1" and rebound["injected"] is False
    audit = Path(config["worker"]["state_dir"]) / "audit"
    assert sum(len(path.read_text().splitlines()) for path in audit.glob("*.jsonl")) == 1

    insert(database, 2, "new-1", frame("ambiguous one"))
    insert(database, 3, "new-1", frame("ambiguous two"))
    ambiguous = publish_compression(
        config,
        platform="telegram",
        session_id="new-1",
        old_session_id=None,
        in_place=True,
        compression_count=2,
        native_store_ref=database,
    )
    descriptor = next(
        item
        for item in compression_descriptors(config["worker"]["state_dir"])
        if item["event_id"] == ambiguous["event_id"]
    )
    assert descriptor["summary_source"]["candidate_row_id"] is None

    insert(database, 4, "new-1", frame("will change"))
    changed = publish_compression(
        config,
        platform="telegram",
        session_id="new-1",
        old_session_id=None,
        in_place=True,
        compression_count=3,
        native_store_ref=database,
    )
    connection = sqlite3.connect(database)
    connection.execute("UPDATE messages SET content = ? WHERE id = 4", (frame("changed"),))
    connection.commit()
    connection.close()
    final = finalize_session(
        config,
        session_id="new-1",
        platform="telegram",
        native_store_ref=database,
    )
    repeated_final = finalize_session(
        config,
        session_id="new-1",
        platform="telegram",
        native_store_ref=database,
    )
    assert rotated["event_id"] != ambiguous["event_id"] != changed["event_id"]
    assert final["published"] is True
    assert repeated_final == {"published": False, "event_id": final["event_id"]}

    assert drain_once(vault, config)["failed"] == 0
    text = (vault / "sessions/hermes/2026/old-1.md").read_text()
    assert "Rotated native summary" in text
    assert text.count("native summary unavailable") >= 3
    assert "status: closed" in text
    assert not (vault / "sessions/hermes/2026/new-1.md").exists()


def test_persisted_nested_model_identity_is_authoritative_everywhere(tmp_path: Path) -> None:
    vault, config, _, database = setup(tmp_path)
    expected = "openrouter/anthropic/claude-opus-4.6"
    host_session(
        database,
        "nested-model",
        model="anthropic/claude-opus-4.6",
        provider="openrouter",
    )

    bind_session(
        vault,
        config,
        session_id="nested-model",
        model="other-provider/other-model",
        platform="cli",
        native_store_ref=database,
    )

    state_dir = Path(config["worker"]["state_dir"])
    state = json.loads((state_dir / "adapter-state/hermes-v0.20.0.json").read_text())
    assert state["sessions"]["nested-model"]["model"] == expected
    audit_record = json.loads(
        next((state_dir / "audit").glob("*.jsonl")).read_text().splitlines()[0]
    )
    assert audit_record["model"] == expected
    descriptor = next(
        read_descriptor(path)
        for path in queue_paths(state_dir).ready.glob("*.json")
        if read_descriptor(path)["event_kind"] == "session_start"
    )
    assert descriptor["host"]["model"] == expected


def test_real_plugin_and_gateway_hook_use_only_json_cli_and_never_block(
    tmp_path: Path, monkeypatch
) -> None:
    vault, config, config_path, database = setup(tmp_path)
    executable = Path(__file__).parents[2] / ".venv/bin/memory"
    monkeypatch.setenv("AGENT_MEMORY_CLI", str(executable))
    monkeypatch.setenv("AGENT_MEMORY_CONFIG", str(config_path))
    monkeypatch.setenv("HERMES_STATE_DB", str(database))
    plugin = module(Path("adapters/hermes/__init__.py"), "test_agent_memory_hermes_plugin")
    registered: list[str] = []

    class Context:
        def register_hook(self, name, callback):
            assert callable(callback)
            registered.append(name)

    plugin.register(Context())
    assert registered == [
        "pre_llm_call",
        "on_session_start",
        "on_session_finalize",
        "on_session_reset",
    ]
    host_session(database, "plugin-cli", use_model_config=True)
    kwargs = {
        "session_id": "plugin-cli",
        "model": "gpt-5.6-sol",
        "platform": "cli",
        "conversation_history": [{"role": "user", "content": "DO NOT STORE RAW PROMPT"}],
        "user_message": "DO NOT STORE RAW PROMPT",
    }
    first = plugin.pre_llm_call(**kwargs)
    second = plugin.pre_llm_call(**kwargs)
    assert "# Agent Memory" in first["context"]
    assert second is None
    adapter_state = json.loads(
        (Path(config["worker"]["state_dir"]) / "adapter-state/hermes-v0.20.0.json").read_text()
    )
    assert adapter_state["sessions"]["plugin-cli"]["model"] == "openai-codex/gpt-5.6-sol"
    start = next(
        read_descriptor(path)
        for path in queue_paths(config["worker"]["state_dir"]).ready.glob("*.json")
        if read_descriptor(path)["event_kind"] == "session_start"
    )
    assert start["host"]["model"] == "openai-codex/gpt-5.6-sol"
    assert start["session"]["started_at"].startswith("2026-")

    insert(database, 1, "plugin-cli", frame("Hook-isolated native summary"))
    hook = module(Path("adapters/hermes/gateway-hook/handler.py"), "test_agent_memory_gateway_hook")
    payload = {
        "platform": "cli",
        "session_id": "plugin-cli",
        "old_session_id": "",
        "in_place": True,
        "compression_count": 1,
    }
    asyncio.run(hook.handle("session:compress", payload))
    before = len(compression_descriptors(config["worker"]["state_dir"]))
    for forbidden in ("summary", "model", "timestamp", "native_event_id"):
        asyncio.run(hook.handle("session:compress", {**payload, forbidden: "rejected"}))
    assert len(compression_descriptors(config["worker"]["state_dir"])) == before == 1

    state_text = (
        Path(config["worker"]["state_dir"]) / "adapter-state/hermes-v0.20.0.json"
    ).read_text()
    descriptors = "".join(
        path.read_text() for path in queue_paths(config["worker"]["state_dir"]).ready.glob("*.json")
    )
    assert "DO NOT STORE RAW PROMPT" not in state_text + descriptors
    assert "Hook-isolated native summary" not in descriptors

    plugin.on_session_finalize(session_id="plugin-cli", platform="cli", reason="new_session")
    plugin.on_session_reset(session_id="new-plugin", platform="cli")
    monkeypatch.setenv("AGENT_MEMORY_CLI", str(tmp_path / "missing-memory"))
    assert "adapter unavailable" in plugin.pre_llm_call(**kwargs)["context"]
    plugin.on_session_finalize(session_id="plugin-cli", platform="cli")
    plugin.on_session_reset(session_id="failed-reset", platform="cli")
    assert not list(vault.rglob("state.db*"))


def test_rotated_pending_publication_recovers_before_child_bind(
    tmp_path: Path, monkeypatch
) -> None:
    vault, config, _, database = setup(tmp_path)
    bind_session(
        vault,
        config,
        session_id="pending-old",
        model="openrouter/anthropic/claude-sonnet-4",
        platform="telegram",
        native_store_ref=database,
    )
    insert(database, 1, "pending-new", frame("pending rotated summary"))
    real_publish = hermes_adapter.publish_descriptor

    def fail_publish(*args, **kwargs):
        raise OSError("synthetic publication crash")

    monkeypatch.setattr(hermes_adapter, "publish_descriptor", fail_publish)
    with pytest.raises(OSError, match="synthetic publication crash"):
        publish_compression(
            config,
            platform="telegram",
            session_id="pending-new",
            old_session_id="pending-old",
            in_place=False,
            compression_count=1,
            native_store_ref=database,
        )
    monkeypatch.setattr(hermes_adapter, "publish_descriptor", real_publish)
    rebound = bind_session(
        vault,
        config,
        session_id="pending-new",
        model="anthropic/claude-sonnet-4-6",
        platform="telegram",
        native_store_ref=database,
    )
    assert rebound == {
        "session_id": "pending-new",
        "logical_session_id": "pending-old",
        "injected": False,
        "notification_allowed": False,
    }
    state = json.loads(
        (Path(config["worker"]["state_dir"]) / "adapter-state/hermes-v0.20.0.json").read_text()
    )
    assert state["sessions"]["pending-new"]["injected"] is True
    assert state["sessions"]["pending-new"]["pending_compression"] is None


def test_pending_finalization_publication_recovers_on_later_bind(
    tmp_path: Path, monkeypatch
) -> None:
    vault, config, _, database = setup(tmp_path)
    sender_id = "private-owner-identity"
    sensitive_content = "SECRET LIVE USER REQUEST"
    bind_session(
        vault,
        config,
        session_id="recover-final",
        model="openrouter/anthropic/claude-sonnet-4",
        platform="telegram",
        sender_id=sender_id,
        native_store_ref=database,
    )
    insert(database, 1, "recover-final", sensitive_content, role="user")
    real_publish = hermes_adapter.publish_descriptor

    def fail_publish(*args, **kwargs):
        raise OSError("synthetic finalization publication crash")

    monkeypatch.setattr(hermes_adapter, "publish_descriptor", fail_publish)
    with pytest.raises(OSError, match="synthetic finalization publication crash"):
        finalize_session(
            config,
            session_id="recover-final",
            platform="telegram",
            native_store_ref=database,
        )

    state_path = Path(config["worker"]["state_dir"]) / "adapter-state/hermes-v0.20.0.json"
    failed_state = json.loads(state_path.read_text())
    pending = failed_state["sessions"]["recover-final"]["pending_finalizations"]
    assert len(pending) == 1

    monkeypatch.setattr(hermes_adapter, "publish_descriptor", real_publish)
    bind_session(
        vault,
        config,
        session_id="recover-final",
        model="openrouter/anthropic/claude-sonnet-4",
        platform="telegram",
        sender_id=sender_id,
        native_store_ref=database,
    )
    ready = queue_paths(config["worker"]["state_dir"]).ready
    descriptors = [read_descriptor(path) for path in ready.glob("*.json")]
    finalizations = [item for item in descriptors if item["event_kind"] == "finalize"]
    assert len(finalizations) == 1

    recovered_state = json.loads(state_path.read_text())
    record = recovered_state["sessions"]["recover-final"]
    assert record["pending_finalizations"] == {}
    assert list(record["finalizations"].values()) == [finalizations[0]["event_id"]]

    bind_session(
        vault,
        config,
        session_id="recover-final",
        model="openrouter/anthropic/claude-sonnet-4",
        platform="telegram",
        sender_id=sender_id,
        native_store_ref=database,
    )
    assert len([path for path in ready.glob("*.json")]) == len(descriptors)
    serialized = state_path.read_text() + "".join(path.read_text() for path in ready.glob("*.json"))
    assert sender_id not in serialized
    assert sensitive_content not in serialized


def test_unrelated_session_rows_do_not_change_replay_boundary(tmp_path: Path) -> None:
    vault, config, _, database = setup(tmp_path)
    for session_id in ("session-a", "session-b"):
        bind_session(
            vault,
            config,
            session_id=session_id,
            model="openrouter/anthropic/claude-sonnet-4",
            platform="telegram",
            native_store_ref=database,
        )
    insert(database, 1, "session-a", frame("summary a"))
    first = publish_compression(
        config,
        platform="telegram",
        session_id="session-a",
        old_session_id=None,
        in_place=True,
        compression_count=1,
        native_store_ref=database,
    )
    insert(database, 2, "session-b", frame("summary b"))
    replay = publish_compression(
        config,
        platform="telegram",
        session_id="session-a",
        old_session_id=None,
        in_place=True,
        compression_count=1,
        native_store_ref=database,
    )
    second = publish_compression(
        config,
        platform="telegram",
        session_id="session-b",
        old_session_id=None,
        in_place=True,
        compression_count=1,
        native_store_ref=database,
    )
    assert replay == {
        "event_id": first["event_id"],
        "replayed": True,
        "candidate_row_id": None,
    }
    assert second["event_id"] != first["event_id"]
    descriptors = compression_descriptors(config["worker"]["state_dir"])
    by_session = {item["summary_source"]["session_id"]: item for item in descriptors}
    assert by_session["session-a"]["summary_source"]["current_message_row_id"] == 1
    assert by_session["session-b"]["summary_source"]["current_message_row_id"] == 2


def test_reset_defers_start_until_pre_llm_and_preserves_reason(tmp_path: Path, monkeypatch) -> None:
    _, config, config_path, database = setup(tmp_path)
    executable = Path(__file__).parents[2] / ".venv/bin/memory"
    monkeypatch.setenv("AGENT_MEMORY_CLI", str(executable))
    monkeypatch.setenv("AGENT_MEMORY_CONFIG", str(config_path))
    monkeypatch.setenv("HERMES_STATE_DB", str(database))
    plugin = module(Path("adapters/hermes/__init__.py"), "test_agent_memory_reset_plugin")
    host_session(database, "old-session")
    assert plugin.pre_llm_call(session_id="old-session", model="gpt-5.6-sol", platform="cli")
    plugin.on_session_finalize(session_id="old-session", platform="cli", reason="new_session")
    plugin.on_session_reset(session_id="new-session", platform="cli")

    state_path = Path(config["worker"]["state_dir"]) / "adapter-state/hermes-v0.20.0.json"
    state = json.loads(state_path.read_text())
    assert state["sessions"]["new-session"]["start_published"] is False
    descriptors = [
        read_descriptor(path)
        for path in queue_paths(config["worker"]["state_dir"]).ready.glob("*.json")
    ]
    assert not any(
        item["event_kind"] == "session_start" and item["session"]["session_id"] == "new-session"
        for item in descriptors
    )
    final = next(item for item in descriptors if item["event_kind"] == "finalize")
    assert final["lifecycle"]["native_event_id"] == "hermes-new_session-old-session"

    host_session(database, "new-session", model="claude-sonnet-4-6", provider="anthropic")
    injected = plugin.pre_llm_call(
        session_id="new-session", model="claude-sonnet-4-6", platform="cli"
    )
    assert "# Agent Memory" in injected["context"]
    descriptors = [
        read_descriptor(path)
        for path in queue_paths(config["worker"]["state_dir"]).ready.glob("*.json")
    ]
    new_start = next(
        item
        for item in descriptors
        if item["event_kind"] == "session_start" and item["session"]["session_id"] == "new-session"
    )
    assert new_start["host"]["model"] == "anthropic/claude-sonnet-4-6"

    plugin.on_session_finalize(session_id="new-session", platform="cli", reason="shutdown")
    descriptors = [
        read_descriptor(path)
        for path in queue_paths(config["worker"]["state_dir"]).ready.glob("*.json")
    ]
    shutdown = next(
        item
        for item in descriptors
        if item["event_kind"] == "finalize" and item["session"]["session_id"] == "new-session"
    )
    assert shutdown["lifecycle"]["native_event_id"] == "hermes-shutdown-new-session"


def test_persisted_telegram_context_controls_safe_notification_delivery(tmp_path: Path) -> None:
    vault, config, _, database = setup(tmp_path)
    host_session(
        database,
        "dm-session",
        source="telegram",
        user_id="authenticated-owner",
        chat_type="dm",
    )
    allowed = bind_session(
        vault,
        config,
        session_id="dm-session",
        model="gpt-5.6-sol",
        platform="telegram",
        sender_id="authenticated-owner",
        native_store_ref=database,
    )
    assert allowed["notification_allowed"] is True

    host_session(
        database,
        "group-session",
        source="telegram",
        user_id="authenticated-owner",
        chat_type="group",
    )
    group = bind_session(
        vault,
        config,
        session_id="group-session",
        model="gpt-5.6-sol",
        platform="telegram",
        sender_id="authenticated-owner",
        native_store_ref=database,
    )
    assert group["notification_allowed"] is False

    host_session(
        database,
        "unknown-session",
        source="telegram",
        user_id="authenticated-owner",
        chat_type="dm",
    )
    unknown = bind_session(
        vault,
        config,
        session_id="unknown-session",
        model="gpt-5.6-sol",
        platform="telegram",
        native_store_ref=database,
    )
    assert unknown["notification_allowed"] is False
    with pytest.raises(HermesAdapterError, match="sender does not match"):
        bind_session(
            vault,
            config,
            session_id="dm-session",
            model="gpt-5.6-sol",
            platform="telegram",
            sender_id="different-user",
            native_store_ref=database,
        )
    with pytest.raises(HermesAdapterError, match="platform does not match"):
        bind_session(
            vault,
            config,
            session_id="dm-session",
            model="gpt-5.6-sol",
            platform="cli",
            sender_id="authenticated-owner",
            native_store_ref=database,
        )

    state_path = Path(config["worker"]["state_dir"]) / "adapter-state/hermes-v0.20.0.json"
    durable = state_path.read_text() + "".join(
        path.read_text() for path in queue_paths(config["worker"]["state_dir"]).ready.glob("*.json")
    )
    assert "authenticated-owner" not in durable
    state = json.loads(state_path.read_text())
    assert state["sessions"]["dm-session"]["chat_type"] == "dm"
    assert state["sessions"]["dm-session"]["sender_digest"]


def test_plugin_acknowledges_notifications_only_after_safe_bind_decision(tmp_path: Path) -> None:
    plugin = module(Path("adapters/hermes/__init__.py"), "test_agent_memory_notification_plugin")
    calls: list[list[str]] = []

    def fake(args: list[str]):
        calls.append(args)
        return {"notifications": [{"retry_id": "retry-1", "message": "sanitized worker failure"}]}

    plugin._run_json = fake
    assert plugin._notifications(session_id="s1", allowed=False) is None
    assert calls == []
    warning = plugin._notifications(session_id="s1", allowed=True)
    assert "sanitized worker failure" in warning
    assert any("--ack" in call for call in calls)


def test_hermes_doctor_checks_pinned_host_plugin_hook_and_enablement(
    tmp_path: Path, monkeypatch
) -> None:
    plugin = tmp_path / "plugins/agent-memory"
    hook = tmp_path / "hooks/agent-memory"
    plugin.mkdir(parents=True)
    hook.mkdir(parents=True)
    (plugin / "plugin.yaml").write_text(
        "name: agent-memory\nprovides_hooks:\n"
        "  - pre_llm_call\n  - on_session_start\n  - on_session_finalize\n"
        "  - on_session_reset\n"
    )
    (plugin / "__init__.py").write_text(
        f'HERMES_COMPAT_VERSION = "{HERMES_VERSION}"\n'
        "def pre_llm_call(): pass\n"
        "def on_session_start(): pass\n"
        "def on_session_finalize(): pass\n"
        "def on_session_reset(): pass\n"
        "def register(ctx): pass\n"
    )
    (hook / "HOOK.yaml").write_text("name: agent-memory\nevents:\n  - session:compress\n")
    (hook / "handler.py").write_text(
        f'HERMES_COMPAT_VERSION = "{HERMES_VERSION}"\nasync def handle(event, context): pass\n'
    )
    (tmp_path / "config.yaml").write_text("plugins:\n  enabled: [agent-memory]\n")

    def runner(argv):
        return subprocess.CompletedProcess(
            argv, 0, f"Hermes Agent v{HERMES_VERSION} ({HERMES_BUILD})\n", ""
        )

    report = hermes_adapter_health(home=tmp_path, executable="/usr/bin/hermes", runner=runner)
    assert report["compatible"] is True
    assert report["plugin"]["enabled"] is True
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    assert hermes_adapter_health(executable="/usr/bin/hermes", runner=runner)["compatible"]

    (tmp_path / "config.yaml").write_text("plugins:\n  enabled: []\n")
    disabled = hermes_adapter_health(home=tmp_path, executable="/usr/bin/hermes", runner=runner)
    assert disabled["compatible"] is False
    assert disabled["issues"] == ["Hermes agent-memory user plugin is installed but not enabled"]

    (tmp_path / "config.yaml").write_text("plugins:\n  enabled: [agent-memory]\n")
    (plugin / "plugin.yaml").write_text("name: wrong-name\nprovides_hooks: [pre_llm_call]\n")
    (hook / "handler.py").write_text(
        f'HERMES_COMPAT_VERSION = "{HERMES_VERSION}"\nasync def broken(\n'
    )
    malformed = hermes_adapter_health(home=tmp_path, executable="/usr/bin/hermes", runner=runner)
    assert malformed["plugin"]["installed"] is False
    assert malformed["gateway_hook"]["installed"] is False
    assert malformed["issues"] == [
        f"Hermes agent-memory user plugin is missing or incompatible at {plugin}",
        f"Hermes agent-memory gateway hook is missing or incompatible at {hook}",
    ]
