from copy import deepcopy

import pytest

from agent_memory.markdown import FrontmatterDocument, parse_frontmatter
from agent_memory.validation import (
    count_body_words,
    is_reserved_okf_path,
    validate_base_okf,
    validate_bundle_document,
    validate_bundle_text,
    validate_local_profile,
)
from tests.fixtures.builders import concept_text


def document(**metadata: object) -> FrontmatterDocument:
    parsed = parse_frontmatter(concept_text())
    parsed.metadata.update(metadata)
    return parsed


def error_fields(result: object) -> set[str]:
    return {issue.field for issue in result.errors}


def test_minimal_base_okf_only_requires_non_empty_type() -> None:
    minimal = parse_frontmatter("---\ntype: CustomType\nunknown: kept\n---\nbody\n")

    assert validate_base_okf(minimal).ok


@pytest.mark.parametrize("value", [None, "", "   ", 3])
def test_base_okf_rejects_missing_empty_or_non_string_type(value: object) -> None:
    parsed = parse_frontmatter("---\nother: value\n---\nbody\n")
    if value is not None:
        parsed.metadata["type"] = value

    assert error_fields(validate_base_okf(parsed)) == {"type"}


def test_reserved_bundle_paths_are_exempt() -> None:
    invalid = parse_frontmatter("---\nother: value\n---\nbody\n")

    assert is_reserved_okf_path("index.md")
    assert is_reserved_okf_path("nested/index.md")
    assert is_reserved_okf_path("log.md")
    assert not is_reserved_okf_path("nested/log.md")
    assert validate_bundle_document("nested/index.md", invalid).ok
    assert not validate_bundle_document("nested/log.md", invalid).ok
    assert validate_bundle_text("nested/index.md", "# Concepts\n").ok
    assert validate_bundle_text("log.md", "# Log\n").ok
    assert "frontmatter" in error_fields(validate_bundle_text("concepts/example.md", "body\n"))


def test_local_profile_requires_title_description_scope_body_and_attribution() -> None:
    parsed = parse_frontmatter("---\ntype: Project\n---\n")
    result = validate_local_profile(parsed)

    assert {
        "title",
        "description",
        "scope",
        "body",
        "created",
        "generated",
    }.issubset(error_fields(result))


def test_valid_local_profile_passes() -> None:
    assert validate_local_profile(parse_frontmatter(concept_text())).ok


def test_scope_must_be_one_allowed_scalar() -> None:
    assert "scope" in error_fields(validate_local_profile(document(scope=["work", "personal"])))
    assert "scope" in error_fields(validate_local_profile(document(scope="other")))


def test_note_requires_content_owner() -> None:
    parsed = document(type="Note")
    assert "content_owner" in error_fields(validate_local_profile(parsed))

    parsed.metadata["content_owner"] = "user"
    assert validate_local_profile(parsed).ok


@pytest.mark.parametrize("value", [["user"], {"owner": "user"}])
def test_malformed_content_owner_is_a_validation_error(value: object) -> None:
    assert "content_owner" in error_fields(
        validate_local_profile(document(type="Note", content_owner=value))
    )


def test_bare_and_list_verified_are_accepted() -> None:
    event = {"by": "human:donald", "at": "2026-01-02T03:04:05Z"}

    assert validate_local_profile(document(verified=event)).ok
    assert validate_local_profile(document(verified=[event, deepcopy(event)])).ok


def test_sources_require_list_mappings_with_resource() -> None:
    assert "sources" in error_fields(validate_local_profile(document(sources="source.md")))
    assert "sources[0]" in error_fields(validate_local_profile(document(sources=["source.md"])))
    assert "sources[0].resource" in error_fields(
        validate_local_profile(document(sources=[{"title": "Missing"}]))
    )
    assert validate_local_profile(document(sources=[{"resource": "source.md", "extra": 1}])).ok


def test_agent_attribution_requires_exact_model_but_human_and_process_do_not() -> None:
    created = {"by": "pi/0.84.2", "at": "2026-01-02T03:04:05Z"}
    assert "created.model" in error_fields(validate_local_profile(document(created=created)))

    created["model"] = "friendly-alias"
    assert "created.model" in error_fields(validate_local_profile(document(created=created)))

    created["model"] = "openai/gpt-5"
    assert validate_local_profile(document(created=created)).ok

    for actor in ("human:donald", "process:memory-cli"):
        assert validate_local_profile(
            document(created={"by": actor, "at": "2026-01-02T03:04:05Z"})
        ).ok


def test_unknown_type_warns_for_import_but_errors_for_managed_creation() -> None:
    imported = validate_local_profile(document(type="CustomType"))
    managed = validate_local_profile(document(type="CustomType"), managed=True)

    assert imported.ok
    assert [warning.field for warning in imported.warnings] == ["type"]
    assert "type" in error_fields(managed)
    assert validate_base_okf(document(type="CustomType")).ok


def test_optional_local_fields_are_validated() -> None:
    assert "status" in error_fields(validate_local_profile(document(status="unknown")))
    assert "tags" in error_fields(validate_local_profile(document(tags=["Not-Lower"])))
    assert "stale_after" in error_fields(validate_local_profile(document(stale_after="next week")))
    assert validate_local_profile(
        document(status="draft", tags=["agent-memory"], stale_after="2026-12-31")
    ).ok


@pytest.mark.parametrize("value", [["draft"], {"value": "draft"}])
def test_malformed_status_is_a_validation_error(value: object) -> None:
    assert "status" in error_fields(validate_local_profile(document(status=value)))


def test_word_count_excludes_fence_lines_and_counts_content_and_compounds() -> None:
    body = """\
One don't state-of-the-art café.
```python
inside code = two
```
"""
    assert count_body_words(body) == 7


def test_word_limit_accepts_600_and_rejects_601() -> None:
    parsed = document()
    parsed.body = "word " * 600
    assert validate_local_profile(parsed).ok

    parsed.body += "word"
    result = validate_local_profile(parsed)
    assert "body" in error_fields(result)


def test_long_override_is_limited_to_references() -> None:
    project = document()
    project.body = "word " * 601
    assert "body" in error_fields(validate_local_profile(project, allow_long=True))

    reference = document(type="Reference")
    reference.body = "word " * 601
    assert validate_local_profile(reference, allow_long=True).ok
