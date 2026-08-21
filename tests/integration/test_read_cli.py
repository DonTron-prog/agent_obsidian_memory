from __future__ import annotations

import json
import multiprocessing
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from agent_memory.audit import (
    AuditError,
    RetrievalContext,
    append_access_event,
    read_access_events,
    spool_path,
)
from agent_memory.cli import main
from agent_memory.search import resolve_concept, search_concepts
from agent_memory.vault import discover_vault, scan_concepts
from tests.fixtures.builders import build_vault, concept_text


def _append_then_exit(state_dir: str, session_id: str, number: int) -> None:
    append_access_event(
        state_dir,
        RetrievalContext(session_id, "pi/0.84.2", "openai/gpt-5"),
        mode="search",
        query=f"query-{number}",
        concepts=["concepts/example-concept"],
    )
    os._exit(0)


def _config(tmp_path: Path, vault: Path) -> Path:
    path = tmp_path / "memory.yaml"
    path.write_text(
        f"vault: {vault}\nworker:\n  state_dir: {tmp_path / 'state'}\n",
        encoding="utf-8",
    )
    return path


def _snapshot(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }


def test_retrieval_context_rejects_model_whitespace() -> None:
    with pytest.raises(AuditError, match="exact provider/model"):
        RetrievalContext("session", "pi", " openai/gpt-5")
    with pytest.raises(AuditError, match="exact provider/model"):
        RetrievalContext("session", "pi", "openai/gpt-5 ")


@pytest.mark.parametrize(
    ("field", "secret"),
    (
        ("session_id", "api_key=abcdefghijklmnop"),
        ("agent", "access_token=abcdefghijklmnop"),
        ("model", "openai/sk-live-abcdefghijklmnop"),
    ),
)
def test_retrieval_context_rejects_secret_bearing_provenance(field: str, secret: str) -> None:
    values = {"session_id": "session", "agent": "pi", "model": "openai/gpt-5"}
    values[field] = secret
    with pytest.raises(AuditError, match="sensitive content"):
        RetrievalContext(**values)


def test_cli_root_search_show_audit_flow_does_not_write_vault(
    tmp_path: Path, capsys, monkeypatch
) -> None:
    vault_root = build_vault(
        tmp_path / "vault",
        {"example-concept": concept_text(body="# Example\n\nDurable needle.\n")},
    )
    config = _config(tmp_path, vault_root)
    before = _snapshot(vault_root)
    monkeypatch.setenv("PI_SESSION_ID", "wrong-session")
    monkeypatch.setenv("PI_PROVIDER", "wrong")
    monkeypatch.setenv("PI_MODEL", "wrong")

    search_code = main(
        [
            "search",
            "needle",
            "--config",
            str(config),
            "--session-id",
            "session-1",
            "--agent",
            "pi/0.84.2",
            "--model",
            "openai/gpt-5",
            "--reason",
            "integration flow",
            "--json",
        ]
    )
    search_payload = json.loads(capsys.readouterr().out)
    show_code = main(
        [
            "show",
            "example-concept",
            "--config",
            str(config),
            "--session-id",
            "session-1",
            "--agent",
            "pi/0.84.2",
            "--model",
            "openai/gpt-5",
            "--reason",
            "open selected result",
            "--json",
        ]
    )
    show_payload = json.loads(capsys.readouterr().out)

    assert search_code == show_code == 0
    assert search_payload["results"][0]["id"] == "concepts/example-concept"
    assert search_payload["results"][0]["matched_fields"] == ["body"]
    assert show_payload["body"].endswith("Durable needle.\n")
    assert _snapshot(vault_root) == before

    spool = spool_path(tmp_path / "state", "session-1")
    events = [json.loads(line) for line in spool.read_text(encoding="utf-8").splitlines()]
    assert [event["mode"] for event in events] == ["search", "show"]
    assert events[0] == {
        "event_id": events[0]["event_id"],
        "agent": "pi/0.84.2",
        "concepts": ["concepts/example-concept"],
        "mode": "search",
        "model": "openai/gpt-5",
        "query": "needle",
        "reason": "integration flow",
        "session_id": "session-1",
        "timestamp": events[0]["timestamp"],
    }
    assert "body" not in events[0]


def test_audited_retrieval_requires_context_and_human_show_can_opt_out(
    tmp_path: Path, capsys, monkeypatch
) -> None:
    config = _config(tmp_path, build_vault(tmp_path / "vault"))
    for name in (
        "PI_SESSION_ID",
        "HERMES_SESSION_ID",
        "MEMORY_SESSION_ID",
        "MEMORY_AGENT",
        "MEMORY_MODEL",
        "PI_PROVIDER",
        "PI_MODEL",
        "HERMES_MODEL",
    ):
        monkeypatch.delenv(name, raising=False)

    assert main(["search", "fixture", "--config", str(config), "--json"]) == 2
    assert "session ID" in json.loads(capsys.readouterr().out)["error"]
    assert (
        main(
            [
                "show",
                "example-concept",
                "--config",
                str(config),
                "--no-audit",
                "--json",
            ]
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out)["id"] == "concepts/example-concept"
    assert not (tmp_path / "state" / "audit").exists()


def test_audit_state_cannot_be_inside_synchronized_vault(tmp_path: Path, capsys) -> None:
    root = build_vault(tmp_path / "vault")
    config = tmp_path / "unsafe.yaml"
    config.write_text(f"vault: {root}\nworker:\n  state_dir: {root / 'state'}\n", encoding="utf-8")

    code = main(
        [
            "show",
            "example-concept",
            "--config",
            str(config),
            "--session-id",
            "session",
            "--agent",
            "pi",
            "--model",
            "openai/gpt-5",
            "--json",
        ]
    )

    assert code == 2
    assert "outside" in json.loads(capsys.readouterr().out)["error"]
    assert not (root / "state").exists()


def test_audit_redacts_secret_fragments_before_durable_spooling(tmp_path: Path) -> None:
    state = tmp_path / "state"
    append_access_event(
        state,
        RetrievalContext("session", "pi/1", "openai/gpt-5"),
        mode="search",
        query="raw prompt contains api_key=abcdefghijklmnop",
        concepts=[],
    )
    text = spool_path(state, "session").read_text()
    assert "abcdefghijklmnop" not in text
    assert "redacted sensitive content" in text

    event = json.loads(text)
    event["concepts"] = [{"not": "text"}]
    spool_path(state, "session").write_text(json.dumps(event) + "\n")
    with pytest.raises(AuditError, match="concepts are invalid"):
        read_access_events(
            state,
            "session",
            start=0,
            end=spool_path(state, "session").stat().st_size,
        )


def test_legacy_audit_redacts_query_reason_and_concepts_on_read(tmp_path: Path) -> None:
    state = tmp_path / "state"
    path = spool_path(state, "session")
    path.parent.mkdir(parents=True)
    legacy = {
        "timestamp": "2026-01-02T03:04:05Z",
        "mode": "search",
        "agent": "pi/1",
        "model": "openai/gpt-5",
        "session_id": "session",
        "query": "api_key=abcdefghijklmnop",
        "reason": "password=qrstuvwxyzabcdef",
        "concepts": ["access_token=zyxwvutsrqponmlk"],
    }
    path.write_text(json.dumps(legacy) + "\n", encoding="utf-8")

    records = read_access_events(state, "session", start=0, end=path.stat().st_size)

    assert records[0]["query"] == "[redacted sensitive content]"
    assert records[0]["reason"] == "[redacted sensitive content]"
    assert records[0]["concepts"] == ["[redacted sensitive content]"]


def test_concurrent_process_exit_appends_preserve_complete_jsonl(tmp_path: Path) -> None:
    state = tmp_path / "state"
    process_count = 12
    processes = [
        multiprocessing.Process(
            target=_append_then_exit, args=(str(state), "shared/session", number)
        )
        for number in range(process_count)
    ]
    for process in processes:
        process.start()
    for process in processes:
        process.join(timeout=10)
        assert process.exitcode == 0

    path = spool_path(state, "shared/session")
    records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert len(records) == process_count
    assert {record["query"] for record in records} == {
        f"query-{number}" for number in range(process_count)
    }
    assert path.parent == state / "audit"
    assert "shared/session" not in path.name


def test_concurrent_readers_share_markdown_without_a_writer_lock(tmp_path: Path) -> None:
    root = build_vault(tmp_path / "vault")
    vault = discover_vault(root)
    before = _snapshot(root)

    def read_once(_: int) -> str:
        concepts = scan_concepts(vault)
        assert search_concepts(concepts, "fixture")
        return resolve_concept(concepts, "example-concept").concept_id

    with ThreadPoolExecutor(max_workers=8) as executor:
        assert set(executor.map(read_once, range(40))) == {"concepts/example-concept"}
    assert _snapshot(root) == before


def test_validate_json_and_strict_warning_exit(tmp_path: Path, capsys) -> None:
    root = build_vault(tmp_path / "vault")
    config = _config(tmp_path, root)

    assert main(["--config", str(config), "validate", "--strict", "--json"]) == 0
    assert json.loads(capsys.readouterr().out) == {"issues": [], "ok": True, "strict": True}

    (root / "memory" / "concepts" / "example-concept.md").write_text(
        concept_text(body="# Example\n\n[missing](missing.md)\n"), encoding="utf-8"
    )
    assert main(["validate", "--config", str(config), "--strict", "--json"]) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert any(issue["field"] == "link" for issue in payload["issues"])
