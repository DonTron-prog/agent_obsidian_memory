"""Deterministic temporary-vault builders used by tests."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

FIXED_TIMESTAMP = "2026-01-02T03:04:05Z"


def concept_text(
    *,
    title: str = "Example Concept",
    concept_type: str = "Project",
    scope: str = "personal",
    body: str = "# Example Concept\n\nFixture body.\n",
) -> str:
    slug_title = title.replace('"', '\\"')
    return (
        "---\n"
        f"type: {concept_type}\n"
        f'title: "{slug_title}"\n'
        'description: "Deterministic fixture concept."\n'
        f"scope: {scope}\n"
        "created:\n"
        "  by: process:fixture\n"
        f"  at: {FIXED_TIMESTAMP}\n"
        "generated:\n"
        "  by: process:fixture\n"
        f"  at: {FIXED_TIMESTAMP}\n"
        "---\n"
        f"{body}"
    )


def build_vault(root: Path, concepts: Mapping[str, str] | None = None) -> Path:
    """Build the same minimal vault for the same input, independent of clock/order."""

    memory = root / "memory"
    concept_dir = memory / "concepts"
    concept_dir.mkdir(parents=True)
    concept_values = concepts or {"example-concept": concept_text()}
    (memory / "index.md").write_text(
        '---\nokf_version: "0.2"\n---\n# Agent Memory\n\n[Concepts](concepts/index.md)\n',
        encoding="utf-8",
    )
    (memory / "log.md").write_text("# Log\n", encoding="utf-8")
    entries = [f"- [{slug}]({slug}.md)" for slug in sorted(concept_values)]
    (concept_dir / "index.md").write_text(
        "# Concepts\n\n" + "\n".join(entries) + "\n", encoding="utf-8"
    )
    for slug, text in sorted(concept_values.items()):
        (concept_dir / f"{slug}.md").write_text(text, encoding="utf-8")
    return root
