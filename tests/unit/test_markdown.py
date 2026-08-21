from pathlib import Path

import pytest

from agent_memory.markdown import FrontmatterError, parse_frontmatter, render_frontmatter


def test_round_trip_preserves_unknown_metadata_and_body() -> None:
    body = "# Title\n\nBody with  two spaces.\n"
    text = f"---\ntype: Project\nproducer_extension:\n  nested: [one, two]\n---\n{body}"

    document = parse_frontmatter(text)
    rendered = render_frontmatter(document)
    reparsed = parse_frontmatter(rendered)

    assert reparsed.metadata["producer_extension"] == {"nested": ["one", "two"]}
    assert reparsed.body == body


def test_requires_leading_mapping_frontmatter() -> None:
    with pytest.raises(FrontmatterError, match="begin"):
        parse_frontmatter("# no frontmatter\n")
    with pytest.raises(FrontmatterError, match="mapping"):
        parse_frontmatter("---\n- list\n---\nbody\n")


def test_rejects_duplicate_keys() -> None:
    with pytest.raises(FrontmatterError, match="duplicate key"):
        parse_frontmatter("---\ntype: Project\ntype: Note\n---\nbody\n")


def test_python_object_tag_is_not_constructed(tmp_path: Path) -> None:
    marker = tmp_path / "executed"
    text = (
        "---\n"
        "type: Project\n"
        f"payload: !!python/object/apply:pathlib.Path.touch ['{marker}']\n"
        "---\nbody\n"
    )

    with pytest.raises(FrontmatterError, match="custom YAML tag"):
        parse_frontmatter(text)
    assert not marker.exists()
