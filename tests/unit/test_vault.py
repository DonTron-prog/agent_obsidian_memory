from pathlib import Path

import pytest

from agent_memory.vault import (
    VaultError,
    discover_vault,
    indexed_concept_ids,
    parse_index,
    scan_concepts,
    validate_vault,
)
from tests.fixtures.builders import build_vault, concept_text


def test_discovers_bundle_and_follows_root_to_concept_index(tmp_path: Path) -> None:
    root = build_vault(
        tmp_path,
        {"zulu": concept_text(title="Zulu"), "alpha": concept_text(title="Alpha")},
    )
    vault = discover_vault(root)

    assert [link.target for link in parse_index(vault.root_index, vault.root)] == [
        "memory/concepts/index.md"
    ]
    assert indexed_concept_ids(vault) == ("concepts/alpha", "concepts/zulu")


def test_nested_concept_index_link_does_not_count_as_direct_entry(tmp_path: Path) -> None:
    vault = discover_vault(build_vault(tmp_path))
    vault.concept_index.write_text(
        "# Concepts\n\n- [nested](nested/example-concept.md)\n", encoding="utf-8"
    )

    assert indexed_concept_ids(vault) == ()
    assert "missing concept entry: concepts/example-concept" in {
        issue.message for issue in validate_vault(vault)
    }


def test_index_and_concept_symlinks_cannot_escape_vault(tmp_path: Path) -> None:
    root = build_vault(tmp_path / "vault")
    outside = tmp_path / "outside.md"
    outside.write_text(concept_text(), encoding="utf-8")
    vault = discover_vault(root)
    (vault.bundle / "concepts" / "escape.md").symlink_to(outside)

    with pytest.raises(VaultError, match="unsafe vault file"):
        scan_concepts(vault)

    vault.root_index.write_text("[escape](../../outside.md)\n", encoding="utf-8")
    with pytest.raises(VaultError, match="escapes vault"):
        parse_index(vault.root_index, vault.root)


def test_validation_reports_local_errors_links_and_index_drift_deterministically(
    tmp_path: Path,
) -> None:
    root = build_vault(
        tmp_path,
        {
            "known": concept_text(body="# Known\n\n[missing](missing.md)\n"),
            "unknown": concept_text(title="Unknown", concept_type="ProducerType"),
        },
    )
    vault = discover_vault(root)
    vault.concept_index.write_text("# Concepts\n\n- [known](known.md)\n", encoding="utf-8")

    first = validate_vault(vault)
    second = validate_vault(vault)

    assert first == second
    assert not [issue for issue in first if issue.level == "error"]
    messages = [issue.message for issue in first]
    assert "broken link: missing.md" in messages
    assert "missing concept entry: concepts/unknown" in messages
    assert any("outside the configured vocabulary" in message for message in messages)


def test_validation_enforces_okf_boundary_for_non_reserved_markdown(tmp_path: Path) -> None:
    vault = discover_vault(build_vault(tmp_path))
    extra = vault.bundle / "other.md"
    extra.write_text("no frontmatter\n", encoding="utf-8")

    issues = validate_vault(vault)

    assert any(issue.path == "memory/other.md" and issue.level == "error" for issue in issues)
