"""Stable evolving session Markdown and transactional lifecycle materialization."""

from __future__ import annotations

import html
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ruamel.yaml.comments import CommentedMap

from agent_memory.audit import advance_cursor, read_access_events, read_cursor
from agent_memory.hermes import resolve_summary
from agent_memory.lifecycle import UNAVAILABLE, validate_descriptor
from agent_memory.locking import writer_lock
from agent_memory.markdown import FrontmatterDocument, parse_frontmatter, render_frontmatter
from agent_memory.transactions import execute_transaction
from agent_memory.validation import DEFAULT_TYPES

CONTEXT_START = "<!-- agent-memory:context-access:start -->"
CONTEXT_END = "<!-- agent-memory:context-access:end -->"
INDEX_START = "<!-- agent-memory:checkpoint-index:start -->"
INDEX_END = "<!-- agent-memory:checkpoint-index:end -->"
CHECKPOINT = re.compile(
    r"(?ms)^## Checkpoint (?P<number>\d+) — (?P<trigger>[^\n]+)\n"
    r"<!-- lifecycle-event:(?P<event>[^>]+) -->\n.*?(?=^## Checkpoint \d+ — |\Z)"
)
ACCESS = re.compile(r"<!-- access-event:([^>]+) -->")


@dataclass(frozen=True)
class MaterializationResult:
    changed: bool
    session_path: str
    checkpoint_count: int
    commit_hash: str | None


def session_path(vault: Path, descriptor: Mapping[str, Any]) -> Path:
    session = descriptor["session"]
    agent_root = vault / "sessions" / session["agent"]
    existing = list(agent_root.glob(f"*/{session['session_id']}.md"))
    if len(existing) > 1:
        raise ValueError("logical session ID maps to multiple session files")
    if existing:
        return existing[0]
    year = session["started_at"][:4]
    if not year.isdigit():
        raise ValueError("session start year is invalid")
    return agent_root / year / f"{session['session_id']}.md"


def _clean_cell(value: object) -> str:
    text = str(value or "").replace("\r", " ").replace("\n", " ").replace("|", "\\|")
    text = text.replace("<!--", "&lt;!--")
    return html.escape(text[:300], quote=False)


def _access_region(events: list[Mapping[str, Any]]) -> str:
    lines = [
        CONTEXT_START,
        "## Context Access",
        "",
        "| Time | Mode | Query or reason | Concepts | Model |",
        "|---|---|---|---|---|",
    ]
    for event in events:
        reason = event.get("reason") or event.get("query") or ""
        concepts = str(event.get("resource") or ", ".join(event.get("concepts", [])))
        lines.extend(
            [
                f"<!-- access-event:{event['event_id']} -->",
                "| "
                + " | ".join(
                    _clean_cell(item)
                    for item in (
                        event.get("timestamp"),
                        event.get("mode"),
                        reason,
                        concepts,
                        event.get("model"),
                    )
                )
                + " |",
            ]
        )
    return "\n".join(lines + [CONTEXT_END])


def _replace_region(body: str, start: str, end: str, replacement: str) -> str:
    pattern = re.compile(re.escape(start) + r".*?" + re.escape(end), re.S)
    if pattern.search(body):
        return pattern.sub(replacement, body, count=1)
    return body.rstrip() + "\n\n" + replacement + "\n"


def _checkpoint_blocks(body: str) -> list[re.Match[str]]:
    return list(CHECKPOINT.finditer(body))


def _index_region(body: str) -> str:
    lines = [INDEX_START, "## Checkpoint Index", ""]
    for match in _checkpoint_blocks(body):
        number = match.group("number")
        trigger = match.group("trigger")
        anchor = f"checkpoint-{number}--{trigger.casefold().replace(' ', '-')}"
        lines.append(f"{number}. [{trigger}](#{anchor})")
    return "\n".join(lines + [INDEX_END])


def _initial_document(descriptor: Mapping[str, Any]) -> FrontmatterDocument:
    session = descriptor["session"]
    model = descriptor["host"].get("model")
    metadata = CommentedMap(
        {
            "agent": session["agent"],
            "agent_version": session["agent_version"],
            "session_id": session["session_id"],
            "started_at": session["started_at"],
            "updated_at": descriptor["lifecycle"]["occurred_at"],
            "status": "active",
            "checkpoint_count": 0,
            "host_models": [model] if model else [],
            "summary_policy": "native-only",
        }
    )
    title = f"# {session['agent'].title()} Session {session['session_id']}"
    start_marker = (
        f"\n<!-- session-start-event:{descriptor['event_id']} -->"
        if descriptor["event_kind"] == "session_start"
        else ""
    )
    body = f"{title}{start_marker}\n\n{_access_region([])}\n\n{_index_region('')}\n"
    return FrontmatterDocument(metadata, body)


def _safe_native_markdown(text: str) -> str:
    """Neutralize only generated-region syntax while preserving summary prose/Markdown."""

    text = text.replace("<!--", "&lt;!--").replace("-->", "--&gt;")
    return re.sub(
        r"(?m)^(## Checkpoint \d+ — )",
        lambda match: "&#35;" + match.group(1)[1:],
        text,
    )


def _summary(descriptor: Mapping[str, Any]) -> tuple[str, str]:
    source = descriptor["summary_source"]
    if source["kind"] == "pi":
        return _safe_native_markdown(source["summary"]), (
            f"Pi compaction `{source['compaction_entry_id']}`"
        )
    if source["kind"] == "hermes-0.20.0":
        text = resolve_summary(descriptor["session"].get("native_store_ref"), source)
        identity = (
            f"Hermes 0.20.0 row `{source.get('candidate_row_id')}` within "
            f"`({source['previous_message_row_id']}, {source['current_message_row_id']}]`"
        )
        return _safe_native_markdown(text), identity
    return UNAVAILABLE, "Unavailable"


def _checkpoint(descriptor: Mapping[str, Any], number: int) -> str:
    lifecycle = descriptor["lifecycle"]
    trigger = lifecycle["trigger"].title()
    summary, source = _summary(descriptor)
    model = descriptor["host"].get("model")
    lines = [
        f"## Checkpoint {number} — {trigger}",
        f"<!-- lifecycle-event:{descriptor['event_id']} -->",
        "",
        f"- **Time:** {lifecycle['occurred_at']}",
        f"- **Event ID:** `{descriptor['event_id']}`",
        f"- **Trigger:** {trigger}",
        f"- **Native summary source:** {source}",
    ]
    session = descriptor["session"]
    host = descriptor["host"]
    lines.extend(
        [
            f"- **Agent/version:** `{session['agent']}/{session['agent_version']}`",
            f"- **Logical session:** `{session['session_id']}`",
            f"- **Session started:** `{session['started_at']}`",
        ]
    )
    native_event_id = lifecycle.get("native_event_id")
    if native_event_id:
        lines.append(f"- **Native event ID:** `{native_event_id}`")
    if host.get("platform"):
        lines.append(f"- **Platform:** `{host['platform']}`")
    native_store_ref = session.get("native_store_ref")
    if native_store_ref and not Path(native_store_ref).is_absolute():
        safe_ref = html.escape(native_store_ref, quote=False).replace("`", "&#96;")
        lines.append(f"- **Native store reference:** `{safe_ref}`")
    if model:
        lines.append(f"- **Host model:** `{model}`")
    source_value = descriptor["summary_source"]
    if source_value["kind"] == "hermes-0.20.0":
        lines.extend(
            [
                f"- **Hermes source session:** `{source_value['session_id']}`",
                f"- **Hermes old session:** `{source_value['old_session_id'] or 'none'}`",
                f"- **Hermes in-place/count:** `{str(source_value['in_place']).lower()}` / "
                f"`{source_value['compression_count']}`",
                f"- **Hermes row boundary:** "
                f"`({source_value['previous_message_row_id']}, "
                f"{source_value['current_message_row_id']}]`",
                f"- **Hermes candidate row:** `{source_value['candidate_row_id'] or 'none'}`",
                f"- **Hermes candidate SHA-256:** "
                f"`{source_value['candidate_summary_sha256'] or 'none'}`",
            ]
        )
    lines.extend(["", "### Native Summary", "", summary, ""])
    return "\n".join(lines)


def _status_update(text: str, descriptor: Mapping[str, Any], status: str) -> str:
    event_id = descriptor["event_id"]
    session_id = descriptor["session"]["session_id"]
    marker = f"<!-- session-status:{descriptor['session']['agent']}:{session_id} -->"
    block = (
        f"{marker}\n- **{descriptor['session']['agent']}/{session_id}:** {status}; "
        f"latest `{event_id}` at {descriptor['lifecycle']['occurred_at']}\n"
    )
    pattern = re.compile(re.escape(marker) + r"\n.*?(?=<!-- session-status:|\Z)", re.S)
    return (
        pattern.sub(block, text, count=1)
        if pattern.search(text)
        else text.rstrip() + "\n\n" + block
    )


def materialize_descriptor(
    vault: Path,
    config: Mapping[str, Any],
    descriptor: Mapping[str, Any],
    *,
    fault_hook=None,
) -> MaterializationResult:
    """Commit one lifecycle descriptor, then acknowledge its bounded audit records."""

    descriptor = validate_descriptor(dict(descriptor))
    path = session_path(vault, descriptor)
    relative = path.relative_to(vault).as_posix()
    state_dir = Path(config["worker"]["state_dir"])
    transaction_state = Path(config["transactions"]["state_dir"])
    with writer_lock(
        transaction_state / "writer.lock",
        timeout=float(config["locking"]["timeout_seconds"]),
        command="lifecycle materialization",
        actor="process:memory-worker",
    ):
        existed = path.exists()
        document = (
            parse_frontmatter(path.read_text(encoding="utf-8"))
            if existed
            else _initial_document(descriptor)
        )
        metadata = document.metadata
        session = descriptor["session"]
        if (
            metadata.get("session_id") != session["session_id"]
            or metadata.get("agent") != session["agent"]
            or str(metadata.get("started_at")) != session["started_at"]
        ):
            raise ValueError("session file identity does not match lifecycle descriptor")
        old_status = str(metadata.get("status", "active"))
        if old_status not in {"active", "closed", "incomplete"}:
            raise ValueError("session status is invalid")
        body = document.body
        start_marker = f"<!-- session-start-event:{descriptor['event_id']} -->"
        duplicate = existed and (
            f"<!-- lifecycle-event:{descriptor['event_id']} -->" in body or start_marker in body
        )
        model = descriptor["host"].get("model")
        if not duplicate:
            models = list(
                dict.fromkeys([*metadata.get("host_models", []), *([model] if model else [])])
            )
            metadata["host_models"] = models
            metadata["agent_version"] = session["agent_version"]
            metadata["updated_at"] = descriptor["lifecycle"]["occurred_at"]
            terminal = descriptor["lifecycle"]["trigger"] in {
                "reset",
                "new",
                "finalization",
            }
            if terminal:
                metadata["status"] = "closed"
            elif old_status not in {"closed", "incomplete"}:
                metadata["status"] = "active"
        existing_events = set(ACCESS.findall(body))
        cursor = read_cursor(state_dir, session["session_id"])
        bound = descriptor["audit_through_offset"]
        new_events: list[Mapping[str, Any]] = []
        if cursor <= bound:
            new_events = [
                event
                for event in read_access_events(
                    state_dir, session["session_id"], start=cursor, end=bound
                )
                if event["event_id"] not in existing_events
            ]
        # Existing rows are preserved as rendered text. New rows are inserted before the end marker.
        region_match = re.search(
            re.escape(CONTEXT_START) + r".*?" + re.escape(CONTEXT_END), body, re.S
        )
        region = region_match.group(0) if region_match else _access_region([])
        if new_events:
            additions = "\n".join(_access_region(new_events).splitlines()[5:-1])
            region = region.replace(CONTEXT_END, additions + "\n" + CONTEXT_END)
        body = _replace_region(body, CONTEXT_START, CONTEXT_END, region)

        if descriptor["event_kind"] == "session_start" and start_marker not in body:
            title_end = body.find("\n")
            body = (
                body[:title_end] + "\n" + start_marker + body[title_end:]
                if title_end >= 0
                else body + "\n" + start_marker + "\n"
            )
        elif not duplicate:
            number = len(_checkpoint_blocks(body)) + 1
            body = body.rstrip() + "\n\n" + _checkpoint(descriptor, number)
        body = _replace_region(body, INDEX_START, INDEX_END, _index_region(body))
        count = len(_checkpoint_blocks(body))
        metadata["checkpoint_count"] = count
        candidate = render_frontmatter(FrontmatterDocument(metadata, body))

        status_path = vault / "system/status.md"
        status_text = (
            status_path.read_text(encoding="utf-8") if status_path.exists() else "# Status\n"
        )
        updated_status = (
            status_text
            if duplicate
            else _status_update(status_text, descriptor, str(metadata["status"]))
        )
        outputs: dict[str, bytes | None] = {}
        if not existed or path.read_text(encoding="utf-8") != candidate:
            outputs[relative] = candidate.encode()
        if status_text != updated_status:
            outputs["system/status.md"] = updated_status.encode()
        commit_hash = None
        if outputs:
            result = execute_transaction(
                vault,
                transaction_state,
                outputs,
                branch=str(config["git"]["branch"]),
                actor="process:memory-worker",
                model=model,
                session_id=session["session_id"],
                summary=f"Materialize lifecycle event {descriptor['event_id']}",
                subject=f"memory({session['agent']}): checkpoint session {session['session_id']}",
                concept_ids=(),
                configured_types=frozenset(config.get("types", DEFAULT_TYPES)),
                max_words=int(config["limits"]["concept_words"]),
                fault_hook=fault_hook,
            )
            commit_hash = result.commit_hash
        if cursor <= bound:
            advance_cursor(state_dir, session["session_id"], bound)
        return MaterializationResult(bool(outputs), relative, count, commit_hash)


def recover_incomplete(
    vault: Path, config: Mapping[str, Any], *, agent: str | None = None
) -> tuple[str, ...]:
    """Mark active sessions incomplete in one managed commit without a checkpoint."""

    state = Path(config["transactions"]["state_dir"])
    with writer_lock(
        state / "writer.lock",
        timeout=float(config["locking"]["timeout_seconds"]),
        command="session recover",
        actor="process:memory-cli",
    ):
        outputs: dict[str, bytes] = {}
        recovered: list[tuple[str, str]] = []
        for path in sorted((vault / "sessions").glob("*/*/*.md")):
            document = parse_frontmatter(path.read_text(encoding="utf-8"))
            if document.metadata.get("status") != "active" or (
                agent and document.metadata.get("agent") != agent
            ):
                continue
            document.metadata["status"] = "incomplete"
            session_agent = str(document.metadata["agent"])
            session_id = str(document.metadata["session_id"])
            recovered.append((session_agent, session_id))
            outputs[path.relative_to(vault).as_posix()] = render_frontmatter(document).encode()
        if not outputs:
            return ()
        status_path = vault / "system/status.md"
        status_text = (
            status_path.read_text(encoding="utf-8") if status_path.exists() else "# Status\n"
        )
        updated_status = status_text
        for session_agent, session_id in recovered:
            marker = f"<!-- session-status:{session_agent}:{session_id} -->"
            block = f"{marker}\n- **{session_agent}/{session_id}:** incomplete; recovered\n"
            pattern = re.compile(re.escape(marker) + r"\n.*?(?=<!-- session-status:|\Z)", re.S)
            updated_status = (
                pattern.sub(block, updated_status, count=1)
                if pattern.search(updated_status)
                else updated_status.rstrip() + "\n\n" + block
            )
        if updated_status != status_text:
            outputs["system/status.md"] = updated_status.encode()
        execute_transaction(
            vault,
            state,
            outputs,
            branch=str(config["git"]["branch"]),
            actor="process:memory-cli",
            model=None,
            session_id=None,
            summary="Mark interrupted sessions incomplete",
            subject="memory(process): recover incomplete sessions",
            concept_ids=(),
        )
    return tuple(sorted(outputs))
