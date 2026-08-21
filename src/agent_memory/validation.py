"""Base OKF v0.2 and local-profile validation."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import PurePosixPath
from typing import Any, Literal

from agent_memory.markdown import FrontmatterDocument, FrontmatterError, parse_frontmatter
from agent_memory.models import Actor, ActorKind

DEFAULT_TYPES = frozenset(
    {"Project", "Person", "Preference", "Procedure", "Note", "Task", "Decision", "Reference"}
)
SCOPES = frozenset({"work", "personal", "global"})
STATUSES = frozenset({"draft", "stable", "deprecated"})
# Unicode words with internal apostrophes or hyphens treated as one word.
WORD_PATTERN = re.compile(r"(?u)\b\w+(?:[-'’]\w+)*\b")
FENCE_DELIMITER = re.compile(r"^\s*(?:`{3,}|~{3,}).*$")


@dataclass(frozen=True)
class ValidationIssue:
    level: Literal["error", "warning"]
    field: str
    message: str


@dataclass
class ValidationResult:
    issues: list[ValidationIssue] = field(default_factory=list)

    @property
    def errors(self) -> list[ValidationIssue]:
        return [issue for issue in self.issues if issue.level == "error"]

    @property
    def warnings(self) -> list[ValidationIssue]:
        return [issue for issue in self.issues if issue.level == "warning"]

    @property
    def ok(self) -> bool:
        return not self.errors

    def error(self, field_name: str, message: str) -> None:
        self.issues.append(ValidationIssue("error", field_name, message))

    def warning(self, field_name: str, message: str) -> None:
        self.issues.append(ValidationIssue("warning", field_name, message))


def count_body_words(body: str) -> int:
    """Count Unicode words, omitting Markdown fence delimiter lines only."""

    text = "\n".join(line for line in body.splitlines() if not FENCE_DELIMITER.fullmatch(line))
    return len(WORD_PATTERN.findall(text))


def is_reserved_okf_path(path: str) -> bool:
    candidate = PurePosixPath(path)
    return candidate.name == "index.md" or candidate.parts == ("log.md",)


def validate_base_okf(document: FrontmatterDocument) -> ValidationResult:
    """Validate only the extensible OKF v0.2 base requirement."""

    result = ValidationResult()
    concept_type = document.metadata.get("type")
    if not isinstance(concept_type, str) or not concept_type.strip():
        result.error("type", "type must be a non-empty string")
    return result


def validate_bundle_document(path: str, document: FrontmatterDocument) -> ValidationResult:
    """Validate a parsed document at a bundle-relative path, honoring reserved files."""

    if is_reserved_okf_path(path):
        return ValidationResult()
    return validate_base_okf(document)


def validate_bundle_text(path: str, text: str) -> ValidationResult:
    """Validate raw bundle Markdown, exempting reserved files before parsing."""

    if is_reserved_okf_path(path):
        return ValidationResult()
    try:
        document = parse_frontmatter(text)
    except FrontmatterError as exc:
        result = ValidationResult()
        result.error("frontmatter", str(exc))
        return result
    return validate_base_okf(document)


def _non_empty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _utc_timestamp(value: Any) -> bool:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return False
    else:
        return False
    return parsed.tzinfo is not None and parsed.utcoffset() == UTC.utcoffset(parsed)


def _valid_model(value: Any) -> bool:
    return (
        isinstance(value, str)
        and value.strip() == value
        and "/" in value
        and all(part for part in value.split("/"))
    )


def _validate_attribution(result: ValidationResult, field_name: str, value: Any) -> None:
    if not isinstance(value, Mapping):
        result.error(field_name, f"{field_name} must be a mapping")
        return
    by = value.get("by")
    if not _non_empty_string(by):
        result.error(f"{field_name}.by", f"{field_name}.by is required")
        return
    if not _utc_timestamp(value.get("at")):
        result.error(f"{field_name}.at", f"{field_name}.at must be an ISO 8601 UTC timestamp")
    try:
        actor = Actor(by=by, model=value.get("model"))
    except ValueError as exc:
        result.error(f"{field_name}.by", str(exc))
        return
    if actor.kind is ActorKind.AGENT and not _valid_model(actor.model):
        result.error(
            f"{field_name}.model",
            f"{field_name}.model must be an exact provider/model identifier for an agent",
        )


def _validate_verified(result: ValidationResult, value: Any) -> None:
    events = [value] if isinstance(value, Mapping) else value
    if not isinstance(events, list):
        result.error("verified", "verified must be a mapping or list of mappings")
        return
    for index, event in enumerate(events):
        if not isinstance(event, Mapping):
            result.error(f"verified[{index}]", "verification event must be a mapping")
            continue
        if not _non_empty_string(event.get("by")):
            result.error(f"verified[{index}].by", "verification actor is required")
        if not _utc_timestamp(event.get("at")):
            result.error(
                f"verified[{index}].at", "verification time must be an ISO 8601 UTC timestamp"
            )


def _validate_sources(result: ValidationResult, value: Any) -> None:
    if not isinstance(value, list):
        result.error("sources", "sources must be a list of mappings")
        return
    for index, source in enumerate(value):
        if not isinstance(source, Mapping):
            result.error(f"sources[{index}]", "source must be a mapping")
        elif not _non_empty_string(source.get("resource")):
            result.error(f"sources[{index}].resource", "source resource is required")


def validate_local_profile(
    document: FrontmatterDocument,
    *,
    configured_types: set[str] | frozenset[str] = DEFAULT_TYPES,
    managed: bool = False,
    max_words: int = 600,
    allow_long: bool = False,
) -> ValidationResult:
    """Validate the strict local profile, retaining base/local separation."""

    result = validate_base_okf(document)
    metadata = document.metadata
    concept_type = metadata.get("type")

    if (
        isinstance(concept_type, str)
        and concept_type.strip()
        and concept_type not in configured_types
    ):
        message = f"type {concept_type!r} is outside the configured vocabulary"
        if managed:
            result.error("type", message)
        else:
            result.warning("type", message)

    for key in ("title", "description"):
        if not _non_empty_string(metadata.get(key)):
            result.error(key, f"{key} must be a non-empty string")
    if not document.body.strip():
        result.error("body", "body must be non-empty")

    scope = metadata.get("scope")
    if not isinstance(scope, str) or scope not in SCOPES:
        result.error("scope", "scope must be exactly one of work, personal, or global")

    _validate_attribution(result, "created", metadata.get("created"))
    _validate_attribution(result, "generated", metadata.get("generated"))

    content_owner = metadata.get("content_owner")
    if concept_type == "Note" and (
        not isinstance(content_owner, str) or content_owner not in {"user", "agent"}
    ):
        result.error("content_owner", "Note content_owner must be user or agent")

    if "verified" in metadata:
        _validate_verified(result, metadata["verified"])
    if "sources" in metadata:
        _validate_sources(result, metadata["sources"])

    status = metadata.get("status")
    if status is not None and (not isinstance(status, str) or status not in STATUSES):
        result.error("status", "status must be draft, stable, or deprecated")

    tags = metadata.get("tags")
    if tags is not None and (
        not isinstance(tags, list)
        or any(not _non_empty_string(tag) or tag != tag.lower() for tag in tags)
    ):
        result.error("tags", "tags must be a list of non-empty lowercase strings")

    stale_after = metadata.get("stale_after")
    if stale_after is not None:
        try:
            if isinstance(stale_after, datetime):
                raise ValueError
            if not isinstance(stale_after, date):
                date.fromisoformat(stale_after)
        except (TypeError, ValueError):
            result.error("stale_after", "stale_after must be an ISO date")

    words = count_body_words(document.body)
    if words > max_words and not (allow_long and concept_type == "Reference"):
        result.error("body", f"body has {words} words; limit is {max_words}")
    return result
