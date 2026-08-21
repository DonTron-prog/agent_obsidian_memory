"""Deterministic metadata and full-text retrieval over Markdown concepts."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Literal

from agent_memory.models import validate_concept_id, validate_slug
from agent_memory.vault import ConceptDocument, VaultError, markdown_targets

TrustTier = Literal["unverified", "machine-confirmed", "human-reviewed"]


@dataclass(frozen=True)
class SearchFilters:
    concept_type: str | None = None
    scope: str | None = None
    tag: str | None = None
    creator: str | None = None
    status: str | None = None
    verification: TrustTier | None = None
    stale: bool = False


@dataclass(frozen=True)
class SearchResult:
    concept: ConceptDocument
    rank: int
    matched_fields: tuple[str, ...]
    trust_tier: TrustTier
    stale: bool
    status: str


def normalize_text(value: object) -> str:
    return " ".join(str(value).casefold().split())


def trust_tier(metadata: Mapping[str, Any]) -> TrustTier:
    value = metadata.get("verified")
    events = [value] if isinstance(value, Mapping) else value
    if not isinstance(events, Sequence) or isinstance(events, str | bytes):
        return "unverified"
    actors = [event.get("by") for event in events if isinstance(event, Mapping)]
    if any(isinstance(actor, str) and actor.startswith("human:") for actor in actors):
        return "human-reviewed"
    if any(isinstance(actor, str) and actor.strip() for actor in actors):
        return "machine-confirmed"
    return "unverified"


def is_stale(metadata: Mapping[str, Any], *, today: date | None = None) -> bool:
    value = metadata.get("stale_after")
    if isinstance(value, datetime):
        stale_after = value.date()
    elif isinstance(value, date):
        stale_after = value
    elif isinstance(value, str):
        try:
            stale_after = date.fromisoformat(value)
        except ValueError:
            return False
    else:
        return False
    return stale_after <= (today or date.today())


def _strings(value: object) -> tuple[str, ...]:
    if isinstance(value, str):
        return (value,)
    if isinstance(value, Sequence) and not isinstance(value, str | bytes):
        return tuple(str(item) for item in value)
    return ()


def _status(metadata: Mapping[str, Any]) -> str:
    value = metadata.get("status", "stable")
    return value if isinstance(value, str) else ""


def _actor(metadata: Mapping[str, Any], field: str) -> str:
    value = metadata.get(field)
    return str(value.get("by", "")) if isinstance(value, Mapping) else ""


def _matches_filters(
    concept: ConceptDocument,
    filters: SearchFilters,
    *,
    today: date | None,
) -> tuple[bool, TrustTier, bool, str]:
    metadata = concept.document.metadata
    tier = trust_tier(metadata)
    stale = is_stale(metadata, today=today)
    status = _status(metadata)
    checks = (
        filters.concept_type is None or metadata.get("type") == filters.concept_type,
        filters.scope is None or metadata.get("scope") == filters.scope,
        filters.tag is None or filters.tag in _strings(metadata.get("tags")),
        filters.creator is None or _actor(metadata, "created") == filters.creator,
        filters.status is None or status == filters.status,
        filters.verification is None or tier == filters.verification,
        not filters.stale or stale,
    )
    return all(checks), tier, stale, status


def _query_matches(concept: ConceptDocument, query: str) -> tuple[int, tuple[str, ...]] | None:
    metadata = concept.document.metadata
    normalized = normalize_text(query)
    if not normalized:
        raise ValueError("search query must not be empty")

    fields: list[str] = []
    rank = 99

    if normalized == normalize_text(concept.concept_id):
        fields.append("id")
        rank = 1
    if normalized == normalize_text(concept.slug):
        fields.append("slug")
        rank = 1

    title = str(metadata.get("title", ""))
    normalized_title = normalize_text(title)
    if normalized == normalized_title:
        fields.append("title")
        rank = min(rank, 2)
    elif normalized in normalized_title:
        fields.append("title")
        rank = min(rank, 3)

    metadata_fields: tuple[tuple[str, tuple[str, ...]], ...] = (
        ("type", _strings(metadata.get("type"))),
        ("scope", _strings(metadata.get("scope"))),
        ("tags", _strings(metadata.get("tags"))),
        ("description", _strings(metadata.get("description"))),
        ("creator", (_actor(metadata, "created"),)),
        ("generated", (_actor(metadata, "generated"),)),
        ("status", (_status(metadata),)),
        ("verification", (trust_tier(metadata),)),
        ("links", markdown_targets(concept.document.body)),
    )
    for field, values in metadata_fields:
        if any(normalized in normalize_text(value) for value in values):
            fields.append(field)
            rank = min(rank, 4)

    if normalized in normalize_text(concept.document.body):
        fields.append("body")
        rank = min(rank, 5)

    return None if rank == 99 else (rank, tuple(dict.fromkeys(fields)))


def search_concepts(
    concepts: Sequence[ConceptDocument],
    query: str,
    *,
    filters: SearchFilters | None = None,
    limit: int = 10,
    today: date | None = None,
) -> tuple[SearchResult, ...]:
    """Search concepts with the fixed explainable ordering from the specification."""

    if limit < 1:
        raise ValueError("limit must be at least 1")
    selected: list[SearchResult] = []
    for concept in concepts:
        matches, tier, stale, status = _matches_filters(
            concept, filters or SearchFilters(), today=today
        )
        if not matches:
            continue
        query_match = _query_matches(concept, query)
        if query_match is None:
            continue
        rank, fields = query_match
        selected.append(SearchResult(concept, rank, fields, tier, stale, status))
    selected.sort(
        key=lambda result: (
            result.rank,
            str(result.concept.document.metadata.get("title", "")).casefold(),
            result.concept.concept_id,
        )
    )
    return tuple(selected[:limit])


def resolve_concept(concepts: Sequence[ConceptDocument], concept_id: str) -> ConceptDocument:
    """Resolve a canonical ID or exact slug shorthand without path traversal."""

    if concept_id.startswith("concepts/"):
        canonical = validate_concept_id(concept_id)
        matches = [concept for concept in concepts if concept.concept_id == canonical]
    else:
        slug = validate_slug(concept_id)
        matches = [concept for concept in concepts if concept.slug == slug]
    if not matches:
        raise VaultError(f"concept not found: {concept_id}")
    if len(matches) > 1:
        raise VaultError(f"ambiguous concept ID: {concept_id}")
    return matches[0]
