from __future__ import annotations

import json
import os
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from ruamel.yaml import YAML

from agent_memory.cli import main
from agent_memory.config import load_config
from agent_memory.initialization import initialize_vault
from agent_memory.mutations import MutationContext, apply_operations
from agent_memory.transactions import InjectedCrash
from agent_memory.vault import discover_vault

CONTEXT = MutationContext("process:test", session_id="session-test")


def git(root: Path, *args: str) -> str:
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    return subprocess.run(
        ["git", "-C", str(root), *args],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    ).stdout


def payload(capsys: pytest.CaptureFixture[str]) -> dict:
    return json.loads(capsys.readouterr().out)


def test_cli_managed_write_and_recovery_contract(tmp_path: Path, capsys) -> None:
    vault = tmp_path / "vault"
    body = tmp_path / "body.md"
    body.write_text("# CLI Project\n\nInitial body.\n")

    assert main(["init", "--vault", str(vault), "--json"]) == 0
    assert payload(capsys)["created"]
    assert main(["init", "--vault", str(vault), "--json"]) == 0
    assert payload(capsys)["created"] == []

    common = ["--vault", str(vault), "--json"]
    mutation_common = ["--agent", "process:test", *common]
    assert (
        main(
            [
                "create",
                "--type",
                "Project",
                "--scope",
                "personal",
                "--title",
                "CLI Project",
                "--description",
                "Created through the public CLI.",
                "--body-file",
                str(body),
                "--slug",
                "cli-project",
                "--source",
                "session:test#instruction",
                *mutation_common,
            ]
        )
        == 0
    )
    assert payload(capsys)["commit_hash"]

    body.write_text("# CLI Project\n\nUpdated body.\n")
    assert main(["update", "cli-project", "--body-file", str(body), *mutation_common]) == 0
    payload(capsys)
    assert (
        main(
            [
                "rename",
                "cli-project",
                "renamed-project",
                "--reason",
                "Clearer",
                *mutation_common,
            ]
        )
        == 0
    )
    payload(capsys)

    batch_body = tmp_path / "batch.md"
    batch_body.write_text("# Batch Concept\n\nBody.\n")
    batch = tmp_path / "batch.yaml"
    batch.write_text(
        f"""version: 1
actor:
  by: process:test
  session_id: session-test
summary: Create a sourced batch concept.
operations:
  - action: create
    type: Reference
    scope: personal
    title: Batch Concept
    description: A batch-created reference.
    slug: batch-concept
    body_file: {batch_body}
    sources:
      - resource: ../../sessions/pi/2026/session.md#checkpoint-1--compaction
        checkpoint_id: pi:session:compact:1
""",
        encoding="utf-8",
    )
    assert main(["apply", str(batch), *mutation_common]) == 0
    assert payload(capsys)["commit_hash"]
    metadata = YAML(typ="safe").load(
        (vault / "memory/concepts/batch-concept.md").read_text().split("---", 2)[1]
    )
    assert metadata["sources"][0]["checkpoint_id"] == "pi:session:compact:1"

    assert main(["delete", "renamed-project", "--reason", "Obsolete", *mutation_common]) == 0
    payload(capsys)
    assert not (vault / "memory/concepts/renamed-project.md").exists()

    config = load_config(vault / "system/memory.yaml")
    view = discover_vault(vault)

    def crash(point: str) -> None:
        if point == "after_stage":
            raise InjectedCrash(point)

    with pytest.raises(InjectedCrash):
        apply_operations(
            view,
            config,
            [{"action": "update", "id": "batch-concept", "body": "# Batch\n\nChanged.\n"}],
            context=CONTEXT,
            summary="Crash for CLI recovery",
            fault_hook=crash,
        )
    state = Path(config["transactions"]["state_dir"])
    transaction_id = next(
        path.parent.name
        for path in state.glob("*/journal.json")
        if '"phase":"staged"' in path.read_text()
    )
    assert main(["recover", "--transaction", transaction_id, *common]) == 0
    assert payload(capsys)["action"] == "rollback"
    assert main(["recover", "--transaction", transaction_id, "--apply", *common]) == 0
    assert payload(capsys)["phase"] == "rolled_back"

    assert (
        main(
            [
                "create",
                "--type",
                "Project",
                "--scope",
                "personal",
                "--title",
                "Invalid Actor",
                "--description",
                "Must fail as JSON.",
                "--body-file",
                str(body),
                "--agent",
                "pi",
                "--model",
                "openai/gpt-5",
                *common,
            ]
        )
        == 2
    )
    assert "runtime version" in payload(capsys)["error"]


def test_cli_update_requires_body_file_and_batch_requires_text_summary(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    vault = tmp_path / "vault"
    initialize_vault(vault)
    with pytest.raises(SystemExit):
        main(["update", "missing", "--vault", str(vault)])
    capsys.readouterr()

    batch = tmp_path / "invalid-batch.yaml"
    batch.write_text(
        """version: 1
actor:
  by: process:test
summary: [not, text]
operations:
  - action: update
    id: missing
    body_file: /tmp/missing.md
""",
        encoding="utf-8",
    )
    assert main(["apply", str(batch), "--vault", str(vault), "--json"]) == 2
    assert "batch summary must be non-empty text" in payload(capsys)["error"]


def test_cli_init_does_not_resolve_away_symlink_root(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    actual = tmp_path / "actual"
    actual.mkdir()
    link = tmp_path / "vault-link"
    link.symlink_to(actual, target_is_directory=True)

    assert main(["init", "--vault", str(link), "--json"]) == 2
    assert "real directory" in payload(capsys)["error"]
    assert not (actual / ".git").exists()


def test_concurrent_managed_writes_serialize(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    initialize_vault(vault)
    config = load_config(vault / "system/memory.yaml")
    view = discover_vault(vault)

    def create(number: int) -> str:
        return (
            apply_operations(
                view,
                config,
                [
                    {
                        "action": "create",
                        "type": "Project",
                        "scope": "personal",
                        "title": f"Concurrent {number}",
                        "description": "A serialized write.",
                        "slug": f"concurrent-{number}",
                        "body": f"# Concurrent {number}\n\nBody.\n",
                    }
                ],
                context=CONTEXT,
                summary=f"Create concurrent {number}",
            ).transaction.commit_hash
            or ""
        )

    with ThreadPoolExecutor(max_workers=4) as executor:
        commits = list(executor.map(create, range(4)))
    assert all(commits)
    assert len(set(commits)) == 4
    assert not git(vault, "status", "--short")


def test_composed_batch_renames_latest_overlay_and_preserves_link_titles(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    initialize_vault(vault)
    config = load_config(vault / "system/memory.yaml")
    view = discover_vault(vault)
    operations = [
        {
            "action": "create",
            "type": "Project",
            "scope": "personal",
            "title": "Target",
            "description": "Rename target.",
            "slug": "target",
            "body": "# Target\n\nOriginal.\n",
        },
        {
            "action": "create",
            "type": "Note",
            "scope": "personal",
            "title": "Linker",
            "description": "Links to the target.",
            "slug": "linker",
            "content_owner": "agent",
            "body": '# Linker\n\n[Target](target.md#part "Optional title") and [[target]].\n',
        },
        {"action": "rename", "id": "target", "new_slug": "middle-target"},
        {
            "action": "update",
            "id": "middle-target",
            "body": "# Target\n\nUpdated after rename.\n",
        },
        {"action": "rename", "id": "middle-target", "new_slug": "final-target"},
    ]

    apply_operations(view, config, operations, context=CONTEXT, summary="Compose renames")

    linker = (vault / "memory/concepts/linker.md").read_text()
    assert '(final-target.md#part "Optional title")' in linker
    assert "[[final-target]]" in linker
    assert "Updated after rename" in (vault / "memory/concepts/final-target.md").read_text()
    assert not (vault / "memory/concepts/target.md").exists()
    assert not (vault / "memory/concepts/middle-target.md").exists()


def test_untracked_concept_is_not_absorbed_into_index(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    initialize_vault(vault)
    config = load_config(vault / "system/memory.yaml")
    view = discover_vault(vault)
    untracked = vault / "memory/concepts/untracked.md"
    untracked.write_text(
        """---
type: Project
title: Untracked Human Work
description: Must stay outside generated artifacts.
scope: personal
created: {by: process:test, at: 2026-01-01T00:00:00Z}
generated: {by: process:test, at: 2026-01-01T00:00:00Z}
---
# Untracked

Do not absorb.
"""
    )

    apply_operations(
        view,
        config,
        [
            {
                "action": "create",
                "type": "Project",
                "scope": "personal",
                "title": "Managed",
                "description": "Managed concept.",
                "slug": "managed",
                "body": "# Managed\n\nBody.\n",
            }
        ],
        context=CONTEXT,
        summary="Create managed only",
    )

    assert "Untracked Human Work" not in (vault / "memory/concepts/index.md").read_text()
    assert untracked.exists()
    assert git(vault, "status", "--short") == "?? memory/concepts/untracked.md\n"
