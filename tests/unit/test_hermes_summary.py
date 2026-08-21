from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path

import pytest

from agent_memory.hermes import isolate_summary, resolve_summary
from agent_memory.lifecycle import UNAVAILABLE
from tests.fixtures.hermes_020 import (
    MERGED_PRIOR_CONTEXT_HEADER,
    MERGED_SUMMARY_DELIMITER,
    MESSAGES_SCHEMA,
    RECOGNIZED_SUMMARY_PREFIXES,
    SUMMARY_END_MARKER,
    SUMMARY_PREFIX,
)


def frame(body: str, prefix: str = SUMMARY_PREFIX) -> str:
    return f"{prefix}\n{body}\n{SUMMARY_END_MARKER}"


def source(body: str, row: int = 11) -> dict:
    return {
        "kind": "hermes-0.20.0",
        "platform": "telegram",
        "session_id": "h-1",
        "old_session_id": None,
        "in_place": True,
        "compression_count": 1,
        "previous_message_row_id": 10,
        "current_message_row_id": 20,
        "candidate_row_id": row,
        "candidate_summary_sha256": hashlib.sha256(body.encode()).hexdigest(),
    }


def database(path: Path, content: str, *, session: str = "h-1", active: int = 1) -> None:
    connection = sqlite3.connect(path)
    connection.executescript(MESSAGES_SCHEMA)
    connection.execute(
        "INSERT INTO messages VALUES (11, ?, 'assistant', ?, ?, 0)",
        (session, content, active),
    )
    connection.commit()
    connection.close()


@pytest.mark.parametrize("prefix", RECOGNIZED_SUMMARY_PREFIXES)
def test_every_frozen_prefix_isolates_standalone_and_merged(prefix: str) -> None:
    body = "isolated native body"
    assert isolate_summary(frame(body, prefix), role="assistant", active=1, compacted=0) == body
    merged = (
        f"{MERGED_PRIOR_CONTEXT_HEADER}\nSECRET PRESERVED TAIL\n"
        f"{MERGED_SUMMARY_DELIMITER}\n{frame(body, prefix)}"
    )
    assert isolate_summary(merged, role="user", active=1, compacted=0) == body


def test_standalone_and_merged_isolate_only_native_body(tmp_path: Path) -> None:
    body = "## Summary\nOnly this body."
    assert isolate_summary(frame(body), role="assistant", active=1, compacted=0) == body
    merged = (
        f"{MERGED_PRIOR_CONTEXT_HEADER}\nSECRET PRESERVED TAIL\n"
        f"{MERGED_SUMMARY_DELIMITER}\n{frame(body)}"
    )
    assert isolate_summary(merged, role="user", active=1, compacted=0) == body
    path = tmp_path / "state.db"
    database(path, merged)
    assert resolve_summary(str(path), source(body)) == body
    assert "PRESERVED" not in resolve_summary(str(path), source(body))


def test_ambiguity_classification_bounds_lineage_and_hash_fail_closed(tmp_path: Path) -> None:
    body = "bounded native body"
    malformed = [
        frame(body) + SUMMARY_END_MARKER,
        frame(body).replace(SUMMARY_END_MARKER, ""),
        SUMMARY_END_MARKER + frame(body),
        frame(body) + " trailing live dialogue",
        f"{SUMMARY_PREFIX}\n{SUMMARY_PREFIX}\n{body}\n{SUMMARY_END_MARKER}",
        f"{MERGED_PRIOR_CONTEXT_HEADER}{MERGED_SUMMARY_DELIMITER}x"
        f"{MERGED_SUMMARY_DELIMITER}{frame(body)}",
    ]
    assert all(
        isolate_summary(value, role="assistant", active=1, compacted=0) is None
        for value in malformed
    )
    assert isolate_summary(frame(body), role="tool", active=1, compacted=0) is None
    assert isolate_summary(frame(body), role="assistant", active=0, compacted=1) is None

    path = tmp_path / "state.db"
    database(path, frame(body), session="other")
    assert resolve_summary(str(path), source(body)) == UNAVAILABLE
    assert resolve_summary(str(path), {**source(body), "candidate_row_id": 21}) == UNAVAILABLE
    assert (
        resolve_summary(
            str(path),
            {**source(body), "candidate_summary_sha256": "0" * 64},
        )
        == UNAVAILABLE
    )
    assert resolve_summary(str(path), {**source(body), "candidate_row_id": None}) == UNAVAILABLE


def test_in_place_foreign_old_session_cannot_resolve_exact_row(tmp_path: Path) -> None:
    body = "foreign row must not be accepted"
    path = tmp_path / "state.db"
    database(path, frame(body), session="foreign")
    hostile = {**source(body), "old_session_id": "foreign", "in_place": True}
    assert resolve_summary(str(path), hostile) == UNAVAILABLE
