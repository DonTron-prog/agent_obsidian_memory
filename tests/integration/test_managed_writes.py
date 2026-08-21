from __future__ import annotations

import copy
import os
import shutil
import subprocess
import uuid
from pathlib import Path

import pytest

from agent_memory.config import load_config
from agent_memory.initialization import initialize_vault
from agent_memory.mutations import MutationContext, apply_operations
from agent_memory.transactions import TransactionError
from agent_memory.vault import discover_vault, validate_vault

CONTEXT = MutationContext("process:test", session_id="session-1")


def git(vault: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(vault), *args],
        check=True,
        capture_output=True,
        text=True,
    ).stdout


def setup_vault(tmp_path: Path) -> tuple[Path, dict, object]:
    vault = tmp_path / "vault"
    initialize_vault(vault)
    config = load_config(vault / "system/memory.yaml")
    return vault, config, discover_vault(vault)


def create(
    view: object,
    config: dict,
    *,
    title: str = "Example Project",
    slug: str = "example-project",
    body: str = "# Example Project\n\nBody.\n",
    concept_type: str = "Project",
    content_owner: str | None = None,
) -> object:
    operation = {
        "action": "create",
        "type": concept_type,
        "scope": "personal",
        "title": title,
        "description": f"Description for {title}.",
        "slug": slug,
        "body": body,
    }
    if content_owner:
        operation["content_owner"] = content_owner
    return apply_operations(view, config, [operation], context=CONTEXT, summary=f"Create {title}")


def test_batch_changes_multiple_concepts_in_one_exact_path_commit(tmp_path: Path) -> None:
    vault, config, view = setup_vault(tmp_path)
    before = int(git(vault, "rev-list", "--count", "HEAD"))
    (vault / "unrelated.txt").write_text("dirty\n")
    operations = [
        {
            "action": "create",
            "type": "Project",
            "scope": "personal",
            "title": title,
            "description": f"{title} description.",
            "slug": title.casefold(),
            "body": f"# {title}\n\nBody.\n",
        }
        for title in ("One", "Two")
    ]

    result = apply_operations(view, config, operations, context=CONTEXT, summary="Create pair")

    assert int(git(vault, "rev-list", "--count", "HEAD")) == before + 1
    assert set(result.transaction.changed_paths) == {
        "memory/concepts/one.md",
        "memory/concepts/two.md",
        "memory/concepts/index.md",
        "memory/log.md",
    }
    assert git(vault, "status", "--short") == "?? unrelated.txt\n"
    assert not git(vault, "diff", "--cached", "--name-only")


def test_duplicate_slug_and_normalized_title_are_rejected(tmp_path: Path) -> None:
    _, config, view = setup_vault(tmp_path)
    create(view, config)
    with pytest.raises(TransactionError, match="duplicate slug"):
        create(view, config, title="Other", slug="example-project")
    with pytest.raises(TransactionError, match="duplicate normalized title"):
        create(view, config, title="  EXAMPLE   project ", slug="other")
    create(view, config, title="Second", slug="second")
    with pytest.raises(TransactionError, match="duplicate normalized title"):
        apply_operations(
            view,
            config,
            [{"action": "update", "id": "second", "title": "example project"}],
            context=CONTEXT,
            summary="Duplicate title",
        )


def test_prestaged_and_dirty_derived_paths_block_without_absorbing_unrelated(
    tmp_path: Path,
) -> None:
    vault, config, view = setup_vault(tmp_path)
    (vault / "staged.txt").write_text("staged\n")
    git(vault, "add", "staged.txt")
    with pytest.raises(TransactionError, match="pre-existing staged"):
        create(view, config)
    git(vault, "restore", "--staged", "staged.txt")

    for relative in ("memory/concepts/index.md", "memory/log.md"):
        path = vault / relative
        original = path.read_text()
        path.write_text(original + "human edit\n")
        with pytest.raises(TransactionError, match="uncommitted"):
            create(view, config)
        path.write_text(original)

    create(view, config, title="Unrelated", slug="unrelated")
    unrelated = vault / "memory/concepts/unrelated.md"
    unrelated.write_text(unrelated.read_text().replace("Unrelated", "Human Dirty", 1))
    create(view, config, title="Second", slug="second")
    assert "Human Dirty" not in (vault / "memory/concepts/index.md").read_text()
    assert "Human Dirty" in unrelated.read_text()


def test_conflict_artifact_blocks_but_syncthing_availability_and_remote_do_not(
    tmp_path: Path,
) -> None:
    vault, config, view = setup_vault(tmp_path)
    conflict = vault / "nested/example.sync-conflict-20260821.md"
    conflict.parent.mkdir()
    conflict.write_text("conflict")
    with pytest.raises(TransactionError, match="Syncthing conflict"):
        create(view, config)
    conflict.unlink()
    git(vault, "remote", "add", "origin", "ssh://unavailable.invalid/private.git")

    result = create(view, config)
    assert result.transaction.commit_hash
    assert git(vault, "remote").strip() == "origin"


def test_note_deletion_authorization_and_git_recovery(tmp_path: Path) -> None:
    vault, config, view = setup_vault(tmp_path)
    create(
        view,
        config,
        title="Shopping",
        slug="shopping",
        concept_type="Note",
        content_owner="user",
    )
    with pytest.raises(TransactionError, match="authorization"):
        apply_operations(
            view,
            config,
            [{"action": "delete", "id": "shopping"}],
            context=CONTEXT,
            summary="Delete note",
        )
    apply_operations(
        view,
        config,
        [
            {
                "action": "delete",
                "id": "shopping",
                "authorized_by": "human:donald",
                "authorization_source": "session:test#instruction",
            }
        ],
        context=CONTEXT,
        summary="Delete authorized note",
    )
    assert not (vault / "memory/concepts/shopping.md").exists()
    assert "Shopping" in git(vault, "show", "HEAD^:memory/concepts/shopping.md")
    assert "session:test#instruction" in (vault / "memory/log.md").read_text()


def test_user_owned_content_cannot_be_retyped_then_deleted_without_authorization(
    tmp_path: Path,
) -> None:
    vault, config, view = setup_vault(tmp_path)
    create(
        view,
        config,
        title="Private list",
        slug="private-list",
        concept_type="Note",
        content_owner="user",
    )
    before = git(vault, "rev-parse", "HEAD")

    with pytest.raises(TransactionError, match="authorization"):
        apply_operations(
            view,
            config,
            [
                {"action": "update", "id": "private-list", "type": "Project"},
                {"action": "delete", "id": "private-list"},
            ],
            context=CONTEXT,
            summary="Attempt authorization bypass",
        )

    assert (vault / "memory/concepts/private-list.md").exists()
    assert git(vault, "rev-parse", "HEAD") == before
    assert not git(vault, "diff", "--cached", "--name-only")


def test_rename_updates_links_and_preserves_attribution_and_verification(tmp_path: Path) -> None:
    vault, config, view = setup_vault(tmp_path)
    create(view, config, title="Target", slug="target")
    create(
        view,
        config,
        title="Linker",
        slug="linker",
        body="# Linker\n\nSee [Target](target.md) and [[target]].\n",
    )
    before = (vault / "memory/concepts/target.md").read_text()

    apply_operations(
        view,
        config,
        [{"action": "rename", "id": "target", "new_slug": "renamed-target"}],
        context=CONTEXT,
        summary="Correct slug",
    )

    linker = (vault / "memory/concepts/linker.md").read_text()
    assert "(renamed-target.md)" in linker
    assert "[[renamed-target]]" in linker
    assert (vault / "memory/concepts/renamed-target.md").read_text() == before
    assert not (vault / "memory/concepts/target.md").exists()


def test_secret_rejection_and_state_directory_policy_have_no_side_effects(tmp_path: Path) -> None:
    vault, config, view = setup_vault(tmp_path)
    initial_head = git(vault, "rev-parse", "HEAD")
    with pytest.raises(ValueError, match="secret-bearing"):
        create(
            view,
            config,
            body="# Example\n\napi_key = abcdefghijklmnop\n",
        )
    with pytest.raises(ValueError, match="secret-bearing managed filename"):
        create(view, config, title="API Token", slug="api-token")
    assert git(vault, "rev-parse", "HEAD") == initial_head
    assert not (vault / "memory/concepts/example-project.md").exists()
    assert not (vault / "memory/concepts/api-token.md").exists()
    assert not git(vault, "status", "--short")

    unsafe = copy.deepcopy(config)
    unsafe["transactions"]["state_dir"] = str(vault / "transaction-state")
    with pytest.raises(TransactionError, match="outside"):
        create(view, unsafe)
    assert not (vault / "transaction-state").exists()


def test_transaction_state_must_share_vault_filesystem(tmp_path: Path) -> None:
    vault, config, view = setup_vault(tmp_path)
    other_root = Path("/dev/shm")
    if not other_root.is_dir() or other_root.stat().st_dev == vault.stat().st_dev:
        pytest.skip("no writable alternate filesystem")
    state = other_root / f"agent-memory-test-{uuid.uuid4().hex}"
    unsafe = copy.deepcopy(config)
    unsafe["transactions"]["state_dir"] = str(state)
    try:
        with pytest.raises(TransactionError, match="same filesystem"):
            create(view, unsafe)
    finally:
        shutil.rmtree(state, ignore_errors=True)


def test_dirty_concept_aborts_without_overwrite(tmp_path: Path) -> None:
    vault, config, view = setup_vault(tmp_path)
    create(view, config)
    target = vault / "memory/concepts/example-project.md"
    target.write_text(target.read_text() + "human edit\n")

    with pytest.raises(TransactionError, match="uncommitted"):
        apply_operations(
            view,
            config,
            [{"action": "update", "id": "example-project", "body": "# New\n\nNew.\n"}],
            context=CONTEXT,
            summary="Unsafe update",
        )
    assert target.read_text().endswith("human edit\n")
    assert not git(vault, "diff", "--cached", "--name-only")


def test_git_environment_and_hooks_cannot_widen_commit(tmp_path: Path, monkeypatch) -> None:
    vault, config, view = setup_vault(tmp_path)
    hook = vault / ".git/hooks/pre-commit"
    marker = vault / "hook-ran"
    hook.write_text(f"#!/bin/sh\ntouch {marker}\ngit add hook-ran\n")
    hook.chmod(0o755)
    alternate_index = tmp_path / "alternate-index"
    monkeypatch.setenv("GIT_INDEX_FILE", str(alternate_index))
    monkeypatch.setenv("GIT_WORK_TREE", str(tmp_path / "wrong-work-tree"))
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "core.hooksPath")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", str(vault / ".git/hooks"))

    result = create(view, config)

    assert result.transaction.commit_hash
    assert not marker.exists()
    assert not alternate_index.exists()
    for key in tuple(os.environ):
        if key.startswith("GIT_"):
            monkeypatch.delenv(key, raising=False)
    assert not git(vault, "status", "--short")
    assert set(git(vault, "show", "--pretty=", "--name-only", "HEAD").splitlines()) == {
        "memory/concepts/example-project.md",
        "memory/concepts/index.md",
        "memory/log.md",
    }


def test_commit_pathspec_excludes_raced_unrelated_stage(tmp_path: Path) -> None:
    vault, config, view = setup_vault(tmp_path)
    outside = vault / "outside.txt"

    def race(point: str) -> None:
        if point == "after_committing_journal":
            outside.write_text("user staged during commit\n")
            git(vault, "add", "outside.txt")

    result = apply_operations(
        view,
        config,
        [
            {
                "action": "create",
                "type": "Project",
                "scope": "personal",
                "title": "Race Safe",
                "description": "Only transaction paths may be committed.",
                "slug": "race-safe",
                "body": "# Race Safe\n\nBody.\n",
            }
        ],
        context=CONTEXT,
        summary="Commit only owned paths",
        fault_hook=race,
    )

    assert set(
        git(vault, "show", "--pretty=", "--name-only", result.transaction.commit_hash).splitlines()
    ) == set(result.transaction.changed_paths)
    assert git(vault, "diff", "--cached", "--name-only") == "outside.txt\n"
    assert git(vault, "show", ":outside.txt") == "user staged during commit\n"


def test_intent_to_add_blocks_transaction_without_side_effects(tmp_path: Path) -> None:
    vault, config, view = setup_vault(tmp_path)
    intent = vault / "intent.txt"
    intent.write_text("user work\n")
    git(vault, "add", "-N", "intent.txt")
    before = git(vault, "status", "--porcelain=v1")

    with pytest.raises(TransactionError, match="pre-existing staged"):
        create(view, config)

    assert git(vault, "status", "--porcelain=v1") == before
    assert not (vault / "memory/concepts/example-project.md").exists()


def test_generated_markdown_escapes_untrusted_metadata_and_provenance(tmp_path: Path) -> None:
    vault, config, view = setup_vault(tmp_path)
    context = MutationContext(
        "pi/1`actor`",
        model="provider/model`name`",
        session_id="`session`\n## Forged session",
    )
    apply_operations(
        view,
        config,
        [
            {
                "action": "create",
                "type": "Project",
                "scope": "personal",
                "title": "<img src=x onerror=alert(1)> Unsafe ](../../outside)\n## Injected",
                "description": "[escape](../../outside)\n## More",
                "slug": "safe-slug",
                "body": "# Safe\n\nBody.\n",
            }
        ],
        context=context,
        summary="<script>alert(1)</script> [summary](../../outside)\n## Forged day",
    )

    index = (vault / "memory/concepts/index.md").read_text()
    log = (vault / "memory/log.md").read_text()
    assert "&lt;img src=x onerror=alert(1)&gt;" in index
    assert "&#93;(../../outside) ## Injected" in index
    assert "&#91;escape&#93;(../../outside) ## More" in index
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in log
    assert "&#91;summary&#93;(../../outside) ## Forged day" in log
    assert "Actor `` pi/1`actor` ``" in log
    assert "model `` provider/model`name` ``" in log
    assert "session `` `session` ## Forged session ``" in log
    assert "<img" not in index
    assert "<script" not in log
    assert "\n## Injected\n" not in index
    assert "\n## Forged day\n" not in log
    assert "\n## Forged session\n" not in log
    assert validate_vault(discover_vault(vault)) == ()


def test_batch_update_requires_actual_input(tmp_path: Path) -> None:
    vault, config, view = setup_vault(tmp_path)
    create(view, config)
    before = git(vault, "rev-parse", "HEAD")
    with pytest.raises(TransactionError, match="requires body or metadata"):
        apply_operations(
            view,
            config,
            [{"action": "update", "id": "example-project"}],
            context=CONTEXT,
            summary="No update",
        )
    assert git(vault, "rev-parse", "HEAD") == before
    assert not git(vault, "status", "--short")


def test_body_file_must_be_regular_non_secret_input(tmp_path: Path) -> None:
    vault, config, view = setup_vault(tmp_path)
    actual = tmp_path / "body.md"
    actual.write_text("# Body\n\nText.\n")
    link = tmp_path / "body-link.md"
    link.symlink_to(actual)
    with pytest.raises(TransactionError, match="regular non-symlink"):
        apply_operations(
            view,
            config,
            [
                {
                    "action": "create",
                    "type": "Project",
                    "scope": "personal",
                    "title": "Linked body",
                    "description": "Must fail.",
                    "slug": "linked-body",
                    "body_file": str(link),
                }
            ],
            context=CONTEXT,
            summary="Reject linked body",
        )
    secret_name = tmp_path / "api-token.md"
    secret_name.write_text("ordinary text")
    with pytest.raises(ValueError, match="secret-bearing managed filename"):
        apply_operations(
            view,
            config,
            [
                {
                    "action": "create",
                    "type": "Project",
                    "scope": "personal",
                    "title": "Secret path",
                    "description": "Must fail.",
                    "slug": "secret-path-input",
                    "body_file": str(secret_name),
                }
            ],
            context=CONTEXT,
            summary="Reject secret path",
        )


def test_mutation_context_is_validated_for_non_content_actions(tmp_path: Path) -> None:
    _, config, view = setup_vault(tmp_path)
    create(view, config)
    for operation in (
        {"action": "delete", "id": "example-project"},
        {"action": "rename", "id": "example-project", "new_slug": "renamed"},
    ):
        with pytest.raises(TransactionError, match="runtime version"):
            apply_operations(
                view,
                config,
                [operation],
                context=MutationContext("pi", model="openai/gpt-5"),
                summary="Invalid agent actor",
            )
        with pytest.raises(TransactionError, match="omit model"):
            apply_operations(
                view,
                config,
                [operation],
                context=MutationContext("human:donald", model="openai/gpt-5"),
                summary="Invalid human model",
            )


def test_hash_recheck_preserves_synchronized_change(tmp_path: Path) -> None:
    vault, config, view = setup_vault(tmp_path)
    create(view, config)
    target = vault / "memory/concepts/example-project.md"

    def synchronize(point: str) -> None:
        if point == "before_replace:memory/concepts/example-project.md":
            target.write_text(target.read_text() + "synchronized change\n")

    with pytest.raises(TransactionError, match="changed before replacement"):
        apply_operations(
            view,
            config,
            [{"action": "update", "id": "example-project", "body": "# New\n\nNew body.\n"}],
            context=CONTEXT,
            summary="Update",
            fault_hook=synchronize,
        )
    assert target.read_text().endswith("synchronized change\n")
    assert not git(vault, "diff", "--cached", "--name-only")
