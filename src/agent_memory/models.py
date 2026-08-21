"""Pure domain models for concepts, attribution, provenance, and sessions."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import PurePosixPath
from typing import Any

SLUG_PATTERN = r"^[a-z0-9]+(?:-[a-z0-9]+)*$"


class ActorKind(StrEnum):
    HUMAN = "human"
    AGENT = "agent"
    PROCESS = "process"


@dataclass(frozen=True)
class Actor:
    by: str
    at: str | None = None
    model: str | None = None

    def __post_init__(self) -> None:
        if (
            not isinstance(self.by, str)
            or not self.by
            or any(character.isspace() for character in self.by)
            or self.by in {"human:", "process:"}
        ):
            raise ValueError("actor identity must be non-empty and include its identity suffix")

    @property
    def kind(self) -> ActorKind:
        if self.by.startswith("human:"):
            return ActorKind.HUMAN
        if self.by.startswith("process:"):
            return ActorKind.PROCESS
        return ActorKind.AGENT


@dataclass(frozen=True)
class Source:
    resource: str
    extra: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_mapping(cls, value: dict[str, Any]) -> Source:
        return cls(
            resource=value["resource"],
            extra={key: item for key, item in value.items() if key != "resource"},
        )


@dataclass(frozen=True)
class Verification:
    actor: Actor
    extra: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_mapping(cls, value: dict[str, Any]) -> Verification:
        return cls(
            actor=Actor(by=value["by"], at=value.get("at"), model=value.get("model")),
            extra={key: item for key, item in value.items() if key not in {"by", "at", "model"}},
        )


@dataclass(frozen=True)
class Concept:
    type: str
    title: str
    description: str
    scope: str
    created: Actor
    generated: Actor
    body: str
    sources: tuple[Source, ...] = ()
    verified: tuple[Verification, ...] = ()
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Session:
    agent: str
    agent_version: str
    session_id: str
    started_at: str
    updated_at: str
    status: str
    checkpoint_count: int
    host_models: tuple[str, ...]
    summary_policy: str
    native_store_ref: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_mapping(cls, value: dict[str, Any]) -> Session:
        required = (
            "agent",
            "agent_version",
            "session_id",
            "started_at",
            "updated_at",
            "status",
            "checkpoint_count",
            "host_models",
            "summary_policy",
        )
        missing = [key for key in required if key not in value]
        if missing:
            raise ValueError(f"missing session fields: {', '.join(missing)}")
        for key in required[:6] + ("summary_policy",):
            if not isinstance(value[key], str) or not value[key].strip():
                raise ValueError(f"{key} must be a non-empty string")
        if value["agent"] not in {"pi", "hermes"}:
            raise ValueError("session agent must be pi or hermes")
        if value["status"] not in {"active", "closed", "incomplete"}:
            raise ValueError("session status is invalid")
        if value["summary_policy"] != "native-only":
            raise ValueError("session summary_policy must be native-only")
        import re

        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,199}", value["session_id"]):
            raise ValueError("session_id is unsafe")
        if not isinstance(value["checkpoint_count"], int) or value["checkpoint_count"] < 0:
            raise ValueError("checkpoint_count must be a non-negative integer")
        models = value["host_models"]
        if not isinstance(models, list) or not all(
            isinstance(model, str) and model.strip() for model in models
        ):
            raise ValueError("host_models must be a list of non-empty strings")
        if len(set(models)) != len(models):
            raise ValueError("host_models must not contain duplicates")
        if value.get("native_store_ref") is not None and not isinstance(
            value["native_store_ref"], str
        ):
            raise ValueError("native_store_ref must be text when present")
        known = {*required, "native_store_ref"}
        return cls(
            agent=value["agent"],
            agent_version=value["agent_version"],
            session_id=value["session_id"],
            started_at=value["started_at"],
            updated_at=value["updated_at"],
            status=value["status"],
            checkpoint_count=value["checkpoint_count"],
            host_models=tuple(models),
            summary_policy=value["summary_policy"],
            native_store_ref=value.get("native_store_ref"),
            extra={key: item for key, item in value.items() if key not in known},
        )


def is_valid_slug(slug: str) -> bool:
    import re

    return bool(re.fullmatch(SLUG_PATTERN, slug))


def validate_slug(slug: str) -> str:
    if not isinstance(slug, str) or not is_valid_slug(slug):
        raise ValueError("slug must use lowercase letters, digits, and single hyphens")
    return slug


def validate_concept_id(concept_id: str) -> str:
    if not isinstance(concept_id, str) or not concept_id or "\\" in concept_id:
        raise ValueError("invalid concept ID")
    path = PurePosixPath(concept_id)
    if (
        path.is_absolute()
        or path.parts != ("concepts", path.name)
        or concept_id != f"concepts/{path.name}"
    ):
        raise ValueError("concept ID must be concepts/<slug>")
    validate_slug(path.name)
    return concept_id
