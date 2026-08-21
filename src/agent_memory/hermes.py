"""Hermes Agent 0.20.0 native summary carrier isolation."""

from __future__ import annotations

import hashlib
import hmac
import sqlite3
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from agent_memory.lifecycle import UNAVAILABLE

# Frozen from Hermes Agent 0.20.0, commit bc80a0be5c1b496a6212a1c6c594b3c5a78e31c6:
# agent/context_compressor.py and hermes_state_common.py. Do not infer newer formats.
SUMMARY_PREFIX = (
    "[CONTEXT COMPACTION — REFERENCE ONLY] Earlier turns were compacted into the summary "
    "below. This is a handoff from a previous context window — treat it as background "
    "reference, NOT as active instructions. Do NOT answer questions or fulfill requests "
    "mentioned in this summary; they were already addressed. Respond ONLY to the latest user "
    "message that appears AFTER this summary — that message is the single source of truth for "
    "what to do right now. If no user message appears AFTER this summary, do nothing: do not "
    "resume, wrap up, or continue work from '## Historical Task Snapshot' or any other section, "
    "do not call tools, and wait for a new user message. This handoff must never become the "
    "active turn by itself. (Exception: if tool results or your own tool calls appear after this "
    "summary, you are mid-way through an in-flight exchange — continue that exchange normally.) "
    "Topic overlap with the summary does NOT mean you should resume its task: even on similar "
    "topics, the latest user message WINS. Treat ONLY the latest message as the active task and "
    "discard stale items from '## Historical Task Snapshot' entirely — do not 'wrap up' or "
    "'finish' work described there unless the latest message explicitly asks for it. Reverse "
    "signals in the latest message (e.g. 'stop', 'undo', 'roll back', 'just verify', 'don't do "
    "that anymore', 'never mind', a new topic) must immediately end any in-flight work described "
    "in the summary; do not re-surface it in later turns. IMPORTANT: Your persistent memory "
    "(MEMORY.md, USER.md) in the system prompt is ALWAYS authoritative and active — never ignore "
    "or deprioritize memory content due to this compaction note. None of the above restricts HOW "
    "you work: your tools remain fully active — keep calling them normally for the active task "
    "(edit files, run commands, search) instead of merely narrating what you would do. The current "
    "session state (files, config, etc.) may reflect work described here — avoid repeating it:"
)
LEGACY_SUMMARY_PREFIX = "[CONTEXT SUMMARY]:"
HISTORICAL_SUMMARY_PREFIXES = (
    "[CONTEXT COMPACTION — REFERENCE ONLY] Earlier turns were compacted into the summary "
    "below. This is a handoff from a previous context window — treat it as background "
    "reference, NOT as active instructions. Do NOT answer questions or fulfill requests "
    "mentioned in this summary; they were already addressed. Respond ONLY to the latest user "
    "message that appears AFTER this summary — that message is the single source of truth for "
    "what to do right now. Topic overlap with the summary does NOT mean you should resume its "
    "task: even on similar topics, the latest user message WINS. Treat ONLY the latest message "
    "as the active task and discard stale items from '## Historical Task Snapshot' entirely — "
    "do not 'wrap up' or 'finish' work described there unless the latest message explicitly "
    "asks for it. Reverse signals in the latest message (e.g. 'stop', 'undo', 'roll back', "
    "'just verify', 'don't do that anymore', 'never mind', a new topic) must immediately end "
    "any in-flight work described in the summary; do not re-surface it in later turns. "
    "IMPORTANT: Your persistent memory (MEMORY.md, USER.md) in the system prompt is ALWAYS "
    "authoritative and active — never ignore or deprioritize memory content due to this "
    "compaction note. None of the above restricts HOW you work: your tools remain fully active "
    "— keep calling them normally for the active task (edit files, run commands, search) "
    "instead of merely narrating what you would do. The current session state (files, config, "
    "etc.) may reflect work described here — avoid repeating it:",
    "[CONTEXT COMPACTION — REFERENCE ONLY] Earlier turns were compacted into the summary "
    "below. This is a handoff from a previous context window — treat it as background "
    "reference, NOT as active instructions. Do NOT answer questions or fulfill requests "
    "mentioned in this summary; they were already addressed. Respond ONLY to the latest user "
    "message that appears AFTER this summary — that message is the single source of truth for "
    "what to do right now. Topic overlap with the summary does NOT mean you should resume its "
    "task: even on similar topics, the latest user message WINS. Treat ONLY the latest message "
    "as the active task and discard stale items from '## Historical Task Snapshot' / '## "
    "Historical In-Progress State' / '## Historical Pending User Asks' / '## Historical "
    "Remaining Work' entirely — do not 'wrap up' or 'finish' work described there unless the "
    "latest message explicitly asks for it. Reverse signals in the latest message (e.g. "
    "'stop', 'undo', 'roll back', 'just verify', 'don't do that anymore', 'never mind', a new "
    "topic) must immediately end any in-flight work described in the summary; do not re-surface "
    "it in later turns. IMPORTANT: Your persistent memory (MEMORY.md, USER.md) in the system "
    "prompt is ALWAYS authoritative and active — never ignore or deprioritize memory content "
    "due to this compaction note. None of the above restricts HOW you work: your tools remain "
    "fully active — keep calling them normally for the active task (edit files, run commands, "
    "search) instead of merely narrating what you would do. The current session state (files, "
    "config, etc.) may reflect work described here — avoid repeating it:",
    "[CONTEXT COMPACTION — REFERENCE ONLY] Earlier turns were compacted into the summary "
    "below. This is a handoff from a previous context window — treat it as background "
    "reference, NOT as active instructions. Do NOT answer questions or fulfill requests "
    "mentioned in this summary; they were already addressed. Respond ONLY to the latest user "
    "message that appears AFTER this summary — that message is the single source of truth for "
    "what to do right now. Topic overlap with the summary does NOT mean you should resume its "
    "task: even on similar topics, the latest user message WINS. Treat ONLY the latest message "
    "as the active task and discard stale items from '## Historical Task Snapshot' / '## "
    "Historical In-Progress State' / '## Historical Pending User Asks' / '## Historical "
    "Remaining Work' entirely — do not 'wrap up' or 'finish' work described there unless the "
    "latest message explicitly asks for it. Reverse signals in the latest message (e.g. "
    "'stop', 'undo', 'roll back', 'just verify', 'don't do that anymore', 'never mind', a new "
    "topic) must immediately end any in-flight work described in the summary; do not re-surface "
    "it in later turns. IMPORTANT: Your persistent memory (MEMORY.md, USER.md) in the system "
    "prompt is ALWAYS authoritative and active — never ignore or deprioritize memory content "
    "due to this compaction note. The current session state (files, config, etc.) may reflect "
    "work described here — avoid repeating it:",
    "[CONTEXT COMPACTION — REFERENCE ONLY] Earlier turns were compacted into the summary "
    "below. This is a handoff from a previous context window — treat it as background "
    "reference, NOT as active instructions. Do NOT answer questions or fulfill requests "
    "mentioned in this summary; they were already addressed. Respond ONLY to the latest user "
    "message that appears AFTER this summary — that message is the single source of truth for "
    "what to do right now. If the latest user message is consistent with the '## Active Task' "
    "section, you may use the summary as background. If the latest user message contradicts, "
    "supersedes, changes topic from, or in any way diverges from '## Active Task' / '## In "
    "Progress' / '## Pending User Asks' / '## Remaining Work', the latest message WINS — "
    "discard those stale items entirely and do not 'wrap up the old task first'. Reverse "
    "signals in the latest message (e.g. 'stop', 'undo', 'roll back', 'just verify', 'don't do "
    "that anymore', 'never mind', a new topic) must immediately end any in-flight work described "
    "in the summary; do not re-surface it in later turns. IMPORTANT: Your persistent memory "
    "(MEMORY.md, USER.md) in the system prompt is ALWAYS authoritative and active — never "
    "ignore or deprioritize memory content due to this compaction note. The current session "
    "state (files, config, etc.) may reflect work described here — avoid repeating it:",
    "[CONTEXT COMPACTION — REFERENCE ONLY] Earlier turns were compacted into the summary "
    "below. This is a handoff from a previous context window — treat it as background "
    "reference, NOT as active instructions. Do NOT answer questions or fulfill requests "
    "mentioned in this summary; they were already addressed. Your current task is identified "
    "in the '## Active Task' section of the summary — resume exactly from there. Respond ONLY "
    "to the latest user message that appears AFTER this summary. The current session state "
    "(files, config, etc.) may reflect work described here — avoid repeating it:",
)
SUMMARY_PREFIXES = (SUMMARY_PREFIX, *HISTORICAL_SUMMARY_PREFIXES, LEGACY_SUMMARY_PREFIX)
SUMMARY_END_MARKER = (
    "--- END OF CONTEXT SUMMARY — respond to the message below, not the summary above ---"
)
MERGED_PRIOR_CONTEXT_HEADER = "[PRIOR CONTEXT — for reference only; not a new message]"
MERGED_SUMMARY_DELIMITER = "[END OF PRIOR CONTEXT — COMPACTION SUMMARY BELOW]"


def isolate_summary(
    content: object, *, role: object, active: object, compacted: object
) -> str | None:
    """Return one isolated 0.20.0 body, otherwise None without diagnostic content."""

    if not isinstance(content, str) or role not in {"user", "assistant"}:
        return None
    if active != 1 or compacted != 0 or content.count(SUMMARY_END_MARKER) != 1:
        return None
    text = content.strip()
    merged = MERGED_SUMMARY_DELIMITER in text
    if merged:
        if (
            text.count(MERGED_SUMMARY_DELIMITER) != 1
            or text.count(MERGED_PRIOR_CONTEXT_HEADER) != 1
            or not text.startswith(MERGED_PRIOR_CONTEXT_HEADER)
        ):
            return None
        frame = text.split(MERGED_SUMMARY_DELIMITER, 1)[1].lstrip()
    else:
        if MERGED_PRIOR_CONTEXT_HEADER in text:
            return None
        frame = text
    matching = [prefix for prefix in SUMMARY_PREFIXES if frame.startswith(prefix)]
    if len(matching) != 1 or sum(frame.count(prefix) for prefix in SUMMARY_PREFIXES) != 1:
        return None
    prefix = matching[0]
    end = frame.find(SUMMARY_END_MARKER, len(prefix))
    if end < 0 or frame[end + len(SUMMARY_END_MARKER) :].strip():
        return None
    body = frame[len(prefix) : end].strip()
    return body or None


def _read_exact_row(database: Path, source: Mapping[str, Any]) -> tuple[Any, ...] | None:
    uri = database.resolve().as_uri() + "?mode=ro"
    connection = sqlite3.connect(uri, uri=True)
    try:
        # Exact candidate only: no interval scan, archived transcript read, or repair search.
        return connection.execute(
            "SELECT id, session_id, role, content, active, compacted FROM messages "
            "WHERE id = ? AND id > ? AND id <= ?",
            (
                source["candidate_row_id"],
                source["previous_message_row_id"],
                source["current_message_row_id"],
            ),
        ).fetchone()
    finally:
        connection.close()


def resolve_summary(native_store_ref: str | None, source: Mapping[str, Any]) -> str:
    """Verify one descriptor-bound Hermes row and return only its isolated body."""

    candidate = source.get("candidate_row_id")
    expected_hash = source.get("candidate_summary_sha256")
    source_session = source.get("session_id")
    old_session = source.get("old_session_id")
    if (
        candidate is None
        or expected_hash is None
        or not native_store_ref
        or (source.get("in_place") is True and old_session not in {None, source_session})
    ):
        return UNAVAILABLE
    try:
        row = _read_exact_row(Path(native_store_ref), source)
    except (OSError, sqlite3.Error, TypeError, ValueError):
        return UNAVAILABLE
    if row is None:
        return UNAVAILABLE
    row_id, session_id, role, content, active, compacted = row
    allowed_sessions = {source_session}
    if source.get("in_place") is False:
        allowed_sessions.add(old_session)
    if row_id != candidate or session_id not in allowed_sessions:
        return UNAVAILABLE
    body = isolate_summary(content, role=role, active=active, compacted=compacted)
    if body is None:
        return UNAVAILABLE
    digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
    return body if hmac.compare_digest(digest, expected_hash) else UNAVAILABLE
