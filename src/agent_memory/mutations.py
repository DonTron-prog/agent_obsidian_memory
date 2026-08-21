"""Managed concept operations rendered through the shared transaction engine."""

from __future__ import annotations

import copy
import hashlib
import os
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

from ruamel.yaml.comments import CommentedMap

from agent_memory.git import dirty_paths, head_concept_paths, show_file
from agent_memory.locking import writer_lock
from agent_memory.markdown import FrontmatterDocument, parse_frontmatter, render_frontmatter
from agent_memory.models import Actor, ActorKind, validate_slug
from agent_memory.search import normalize_text, search_concepts
from agent_memory.secrets import reject_secret_path
from agent_memory.transactions import TransactionError, TransactionResult, execute_transaction
from agent_memory.validation import DEFAULT_TYPES
from agent_memory.vault import MARKDOWN_LINK, WIKI_LINK, ConceptDocument, Vault, scan_concepts


@dataclass(frozen=True)
class MutationContext:
    actor: str
    model: str | None = None
    session_id: str | None = None


@dataclass(frozen=True)
class MutationResult:
    transaction: TransactionResult
    duplicate_candidates: tuple[str, ...] = ()


def _timestamp() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _validate_context(context: MutationContext, summary: str) -> Actor:
    try:
        actor = Actor(context.actor, model=context.model)
    except ValueError as exc:
        raise TransactionError(str(exc)) from exc
    if not isinstance(summary, str) or not summary.strip():
        raise TransactionError("mutation summary must be non-empty")
    if context.session_id is not None and (
        not isinstance(context.session_id, str) or not context.session_id.strip()
    ):
        raise TransactionError("session ID must be non-empty when supplied")
    if actor.kind is ActorKind.AGENT:
        if "/" not in actor.by or any(not part for part in actor.by.split("/")):
            raise TransactionError("agent actor must include its runtime version")
        if (
            not isinstance(context.model, str)
            or context.model.strip() != context.model
            or any(character.isspace() for character in context.model)
            or "/" not in context.model
            or any(not part for part in context.model.split("/"))
        ):
            raise TransactionError("agent mutations require an exact provider/model identifier")
    elif context.model is not None:
        raise TransactionError("human and process actors must omit model identity")
    return actor


def _attribution(actor: Actor, context: MutationContext, timestamp: str) -> CommentedMap:
    value = CommentedMap({"by": context.actor, "at": timestamp})
    if actor.kind is ActorKind.AGENT:
        value["model"] = context.model
    return value


def _sources(value: object) -> list[dict[str, Any]]:
    if value is None:
        return []
    if not isinstance(value, Sequence) or isinstance(value, str | bytes):
        raise TransactionError("sources must be a list")
    result: list[dict[str, Any]] = []
    for source in value:
        if isinstance(source, str) and source.strip():
            result.append({"resource": source})
        elif (
            isinstance(source, Mapping)
            and isinstance(source.get("resource"), str)
            and source["resource"].strip()
        ):
            result.append(dict(source))
        else:
            raise TransactionError("each source must contain a resource")
    return result


def _document(text: str) -> FrontmatterDocument:
    return parse_frontmatter(text)


_MEANINGFUL_FIELDS = ("title", "description", "type", "scope", "resource", "sources")


def _meaningful_change(before: FrontmatterDocument, after: FrontmatterDocument) -> bool:
    return before.body != after.body or any(
        before.metadata.get(field) != after.metadata.get(field) for field in _MEANINGFUL_FIELDS
    )


def _working(vault: Vault) -> dict[str, str]:
    return {concept.slug: concept.text for concept in scan_concepts(vault)}


def _head_texts(vault: Vault) -> dict[str, str]:
    result: dict[str, str] = {}
    for relative in head_concept_paths(vault.root):
        path = Path(relative)
        if (
            path.parent == Path("memory/concepts")
            and path.suffix == ".md"
            and path.name != "index.md"
        ):
            content = show_file(vault.root, relative)
            if content is not None:
                result[path.stem] = content.decode("utf-8")
    return result


def _resolve_slug(texts: Mapping[str, str], value: str) -> str:
    candidate = value.removeprefix("concepts/")
    validate_slug(candidate)
    if candidate not in texts:
        raise TransactionError(f"concept not found: {value}")
    return candidate


def _duplicates(texts: Mapping[str, str], slug: str, title: str) -> None:
    if slug in texts:
        raise TransactionError(f"duplicate slug: concepts/{slug}")
    normalized = normalize_text(title)
    for existing_slug, text in texts.items():
        if normalize_text(_document(text).metadata.get("title", "")) == normalized:
            raise TransactionError(f"duplicate normalized title: concepts/{existing_slug}")


def _markdown_inline(value: object) -> str:
    text = " ".join(str(value).split())
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace("\\", "&#92;")
        .replace("`", "&#96;")
        .replace("[", "&#91;")
        .replace("]", "&#93;")
    )


def _markdown_code(value: object) -> str:
    text = " ".join(str(value).split())
    longest = max((len(match.group()) for match in re.finditer(r"`+", text)), default=0)
    fence = "`" * (longest + 1)
    padding = " " if text.startswith("`") or text.endswith("`") else ""
    return f"{fence}{padding}{text}{padding}{fence}"


def _render_index(texts: Mapping[str, str]) -> str:
    rows: list[tuple[str, str, str, str, str]] = []
    for slug, text in texts.items():
        metadata = _document(text).metadata
        rows.append(
            (
                str(metadata.get("scope", "")),
                str(metadata.get("type", "")),
                str(metadata.get("title", "")),
                slug,
                str(metadata.get("description", "")),
            )
        )
    rows.sort(key=lambda row: (row[0].casefold(), row[1].casefold(), row[2].casefold(), row[3]))
    lines = ["# Concepts", ""]
    last_scope = last_type = None
    for scope, concept_type, title, slug, description in rows:
        if scope != last_scope:
            lines.extend([f"## {scope.title()}", ""])
            last_scope, last_type = scope, None
        if concept_type != last_type:
            lines.extend([f"### {concept_type}", ""])
            last_type = concept_type
        lines.append(f"- [{_markdown_inline(title)}]({slug}.md) — {_markdown_inline(description)}")
    return "\n".join(lines).rstrip() + "\n"


def _log(existing: str, entries: Sequence[str], day: str) -> str:
    heading = f"## {day}"
    block = "\n".join(f"* {entry}" for entry in entries) + "\n"
    if heading in existing.splitlines():
        position = existing.index(heading) + len(heading)
        return existing[:position] + "\n" + block + existing[position:].lstrip("\n")
    first_line, separator, rest = existing.partition("\n")
    if not separator:
        first_line = "# Log"
        rest = ""
    return (
        first_line
        + "\n\n"
        + heading
        + "\n"
        + block
        + ("\n" + rest.lstrip() if rest.strip() else "")
    )


def _actor_label(actor: str) -> str:
    if actor.startswith("human:"):
        return "human"
    if actor.startswith("hermes"):
        return "hermes"
    if actor.startswith("pi"):
        return "pi"
    return "process"


def _entry(
    label: str,
    slug: str,
    title: str,
    summary: str,
    context: MutationContext,
    *,
    link: bool = True,
) -> str:
    safe_title = _markdown_inline(title)
    safe_summary = _markdown_inline(summary)
    target = f"[{safe_title}](concepts/{slug}.md)" if link else f"`concepts/{slug}`"
    provenance = f"Actor {_markdown_code(context.actor)}"
    if context.model:
        provenance += f", model {_markdown_code(context.model)}"
    if context.session_id:
        provenance += f", session {_markdown_code(context.session_id)}"
    return f"**{label}**: {target} — {safe_summary}. {provenance}."


def _markdown_rename(text: str, source: Path, old: Path, new: Path) -> str:
    def markdown(match: re.Match[str]) -> str:
        label, raw, title = match.groups()
        angled = raw.startswith("<") and raw.endswith(">")
        path_part, marker, fragment = raw.strip("<>").partition("#")
        split = urlsplit(path_part)
        if split.scheme or split.netloc:
            return match.group(0)
        candidate = (source.parent / unquote(split.path)).resolve(strict=False)
        if candidate != old.resolve(strict=False):
            return match.group(0)
        relative_text = Path(os.path.relpath(new, source.parent)).as_posix()
        replacement = relative_text + (marker + fragment if marker else "")
        if angled:
            replacement = f"<{replacement}>"
        return f"[{label}]({replacement}{f' {title}' if title else ''})"

    rendered = MARKDOWN_LINK.sub(markdown, text)

    def wiki(match: re.Match[str]) -> str:
        value = match.group(1)
        replacement = value
        for old_value, new_value in (
            (old.stem, new.stem),
            (f"concepts/{old.stem}", f"concepts/{new.stem}"),
            (f"memory/concepts/{old.stem}", f"memory/concepts/{new.stem}"),
        ):
            if value == old_value:
                replacement = new_value
                break
        return match.group(0).replace(value, replacement, 1)

    return WIKI_LINK.sub(wiki, rendered)


def _rewrite_links(
    vault: Vault,
    outputs: dict[str, bytes | None],
    working: dict[str, str],
    planned: dict[str, str],
    old_path: Path,
    new_path: Path,
) -> None:
    relatives = {
        path.relative_to(vault.root).as_posix()
        for path in vault.root.rglob("*.md")
        if path != vault.concept_index
    }
    relatives.update(
        relative
        for relative, content in outputs.items()
        if relative.endswith(".md") and content is not None
    )
    old_relative = old_path.relative_to(vault.root).as_posix()
    for relative in sorted(relatives):
        if relative == old_relative or relative == "memory/concepts/index.md":
            continue
        path = vault.root / relative
        if path.exists() and (path.is_symlink() or not path.is_file()):
            raise TransactionError(f"unsafe Markdown path during rename: {relative}")
        try:
            path.resolve(strict=False).relative_to(vault.root.resolve())
        except ValueError as exc:
            raise TransactionError(f"Markdown path escapes vault: {relative}") from exc
        current = outputs.get(relative)
        if current is None:
            if relative in outputs or not path.exists():
                continue
            current = path.read_bytes()
        try:
            original = current.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise TransactionError(f"Markdown must be UTF-8: {relative}") from exc
        updated = _markdown_rename(original, path, old_path, new_path)
        if updated == original:
            continue
        outputs[relative] = updated.encode()
        relative_path = Path(relative)
        if relative_path.parent == Path("memory/concepts"):
            slug = relative_path.stem
            if slug in working:
                working[slug] = updated
            if slug in planned:
                planned[slug] = updated


def apply_operations(
    vault: Vault,
    config: Mapping[str, Any],
    operations: Sequence[Mapping[str, Any]],
    *,
    context: MutationContext,
    summary: str,
    dry_run: bool = False,
    fault_hook: Any = None,
    interactive_verification: bool = False,
) -> MutationResult:
    if not operations:
        raise TransactionError("batch must contain at least one operation")
    if interactive_verification and (
        len(operations) != 1
        or operations[0].get("action") != "verify"
        or context != MutationContext(str(config["identity"]["human"]))
    ):
        raise TransactionError(
            "interactive verification requires one verify operation in the configured human context"
        )
    actor = _validate_context(context, summary)
    state_dir = Path(config["transactions"]["state_dir"])
    try:
        state_dir.resolve(strict=False).relative_to(vault.root.resolve())
    except ValueError:
        pass
    else:
        raise TransactionError("transaction state directory must be outside the vault")
    lock_path = state_dir / "writer.lock"
    with writer_lock(
        lock_path,
        timeout=float(config["locking"]["timeout_seconds"]),
        command="apply",
        actor=context.actor,
    ):
        working = _working(vault)
        planned = _head_texts(vault)
        outputs: dict[str, bytes | None] = {}
        entries: list[str] = []
        ids: list[str] = []
        candidates: list[str] = []
        allow_long = False
        timestamp = _timestamp()
        attribution = _attribution(actor, context, timestamp)

        for operation in operations:
            action = operation.get("action")
            if action == "create":
                title = str(operation.get("title", "")).strip()
                slug = validate_slug(
                    str(
                        operation.get("slug")
                        or re.sub(r"[^a-z0-9]+", "-", title.casefold()).strip("-")
                    )
                )
                _duplicates(working, slug, title)
                concept_type = operation.get("type")
                scope = operation.get("scope")
                body = _body(operation)
                metadata = CommentedMap(
                    {
                        "type": concept_type,
                        "title": title,
                        "description": operation.get("description"),
                        "scope": scope,
                        "created": copy.deepcopy(attribution),
                        "generated": copy.deepcopy(attribution),
                    }
                )
                if operation.get("tags") is not None:
                    metadata["tags"] = list(operation["tags"])
                if operation.get("status") is not None:
                    metadata["status"] = operation["status"]
                if operation.get("resource") is not None:
                    metadata["resource"] = operation["resource"]
                sources = _sources(operation.get("sources"))
                if sources:
                    metadata["sources"] = sources
                if concept_type == "Note":
                    metadata["content_owner"] = operation.get("content_owner")
                text = render_frontmatter(FrontmatterDocument(metadata, body))
                working[slug] = text
                planned[slug] = text
                outputs[f"memory/concepts/{slug}.md"] = text.encode()
                entries.append(_entry("Creation", slug, title, summary, context))
                ids.append(f"concepts/{slug}")
                allow_long = allow_long or bool(operation.get("allow_long"))
                pseudo = [
                    ConceptDocument(
                        f"concepts/{key}",
                        key,
                        vault.bundle / "concepts" / f"{key}.md",
                        _document(value),
                        value,
                    )
                    for key, value in working.items()
                    if key != slug
                ]
                candidates.extend(
                    result.concept.concept_id for result in search_concepts(pseudo, title)
                )
            elif action == "update":
                update_fields = (
                    "body",
                    "body_file",
                    "type",
                    "title",
                    "description",
                    "scope",
                    "status",
                    "tags",
                    "sources",
                    "resource",
                )
                if not any(
                    key in operation and operation[key] is not None for key in update_fields
                ):
                    raise TransactionError("update requires body or metadata input")
                slug = _resolve_slug(working, str(operation.get("id", "")))
                document = _document(working[slug])
                before = FrontmatterDocument(copy.deepcopy(document.metadata), document.body)
                metadata = document.metadata
                old_title = str(metadata.get("title", slug))
                for key in (
                    "type",
                    "title",
                    "description",
                    "scope",
                    "status",
                    "tags",
                    "resource",
                ):
                    if key in operation and operation[key] is not None:
                        metadata[key] = operation[key]
                normalized_title = normalize_text(metadata.get("title", ""))
                for other_slug, other_text in working.items():
                    if (
                        other_slug != slug
                        and normalize_text(_document(other_text).metadata.get("title", ""))
                        == normalized_title
                    ):
                        raise TransactionError(f"duplicate normalized title: concepts/{other_slug}")
                if "sources" in operation:
                    metadata["sources"] = _sources(operation["sources"])
                body = _body(operation, default=document.body)
                candidate = FrontmatterDocument(metadata, body)
                if _meaningful_change(before, candidate):
                    metadata["generated"] = copy.deepcopy(attribution)
                    metadata.pop("verified", None)
                text = render_frontmatter(candidate)
                working[slug] = text
                planned[slug] = text
                outputs[f"memory/concepts/{slug}.md"] = text.encode()
                entries.append(
                    _entry("Update", slug, str(metadata.get("title", old_title)), summary, context)
                )
                ids.append(f"concepts/{slug}")
                allow_long = allow_long or bool(operation.get("allow_long"))
            elif action == "verify":
                authorization_source = operation.get("authorization_source")
                if not interactive_verification and (
                    not isinstance(authorization_source, str) or not authorization_source.strip()
                ):
                    raise TransactionError(
                        "noninteractive human verification requires an authorization source"
                    )
                note = operation.get("note")
                if note is not None and (not isinstance(note, str) or not note.strip()):
                    raise TransactionError("verification note must be non-empty when supplied")
                slug = _resolve_slug(working, str(operation.get("id", "")))
                document = _document(working[slug])
                metadata = document.metadata
                current = metadata.get("verified")
                if isinstance(current, Mapping):
                    events = [copy.deepcopy(current)]
                elif isinstance(current, Sequence) and not isinstance(current, str | bytes):
                    events = list(copy.deepcopy(current))
                elif current is None:
                    events = []
                else:
                    raise TransactionError("verified must be a mapping or list of mappings")
                event = CommentedMap({"by": config["identity"]["human"], "at": timestamp})
                if isinstance(authorization_source, str) and authorization_source.strip():
                    event["authorization_source"] = authorization_source
                if isinstance(note, str):
                    event["note"] = note
                events.append(event)
                metadata["verified"] = events
                text = render_frontmatter(document)
                working[slug] = text
                planned[slug] = text
                outputs[f"memory/concepts/{slug}.md"] = text.encode()
                title = str(metadata.get("title", slug))
                entries.append(_entry("Verification", slug, title, summary, context))
                ids.append(f"concepts/{slug}")
            elif action == "delete":
                slug = _resolve_slug(working, str(operation.get("id", "")))
                document = _document(working[slug])
                if document.metadata.get("content_owner") == "user":
                    authorization_source = operation.get("authorization_source")
                    if operation.get("authorized_by") != config["identity"]["human"] or (
                        not isinstance(authorization_source, str)
                        or not authorization_source.strip()
                    ):
                        raise TransactionError(
                            "user-owned Note deletion requires human:donald authorization "
                            "and a source"
                        )
                title = str(document.metadata.get("title", slug))
                del working[slug]
                planned.pop(slug, None)
                outputs[f"memory/concepts/{slug}.md"] = None
                deletion_summary = summary
                if operation.get("authorization_source"):
                    if (
                        not isinstance(operation["authorization_source"], str)
                        or not operation["authorization_source"].strip()
                    ):
                        raise TransactionError("authorization source must be non-empty")
                    deletion_summary += (
                        f"; authorized by {operation.get('authorized_by')} from "
                        f"{operation.get('authorization_source')}"
                    )
                entries.append(
                    _entry("Deletion", slug, title, deletion_summary, context, link=False)
                )
                ids.append(f"concepts/{slug}")
            elif action == "rename":
                old_slug = _resolve_slug(working, str(operation.get("id", "")))
                new_slug = validate_slug(str(operation.get("new_slug", "")))
                if new_slug == old_slug:
                    raise TransactionError("new slug must differ from the current slug")
                if new_slug in working:
                    raise TransactionError(f"duplicate slug: concepts/{new_slug}")
                text = working.pop(old_slug)
                working[new_slug] = text
                planned.pop(old_slug, None)
                planned[new_slug] = text
                old_path = vault.bundle / "concepts" / f"{old_slug}.md"
                new_path = vault.bundle / "concepts" / f"{new_slug}.md"
                outputs[f"memory/concepts/{old_slug}.md"] = None
                outputs[f"memory/concepts/{new_slug}.md"] = text.encode()
                _rewrite_links(vault, outputs, working, planned, old_path, new_path)
                title = str(_document(text).metadata.get("title", new_slug))
                entries.append(_entry("Rename", new_slug, title, summary, context))
                ids.append(f"concepts/{old_slug}")
                ids.append(f"concepts/{new_slug}")
            else:
                raise TransactionError(f"unsupported operation action: {action!r}")

        outputs["memory/concepts/index.md"] = _render_index(planned).encode()
        log_path = vault.bundle / "log.md"
        existing_log = outputs.get("memory/log.md")
        if not isinstance(existing_log, bytes):
            existing_log = log_path.read_bytes()
        outputs["memory/log.md"] = _log(
            existing_log.decode("utf-8"), entries, timestamp[:10]
        ).encode()
        action_name = str(operations[0]["action"]) if len(operations) == 1 else "apply batch"
        subject_id = (
            ids[-1].removeprefix("concepts/") if len(ids) == 1 else f"{len(operations)} concepts"
        )
        transaction = execute_transaction(
            vault.root,
            state_dir,
            outputs,
            branch=str(config["git"]["branch"]),
            actor=context.actor,
            model=context.model,
            session_id=context.session_id,
            summary=summary,
            subject=f"memory({_actor_label(context.actor)}): {action_name} {subject_id}",
            concept_ids=tuple(dict.fromkeys(ids)),
            configured_types=frozenset(config.get("types", DEFAULT_TYPES)),
            max_words=int(config["limits"]["concept_words"]),
            allow_long=allow_long,
            dry_run=dry_run,
            fault_hook=fault_hook,
        )
        return MutationResult(transaction, tuple(dict.fromkeys(candidates)))


def reconcile_concept(
    vault: Vault,
    config: Mapping[str, Any],
    concept_id: str,
    *,
    summary: str,
    dry_run: bool = False,
    fault_hook: Any = None,
) -> MutationResult:
    """Adopt one tracked direct edit without absorbing other working-tree content."""

    human = str(config["identity"]["human"])
    context = MutationContext(human)
    actor = _validate_context(context, summary)
    state_dir = Path(config["transactions"]["state_dir"])
    try:
        state_dir.resolve(strict=False).relative_to(vault.root.resolve())
    except ValueError:
        pass
    else:
        raise TransactionError("transaction state directory must be outside the vault")

    with writer_lock(
        state_dir / "writer.lock",
        timeout=float(config["locking"]["timeout_seconds"]),
        command="reconcile",
        actor=human,
    ):
        committed = _head_texts(vault)
        slug = _resolve_slug(committed, concept_id)
        relative = f"memory/concepts/{slug}.md"
        path = vault.root / relative
        if path.is_symlink() or not path.is_file():
            raise TransactionError(
                "reconcile requires the tracked concept at its committed path; "
                "direct filesystem renames are not inferred"
            )
        current_bytes = path.read_bytes()
        current_text = current_bytes.decode("utf-8")
        current_hash = hashlib.sha256(current_bytes).hexdigest()
        committed_text = committed[slug]
        if current_text == committed_text:
            raise TransactionError("concept has no direct edit to reconcile")

        before = _document(committed_text)
        candidate = _document(current_text)
        candidate.metadata["created"] = copy.deepcopy(before.metadata["created"])
        meaningful = _meaningful_change(before, candidate)
        timestamp = _timestamp()
        if meaningful:
            candidate.metadata["generated"] = _attribution(actor, context, timestamp)
            candidate.metadata.pop("verified", None)
        else:
            candidate.metadata["generated"] = copy.deepcopy(before.metadata["generated"])
            if "verified" in before.metadata:
                candidate.metadata["verified"] = copy.deepcopy(before.metadata["verified"])
            else:
                candidate.metadata.pop("verified", None)

        normalized_title = normalize_text(candidate.metadata.get("title", ""))
        for other_slug, text in committed.items():
            if (
                other_slug != slug
                and normalize_text(_document(text).metadata.get("title", "")) == normalized_title
            ):
                raise TransactionError(f"duplicate normalized title: concepts/{other_slug}")

        rendered = render_frontmatter(candidate)
        if rendered == committed_text:
            raise TransactionError(
                "direct edit changes only immutable created or managed attribution metadata; "
                "reconciliation rejected"
            )
        planned = dict(committed)
        planned[slug] = rendered
        log_path = vault.bundle / "log.md"
        title = str(candidate.metadata.get("title", slug))
        outputs = {
            relative: rendered.encode(),
            "memory/concepts/index.md": _render_index(planned).encode(),
            "memory/log.md": _log(
                log_path.read_text(encoding="utf-8"),
                [_entry("Reconciliation", slug, title, summary, context)],
                timestamp[:10],
            ).encode(),
        }
        transaction = execute_transaction(
            vault.root,
            state_dir,
            outputs,
            branch=str(config["git"]["branch"]),
            actor=human,
            model=None,
            session_id=None,
            summary=summary,
            subject=f"memory(human): reconcile {slug}",
            concept_ids=(f"concepts/{slug}",),
            configured_types=frozenset(config.get("types", DEFAULT_TYPES)),
            max_words=int(config["limits"]["concept_words"]),
            dry_run=dry_run,
            fault_hook=fault_hook,
            adopted_paths={relative: current_hash},
        )
        return MutationResult(transaction)


def rebuild_index(
    vault: Vault,
    config: Mapping[str, Any],
    *,
    dry_run: bool = False,
    fault_hook: Any = None,
) -> MutationResult:
    """Rebuild the concept index only from a clean concept corpus."""

    state_dir = Path(config["transactions"]["state_dir"])
    try:
        state_dir.resolve(strict=False).relative_to(vault.root.resolve())
    except ValueError:
        pass
    else:
        raise TransactionError("transaction state directory must be outside the vault")

    with writer_lock(
        state_dir / "writer.lock",
        timeout=float(config["locking"]["timeout_seconds"]),
        command="rebuild-index",
        actor="process:memory-cli",
    ):
        tracked = {
            path
            for path in head_concept_paths(vault.root)
            if Path(path).parent == Path("memory/concepts") and Path(path).name != "index.md"
        }
        present = {
            path.relative_to(vault.root).as_posix()
            for path in (vault.bundle / "concepts").iterdir()
            if path.name != "index.md" and path.suffix == ".md"
        }
        corpus_dirty = dirty_paths(vault.root, tuple(sorted(tracked | present)))
        if corpus_dirty:
            raise TransactionError(
                "unreconciled concept edits block full index rebuild: "
                f"{', '.join(corpus_dirty)}. Reconcile each changed tracked concept with "
                "memory reconcile; resolve added, deleted, or renamed files manually."
            )

        rendered = _render_index(_head_texts(vault)).encode()
        raced_present = {
            path.relative_to(vault.root).as_posix()
            for path in (vault.bundle / "concepts").iterdir()
            if path.name != "index.md" and path.suffix == ".md"
        }
        raced_dirty = dirty_paths(vault.root, tuple(sorted(tracked | raced_present)))
        if raced_dirty:
            raise TransactionError(
                "unreconciled concept edits block full index rebuild: "
                f"{', '.join(raced_dirty)}. Reconcile each changed tracked concept with "
                "memory reconcile; resolve added, deleted, or renamed files manually."
            )
        if vault.concept_index.read_bytes() == rendered:
            dirty_index = dirty_paths(vault.root, ("memory/concepts/index.md",))
            if dirty_index:
                raise TransactionError(
                    "transaction targets have uncommitted changes: memory/concepts/index.md. "
                    "Resolve generated index edits manually."
                )
            return MutationResult(TransactionResult("no-op", (), None, dry_run))
        transaction = execute_transaction(
            vault.root,
            state_dir,
            {"memory/concepts/index.md": rendered},
            branch=str(config["git"]["branch"]),
            actor="process:memory-cli",
            model=None,
            session_id=None,
            summary="Rebuild concept index",
            subject="memory(process): rebuild concept index",
            concept_ids=(),
            configured_types=frozenset(config.get("types", DEFAULT_TYPES)),
            max_words=int(config["limits"]["concept_words"]),
            dry_run=dry_run,
            fault_hook=fault_hook,
        )
        return MutationResult(transaction)


def _body(operation: Mapping[str, Any], default: str | None = None) -> str:
    if operation.get("body") is not None:
        value = operation["body"]
        if not isinstance(value, str):
            raise TransactionError("body must be text")
        return value
    if operation.get("body_file") is not None:
        path = Path(str(operation["body_file"])).expanduser()
        reject_secret_path(path.as_posix())
        if path.is_symlink() or not path.is_file():
            raise TransactionError(f"body file must be a regular non-symlink file: {path}")
        return path.read_text(encoding="utf-8")
    if default is not None:
        return default
    raise TransactionError("body or body_file is required")
