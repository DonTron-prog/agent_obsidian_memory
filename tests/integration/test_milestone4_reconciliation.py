from __future__ import annotations

import io
import subprocess
import sys
from pathlib import Path

import pytest

import agent_memory.mutations as mutations
from agent_memory.cli import main
from agent_memory.config import load_config
from agent_memory.initialization import initialize_vault
from agent_memory.markdown import parse_frontmatter, render_frontmatter
from agent_memory.mutations import (
    MutationContext,
    apply_operations,
    rebuild_index,
    reconcile_concept,
)
from agent_memory.transactions import TransactionError, execute_transaction
from agent_memory.vault import discover_vault

CONTEXT = MutationContext("process:test", session_id="session-test")
AGENT_CONTEXT = MutationContext("pi/1.0", model="provider/model", session_id="session-agent")


def git(vault: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(vault), *args],
        check=True,
        capture_output=True,
        text=True,
    ).stdout


def setup_vault(tmp_path: Path):
    vault = tmp_path / "vault"
    initialize_vault(vault)
    config = load_config(vault / "system/memory.yaml")
    return vault, config, discover_vault(vault)


def create(view, config, *, slug: str = "example", title: str = "Example") -> Path:
    apply_operations(
        view,
        config,
        [
            {
                "action": "create",
                "type": "Project",
                "scope": "personal",
                "title": title,
                "description": f"Description for {title}.",
                "slug": slug,
                "body": f"# {title}\n\nOriginal body.\n",
            }
        ],
        context=CONTEXT,
        summary=f"Create {title}",
    )
    return view.bundle / "concepts" / f"{slug}.md"


def verify(view, config, slug: str = "example") -> None:
    apply_operations(
        view,
        config,
        [
            {
                "action": "verify",
                "id": slug,
                "authorization_source": "session:test#instruction",
            }
        ],
        context=AGENT_CONTEXT,
        summary="Record explicit review",
    )


def test_direct_body_and_description_reconcile_through_cli_preserves_creation(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    vault, config, view = setup_vault(tmp_path)
    path = create(view, config)
    verify(view, config)
    before = parse_frontmatter(path.read_text())
    original_created = dict(before.metadata["created"])
    before.metadata["description"] = "Corrected direct description."
    before.metadata["created"] = {
        "by": "human:forged",
        "at": "2030-01-01T00:00:00Z",
    }
    before.body = "# Example\n\nCorrected directly in Obsidian.\n"
    path.write_text(render_frontmatter(before))

    assert (
        main(
            [
                "reconcile",
                "example",
                "--summary",
                "Correct direct edit",
                "--vault",
                str(vault),
                "--json",
            ]
        )
        == 0
    )
    assert '"commit_hash":' in capsys.readouterr().out

    reconciled = parse_frontmatter(path.read_text())
    assert dict(reconciled.metadata["created"]) == original_created
    assert reconciled.metadata["generated"]["by"] == "human:donald"
    assert "model" not in reconciled.metadata["generated"]
    assert "verified" not in reconciled.metadata
    assert reconciled.body.endswith("Corrected directly in Obsidian.\n")
    assert "Corrected direct description." in view.concept_index.read_text()
    assert "**Reconciliation**" in (view.bundle / "log.md").read_text()
    assert set(git(vault, "show", "--pretty=", "--name-only", "HEAD").splitlines()) == {
        "memory/concepts/example.md",
        "memory/concepts/index.md",
        "memory/log.md",
    }


def test_created_only_direct_edit_is_rejected_without_partial_commit(tmp_path: Path) -> None:
    vault, config, view = setup_vault(tmp_path)
    path = create(view, config)
    document = parse_frontmatter(path.read_text())
    document.metadata["created"] = {
        "by": "human:forged",
        "at": "2030-01-01T00:00:00Z",
    }
    external_edit = render_frontmatter(document)
    path.write_text(external_edit)
    before_head = git(vault, "rev-parse", "HEAD")

    with pytest.raises(TransactionError, match="immutable created"):
        reconcile_concept(view, config, "example", summary="Attempt provenance rewrite")

    assert path.read_text() == external_edit
    assert git(vault, "rev-parse", "HEAD") == before_head
    assert git(vault, "status", "--short") == " M memory/concepts/example.md\n"
    assert not git(vault, "diff", "--cached", "--name-only")


def test_oversized_direct_edit_fails_without_overwrite(tmp_path: Path) -> None:
    vault, config, view = setup_vault(tmp_path)
    path = create(view, config)
    document = parse_frontmatter(path.read_text())
    document.body = "# Example\n\n" + "word " * 601 + "\n"
    external_edit = render_frontmatter(document)
    path.write_text(external_edit)
    before_head = git(vault, "rev-parse", "HEAD")

    with pytest.raises(TransactionError, match="limit is 600"):
        reconcile_concept(view, config, "example", summary="Adopt oversized edit")

    assert path.read_text() == external_edit
    assert git(vault, "rev-parse", "HEAD") == before_head
    assert not git(vault, "diff", "--cached", "--name-only")


def test_verification_requires_explicit_provenance_and_preserves_independent_checks(
    tmp_path: Path,
) -> None:
    vault, config, view = setup_vault(tmp_path)
    path = create(view, config)
    document = parse_frontmatter(path.read_text())
    original_generated = dict(document.metadata["generated"])
    document.metadata["verified"] = {
        "by": "process:validator",
        "at": "2026-01-02T03:04:05Z",
    }
    path.write_text(render_frontmatter(document))
    git(vault, "add", "memory/concepts/example.md")
    git(
        vault,
        "-c",
        "user.name=Test",
        "-c",
        "user.email=test@localhost",
        "commit",
        "-m",
        "fixture: add machine verification",
    )

    with pytest.raises(TransactionError, match="authorization source"):
        apply_operations(
            view,
            config,
            [{"action": "verify", "id": "example"}],
            context=AGENT_CONTEXT,
            summary="Unproven review",
        )

    verify(view, config)
    verified = parse_frontmatter(path.read_text())
    assert [event["by"] for event in verified.metadata["verified"]] == [
        "process:validator",
        "human:donald",
    ]
    assert verified.metadata["verified"][1]["authorization_source"] == ("session:test#instruction")
    assert dict(verified.metadata["generated"]) == original_generated


def test_source_change_invalidates_but_rename_and_verification_do_not(tmp_path: Path) -> None:
    vault, config, view = setup_vault(tmp_path)
    path = create(view, config)
    verify(view, config)
    verified = parse_frontmatter(path.read_text())
    generated = dict(verified.metadata["generated"])

    apply_operations(
        view,
        config,
        [{"action": "rename", "id": "example", "new_slug": "renamed-example"}],
        context=CONTEXT,
        summary="Improve slug",
    )
    path = view.bundle / "concepts/renamed-example.md"
    renamed = parse_frontmatter(path.read_text())
    assert "verified" in renamed.metadata
    assert dict(renamed.metadata["generated"]) == generated

    verify(view, config, "renamed-example")
    verification_only = parse_frontmatter(path.read_text())
    assert len(verification_only.metadata["verified"]) == 2
    assert dict(verification_only.metadata["generated"]) == generated

    apply_operations(
        view,
        config,
        [
            {
                "action": "update",
                "id": "renamed-example",
                "sources": ["session:test#new-source"],
            }
        ],
        context=MutationContext("process:updater"),
        summary="Change source provenance",
    )
    changed = parse_frontmatter(path.read_text())
    assert "verified" not in changed.metadata
    assert changed.metadata["generated"]["by"] == "process:updater"
    assert "**Verification**" in (view.bundle / "log.md").read_text()
    assert "human:donald" in git(vault, "show", "HEAD^:memory/concepts/renamed-example.md")


def test_status_and_tag_only_update_preserves_generated_and_verification(tmp_path: Path) -> None:
    _, config, view = setup_vault(tmp_path)
    path = create(view, config)
    verify(view, config)
    before = parse_frontmatter(path.read_text())
    generated = dict(before.metadata["generated"])
    verification = [dict(event) for event in before.metadata["verified"]]

    apply_operations(
        view,
        config,
        [{"action": "update", "id": "example", "status": "draft", "tags": ["review"]}],
        context=CONTEXT,
        summary="Adjust presentation metadata",
    )

    updated = parse_frontmatter(path.read_text())
    assert dict(updated.metadata["generated"]) == generated
    assert [dict(event) for event in updated.metadata["verified"]] == verification


def test_interactive_verification_bypass_is_limited_to_one_human_verify(
    tmp_path: Path,
) -> None:
    _, config, view = setup_vault(tmp_path)
    create(view, config)
    operation = {"action": "verify", "id": "example"}

    with pytest.raises(TransactionError, match="one verify operation"):
        apply_operations(
            view,
            config,
            [operation],
            context=AGENT_CONTEXT,
            summary="Invalid interactive context",
            interactive_verification=True,
        )
    with pytest.raises(TransactionError, match="one verify operation"):
        apply_operations(
            view,
            config,
            [operation, operation],
            context=MutationContext("human:donald"),
            summary="Invalid interactive batch",
            interactive_verification=True,
        )


def test_reconcile_commits_only_selected_concept_and_clean_derived_files(
    tmp_path: Path,
) -> None:
    vault, config, view = setup_vault(tmp_path)
    selected = create(view, config, slug="selected", title="Selected")
    unrelated = create(view, config, slug="unrelated", title="Unrelated")
    selected_document = parse_frontmatter(selected.read_text())
    selected_document.body = "# Selected\n\nAdopt this edit.\n"
    selected.write_text(render_frontmatter(selected_document))
    unrelated_document = parse_frontmatter(unrelated.read_text())
    unrelated_document.metadata["title"] = "Unreconciled Title"
    unrelated.write_text(render_frontmatter(unrelated_document))

    result = reconcile_concept(view, config, "selected", summary="Adopt selected edit")

    assert set(result.transaction.changed_paths) == {
        "memory/concepts/selected.md",
        "memory/log.md",
    }
    assert git(vault, "status", "--short") == " M memory/concepts/unrelated.md\n"
    assert "Unreconciled Title" not in view.concept_index.read_text()
    assert "Unreconciled Title" in unrelated.read_text()


def test_reconcile_detects_a_concurrent_change_before_replacement(tmp_path: Path) -> None:
    vault, config, view = setup_vault(tmp_path)
    path = create(view, config)
    document = parse_frontmatter(path.read_text())
    document.body = "# Example\n\nFirst external edit.\n"
    path.write_text(render_frontmatter(document))

    def race(point: str) -> None:
        if point == "before_replace:memory/concepts/example.md":
            path.write_text(path.read_text() + "Concurrent edit wins.\n")

    with pytest.raises(TransactionError, match="changed before replacement"):
        reconcile_concept(
            view,
            config,
            "example",
            summary="Race-safe reconcile",
            fault_hook=race,
        )

    assert path.read_text().endswith("Concurrent edit wins.\n")
    assert not git(vault, "diff", "--cached", "--name-only")


@pytest.mark.parametrize("relative", ["memory/concepts/index.md", "memory/log.md"])
def test_transaction_blocks_requested_dirty_output_even_when_render_is_byte_identical(
    tmp_path: Path, relative: str
) -> None:
    vault, config, _ = setup_vault(tmp_path)
    target = vault / relative
    proposed = target.read_bytes() + b"direct edit\n"
    target.write_bytes(proposed)
    before_head = git(vault, "rev-parse", "HEAD")

    with pytest.raises(TransactionError, match="uncommitted changes"):
        execute_transaction(
            vault,
            Path(config["transactions"]["state_dir"]),
            {relative: proposed, "system/status.md": b"# Status\n\nChanged.\n"},
            branch="main",
            actor="process:test",
            model=None,
            session_id=None,
            summary="Exercise requested-output preflight",
            subject="memory(process): test preflight",
            concept_ids=(),
        )

    assert git(vault, "rev-parse", "HEAD") == before_head
    assert target.read_bytes() == proposed
    assert not git(vault, "diff", "--cached", "--name-only")


def test_current_index_no_op_preserves_dry_run_and_rejects_dirty_equivalent(
    tmp_path: Path,
) -> None:
    vault, config, view = setup_vault(tmp_path)
    create(view, config)

    result = rebuild_index(view, config, dry_run=True)
    assert result.transaction.transaction_id == "no-op"
    assert result.transaction.dry_run is True
    assert result.transaction.changed_paths == ()

    correct = view.concept_index.read_text()
    view.concept_index.write_text("# Concepts\n\nCommitted stale index.\n")
    git(vault, "add", "memory/concepts/index.md")
    git(
        vault,
        "-c",
        "user.name=Test",
        "-c",
        "user.email=test@localhost",
        "commit",
        "-m",
        "fixture: stale generated index",
    )
    view.concept_index.write_text(correct)

    with pytest.raises(TransactionError, match="uncommitted changes"):
        rebuild_index(view, config)

    assert view.concept_index.read_text() == correct
    assert not git(vault, "diff", "--cached", "--name-only")


def test_full_rebuild_refuses_concept_edit_racing_initial_check(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    vault, config, view = setup_vault(tmp_path)
    concept = create(view, config)
    before_head = git(vault, "rev-parse", "HEAD")
    before_index = view.concept_index.read_text()
    original_render = mutations._render_index

    def race(committed: dict[str, str]) -> str:
        assert "Raced description." not in next(iter(committed.values()))
        rendered = original_render(committed)
        document = parse_frontmatter(concept.read_text())
        document.metadata["description"] = "Raced description."
        concept.write_text(render_frontmatter(document))
        return rendered

    monkeypatch.setattr(mutations, "_render_index", race)

    with pytest.raises(TransactionError, match="unreconciled concept edits"):
        rebuild_index(view, config)

    assert "Raced description." in concept.read_text()
    assert view.concept_index.read_text() == before_index
    assert git(vault, "rev-parse", "HEAD") == before_head
    assert not git(vault, "diff", "--cached", "--name-only")


def test_full_rebuild_refuses_dirty_corpus_then_rebuilds_clean_corpus(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    vault, config, view = setup_vault(tmp_path)
    concept = create(view, config)
    view.concept_index.write_text("# Concepts\n\nCommitted stale index.\n")
    git(vault, "add", "memory/concepts/index.md")
    git(
        vault,
        "-c",
        "user.name=Test",
        "-c",
        "user.email=test@localhost",
        "commit",
        "-m",
        "fixture: stale generated index",
    )
    concept.write_text(concept.read_text() + "Unreconciled edit.\n")

    with pytest.raises(TransactionError, match="unreconciled concept edits"):
        rebuild_index(view, config)
    assert view.concept_index.read_text() == "# Concepts\n\nCommitted stale index.\n"

    git(vault, "restore", "memory/concepts/example.md")
    assert main(["rebuild-index", "--vault", str(vault), "--json"]) == 0
    assert '"changed_paths":["memory/concepts/index.md"]' in capsys.readouterr().out
    assert "[Example](example.md)" in view.concept_index.read_text()


def test_interactive_verification_and_conflict_guidance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    vault, config, view = setup_vault(tmp_path)
    path = create(view, config)
    before_head = git(vault, "rev-parse", "HEAD")
    assert main(["verify", "example", "--vault", str(vault), "--json"]) == 2
    assert git(vault, "rev-parse", "HEAD") == before_head
    assert "authorization-source" in capsys.readouterr().out

    class InteractiveInput(io.StringIO):
        def isatty(self) -> bool:
            return True

    monkeypatch.setattr(sys, "stdin", InteractiveInput("yes\n"))
    assert main(["verify", "example", "--vault", str(vault)]) == 0
    verified = parse_frontmatter(path.read_text())
    assert verified.metadata["verified"][0]["by"] == "human:donald"
    assert (
        git(vault, "show", "-s", "--format=%B", "HEAD")
        .splitlines()[0]
        .startswith("memory(human): verify")
    )
    assert main(["show", "example", "--no-audit", "--vault", str(vault)]) == 0
    assert "Verification: human-reviewed" in capsys.readouterr().out

    conflict = vault / "example.sync-conflict-20260101.md"
    conflict.write_text("conflict")
    with pytest.raises(TransactionError) as error:
        apply_operations(
            view,
            config,
            [{"action": "verify", "id": "example", "authorization_source": "session:test"}],
            context=AGENT_CONTEXT,
            summary="Blocked verification",
        )
    message = str(error.value)
    assert "Resolve conflict copies manually" in message
    assert "memory reconcile" in message
    assert "memory doctor" in message
