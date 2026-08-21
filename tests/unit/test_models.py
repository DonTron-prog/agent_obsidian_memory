import pytest

from agent_memory.models import (
    Actor,
    ActorKind,
    Session,
    validate_concept_id,
    validate_slug,
)


def test_distinguishes_actor_kinds() -> None:
    assert Actor("human:donald").kind is ActorKind.HUMAN
    assert Actor("pi/0.84.2").kind is ActorKind.AGENT
    assert Actor("process:memory-cli").kind is ActorKind.PROCESS


@pytest.mark.parametrize("identity", ["human:", "human: ", "process:\t", "pi agent/0.84.2"])
def test_rejects_empty_or_whitespace_actor_identities(identity: str) -> None:
    with pytest.raises(ValueError):
        Actor(identity)


@pytest.mark.parametrize(
    "slug",
    ["Upper", "starts-", "-ends", "two--hyphens", "has_underscore", "path/name", ""],
)
def test_rejects_invalid_slugs(slug: str) -> None:
    with pytest.raises(ValueError):
        validate_slug(slug)


def test_accepts_valid_slug_and_concept_id() -> None:
    assert validate_slug("agent-memory-2") == "agent-memory-2"
    assert validate_concept_id("concepts/agent-memory-2") == "concepts/agent-memory-2"


@pytest.mark.parametrize(
    "concept_id",
    [
        "../concepts/example",
        "/concepts/example",
        "concepts/../example",
        "concepts/deeper/example",
        "concepts/example.md",
        "concepts//example",
        "./concepts/example",
        "concepts\\example",
    ],
)
def test_rejects_traversal_or_noncanonical_concept_ids(concept_id: str) -> None:
    with pytest.raises(ValueError):
        validate_concept_id(concept_id)


def test_session_model_accepts_optional_native_reference() -> None:
    session = Session.from_mapping(
        {
            "agent": "pi",
            "agent_version": "0.84.2",
            "session_id": "session-1",
            "started_at": "2026-01-02T03:04:05Z",
            "updated_at": "2026-01-02T04:04:05Z",
            "status": "active",
            "checkpoint_count": 0,
            "host_models": ["openai/gpt-5"],
            "summary_policy": "native-only",
            "producer_extension": "kept",
        }
    )

    assert session.native_store_ref is None
    assert session.extra == {"producer_extension": "kept"}
