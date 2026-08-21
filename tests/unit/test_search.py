from datetime import date
from pathlib import Path

import pytest

from agent_memory.search import (
    SearchFilters,
    is_stale,
    resolve_concept,
    search_concepts,
    trust_tier,
)
from agent_memory.vault import VaultError, discover_vault, scan_concepts
from tests.fixtures.builders import build_vault, concept_text


def _concept(
    title: str,
    *,
    concept_type: str = "Project",
    scope: str = "personal",
    body: str = "Fixture body.",
    extra: str = "",
) -> str:
    return concept_text(
        title=title,
        concept_type=concept_type,
        scope=scope,
        body=f"# {title}\n\n{body}\n",
    ).replace("---\n#", f"{extra}---\n#", 1)


def _records(tmp_path: Path, concepts: dict[str, str]):
    return scan_concepts(discover_vault(build_vault(tmp_path, concepts)))


def test_fixed_ranking_and_matched_fields_are_explainable(tmp_path: Path) -> None:
    concepts = _records(
        tmp_path,
        {
            "target": _concept("Different", body="nothing"),
            "exact-title": _concept("Target", body="nothing"),
            "partial-title": _concept("Target workflow", body="nothing"),
            "description": _concept("Description", extra="tags: [target]\n", body="nothing"),
            "body-only": _concept("Body Only", body="contains target here"),
        },
    )

    results = search_concepts(concepts, "target")

    assert [item.concept.slug for item in results] == [
        "target",
        "exact-title",
        "partial-title",
        "description",
        "body-only",
    ]
    assert results[0].matched_fields == ("slug",)
    assert "title" in results[1].matched_fields
    assert results[3].matched_fields == ("tags",)
    assert results[4].matched_fields == ("body",)


def test_exact_filters_trust_staleness_and_stable_default(tmp_path: Path) -> None:
    concepts = _records(
        tmp_path,
        {
            "human": _concept(
                "Human Project",
                scope="work",
                extra=(
                    "tags: [shared]\n"
                    "verified:\n  by: human:donald\n  at: 2026-01-02T03:04:05Z\n"
                    "stale_after: 2026-01-10\n"
                ),
                body="searchable",
            ),
            "machine": _concept(
                "Machine Note",
                concept_type="Note",
                extra=(
                    "content_owner: agent\n"
                    "verified:\n  by: process:validator\n  at: 2026-01-02T03:04:05Z\n"
                ),
                body="searchable",
            ),
            "plain": _concept("Plain Project", body="searchable"),
        },
    )

    human = search_concepts(
        concepts,
        "searchable",
        filters=SearchFilters(
            concept_type="Project",
            scope="work",
            tag="shared",
            creator="process:fixture",
            status="stable",
            verification="human-reviewed",
            stale=True,
        ),
        today=date(2026, 1, 10),
    )
    machine = search_concepts(
        concepts,
        "searchable",
        filters=SearchFilters(verification="machine-confirmed"),
    )
    plain = search_concepts(
        concepts,
        "searchable",
        filters=SearchFilters(verification="unverified", status="stable"),
    )

    assert [item.concept.slug for item in human] == ["human"]
    assert human[0].stale and human[0].status == "stable"
    assert [item.concept.slug for item in machine] == ["machine"]
    assert [item.concept.slug for item in plain] == ["plain"]
    assert trust_tier(plain[0].concept.document.metadata) == "unverified"
    assert is_stale(human[0].concept.document.metadata, today=date(2026, 1, 9)) is False


def test_result_order_is_stable_and_does_not_depend_on_concept_index(tmp_path: Path) -> None:
    vault_root = build_vault(
        tmp_path,
        {
            "zulu": _concept("same", body="needle"),
            "alpha": _concept("Same", body="needle"),
        },
    )
    vault = discover_vault(vault_root)
    vault.concept_index.unlink()

    first = search_concepts(scan_concepts(vault), "needle")
    second = search_concepts(scan_concepts(vault), "needle")

    assert [item.concept.slug for item in first] == ["alpha", "zulu"]
    assert first == second


def test_show_resolution_accepts_full_or_slug_and_rejects_traversal(tmp_path: Path) -> None:
    concepts = _records(tmp_path, {"safe-concept": _concept("Safe")})

    assert resolve_concept(concepts, "safe-concept").concept_id == "concepts/safe-concept"
    assert resolve_concept(concepts, "concepts/safe-concept").slug == "safe-concept"
    with pytest.raises(ValueError):
        resolve_concept(concepts, "../safe-concept")
    with pytest.raises(VaultError, match="not found"):
        resolve_concept(concepts, "missing")
