from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

import agent_memory.transactions as transactions
from agent_memory.cli import main
from agent_memory.config import load_config
from agent_memory.initialization import initialize_vault
from agent_memory.mutations import MutationContext, apply_operations
from agent_memory.transactions import (
    InjectedCrash,
    TransactionError,
    apply_recovery,
    recovery_plan,
)
from agent_memory.vault import discover_vault

CONTEXT = MutationContext("process:test")


def git(vault: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(vault), *args],
        check=True,
        capture_output=True,
        text=True,
    ).stdout


def setup_concepts(tmp_path: Path) -> tuple[Path, dict, object, str, str]:
    vault = tmp_path / "vault"
    initialize_vault(vault)
    config = load_config(vault / "system/memory.yaml")
    view = discover_vault(vault)
    apply_operations(
        view,
        config,
        [
            {
                "action": "create",
                "type": "Project",
                "scope": "personal",
                "title": "Recoverable",
                "description": "A recoverable concept.",
                "slug": "recoverable",
                "body": "# Recoverable\n\nOriginal user content.\n",
            },
            {
                "action": "create",
                "type": "Project",
                "scope": "personal",
                "title": "Companion",
                "description": "A companion concept.",
                "slug": "companion",
                "body": "# Companion\n\nCompanion user content.\n",
            },
        ],
        context=CONTEXT,
        summary="Create recovery fixture",
    )
    original = (vault / "memory/concepts/recoverable.md").read_text()
    companion = (vault / "memory/concepts/companion.md").read_text()
    return vault, config, view, original, companion


PRECOMMIT_BOUNDARIES = [
    "after_prepare",
    "before_replace:memory/concepts/companion.md",
    "after_replace:memory/concepts/companion.md",
    "before_replace:memory/concepts/index.md",
    "after_replace:memory/concepts/index.md",
    "before_replace:memory/concepts/recoverable.md",
    "after_replace:memory/concepts/recoverable.md",
    "before_replace:memory/log.md",
    "after_replace:memory/log.md",
    "before_stage",
    "after_stage",
    "before_commit",
    "after_committing_journal",
]


@pytest.mark.parametrize("boundary", PRECOMMIT_BOUNDARIES)
def test_each_multiconcept_fault_has_preview_first_lossless_rollback(
    tmp_path: Path, boundary: str
) -> None:
    vault, config, view, original, companion = setup_concepts(tmp_path)
    old_head = git(vault, "rev-parse", "HEAD").strip()

    def crash(point: str) -> None:
        if point == boundary:
            raise InjectedCrash(point)

    with pytest.raises(InjectedCrash):
        apply_operations(
            view,
            config,
            [
                {
                    "action": "update",
                    "id": "recoverable",
                    "title": "Recovered Title",
                    "body": "# New\n\nNew body.\n",
                },
                {
                    "action": "update",
                    "id": "companion",
                    "body": "# Companion\n\nNew companion body.\n",
                },
            ],
            context=CONTEXT,
            summary="Faulted batch update",
            fault_hook=crash,
        )

    state = Path(config["transactions"]["state_dir"])
    transaction_id = next(
        path.parent.name
        for path in state.glob("*/journal.json")
        if '"phase":"complete"' not in path.read_text()
    )
    preview = recovery_plan(state, vault, transaction_id)
    assert preview["action"] == "rollback"
    assert git(vault, "rev-parse", "HEAD").strip() == old_head
    with pytest.raises(TransactionError, match="incomplete"):
        apply_operations(
            view,
            config,
            [{"action": "update", "id": "recoverable", "body": "# Other\n\nBody.\n"}],
            context=CONTEXT,
            summary="Blocked",
        )

    applied = apply_recovery(state, vault, transaction_id)
    assert applied["phase"] == "rolled_back"
    assert (vault / "memory/concepts/recoverable.md").read_text() == original
    assert (vault / "memory/concepts/companion.md").read_text() == companion
    assert not git(vault, "status", "--short")


def _faulted_transaction(
    tmp_path: Path, boundary: str = "after_stage"
) -> tuple[Path, dict, str, dict]:
    vault, config, view, _, _ = setup_concepts(tmp_path)

    def crash(point: str) -> None:
        if point == boundary:
            raise InjectedCrash(point)

    with pytest.raises(InjectedCrash):
        apply_operations(
            view,
            config,
            [
                {
                    "action": "update",
                    "id": "recoverable",
                    "body": "# Recoverable\n\nTransaction output.\n",
                }
            ],
            context=CONTEXT,
            summary="Faulted recovery safety",
            fault_hook=crash,
        )
    state = Path(config["transactions"]["state_dir"])
    phase = {
        "after_prepare": "prepared",
        "before_stage": "replaced",
        "after_stage": "staged",
        "after_committing_journal": "committing",
        "after_commit": "committing",
        "after_committed_journal": "committed",
    }[boundary]
    journal_path = next(
        path for path in state.glob("*/journal.json") if f'"phase":"{phase}"' in path.read_text()
    )
    return vault, config, journal_path.parent.name, json.loads(journal_path.read_text())


@pytest.mark.parametrize(
    ("boundary", "phase", "action"),
    [
        ("after_prepare", "prepared", "rollback"),
        ("before_stage", "replaced", "rollback"),
        ("after_stage", "staged", "rollback"),
        ("after_committing_journal", "committing", "rollback"),
        ("after_commit", "committing", "finalize"),
        ("after_committed_journal", "committed", "finalize"),
    ],
)
def test_doctor_diagnoses_incomplete_phases(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    boundary: str,
    phase: str,
    action: str,
) -> None:
    vault, _, transaction_id, _ = _faulted_transaction(tmp_path, boundary)
    assert main(["doctor", "--vault", str(vault), "--json"]) == 1
    report = json.loads(capsys.readouterr().out)
    transaction = next(
        item for item in report["transactions"] if item["transaction_id"] == transaction_id
    )
    assert transaction["phase"] == phase
    assert transaction["action"] == action


def test_corrupt_or_missing_backup_requires_manual_recovery(tmp_path: Path) -> None:
    vault, config, transaction_id, journal = _faulted_transaction(tmp_path)
    state = Path(config["transactions"]["state_dir"])
    backup_target = next(target for target in journal["targets"] if target["old_hash"] is not None)
    backup = state / transaction_id / backup_target["backup"]
    backup.write_text("corrupt")
    before = git(vault, "status", "--porcelain=v1")

    assert recovery_plan(state, vault, transaction_id)["action"] == "manual"
    with pytest.raises(TransactionError, match="ambiguous"):
        apply_recovery(state, vault, transaction_id)
    assert git(vault, "status", "--porcelain=v1") == before

    backup.unlink()
    assert recovery_plan(state, vault, transaction_id)["action"] == "manual"


def test_unknown_current_hash_is_detected_before_any_restore(tmp_path: Path) -> None:
    vault, config, transaction_id, _ = _faulted_transaction(tmp_path)
    state = Path(config["transactions"]["state_dir"])
    transaction_output = (vault / "memory/concepts/recoverable.md").read_text()
    log = vault / "memory/log.md"
    log.write_text(log.read_text() + "external winner\n")

    assert recovery_plan(state, vault, transaction_id)["action"] == "manual"
    with pytest.raises(TransactionError, match="ambiguous"):
        apply_recovery(state, vault, transaction_id)
    assert (vault / "memory/concepts/recoverable.md").read_text() == transaction_output
    assert log.read_text().endswith("external winner\n")


def test_unknown_staged_state_is_preserved(tmp_path: Path) -> None:
    vault, config, transaction_id, _ = _faulted_transaction(tmp_path, "before_stage")
    state = Path(config["transactions"]["state_dir"])
    outside = vault / "outside.txt"
    outside.write_text("user stage\n")
    git(vault, "add", "outside.txt")
    staged_before = git(vault, "diff", "--cached", "--name-only")
    target_before = (vault / "memory/concepts/recoverable.md").read_text()

    assert recovery_plan(state, vault, transaction_id)["action"] == "manual"
    with pytest.raises(TransactionError, match="ambiguous"):
        apply_recovery(state, vault, transaction_id)
    assert git(vault, "diff", "--cached", "--name-only") == staged_before
    assert (vault / "memory/concepts/recoverable.md").read_text() == target_before


def test_restore_failure_after_preview_preserves_staged_transaction_blobs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    vault, config, transaction_id, _ = _faulted_transaction(tmp_path, "after_stage")
    state = Path(config["transactions"]["state_dir"])
    staged_before = git(vault, "diff", "--cached", "--name-only")
    assert staged_before

    monkeypatch.setattr(transactions, "_restore", lambda *args, **kwargs: False)
    with pytest.raises(TransactionError, match="target or Git index changed"):
        apply_recovery(state, vault, transaction_id)

    assert git(vault, "diff", "--cached", "--name-only") == staged_before


def test_recovery_preserves_stage_added_during_restore(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    vault, config, transaction_id, _ = _faulted_transaction(tmp_path, "after_stage")
    state = Path(config["transactions"]["state_dir"])
    outside = vault / "outside.txt"
    restore = transactions._restore

    def race(*args, **kwargs) -> bool:
        restored = restore(*args, **kwargs)
        outside.write_text("user staged during restore\n")
        git(vault, "add", "outside.txt")
        return restored

    monkeypatch.setattr(transactions, "_restore", race)
    with pytest.raises(TransactionError, match="target or Git index changed"):
        apply_recovery(state, vault, transaction_id)

    staged = set(git(vault, "diff", "--cached", "--name-only").splitlines())
    assert "outside.txt" in staged
    assert git(vault, "show", ":outside.txt") == "user staged during restore\n"
    assert recovery_plan(state, vault, transaction_id)["action"] == "manual"


@pytest.mark.parametrize("boundary", ["after_commit", "after_committed_journal"])
def test_finalize_removes_fsynced_backups(tmp_path: Path, boundary: str) -> None:
    vault, config, transaction_id, _ = _faulted_transaction(tmp_path, boundary)
    state = Path(config["transactions"]["state_dir"])
    backups = state / transaction_id / "backups"
    assert backups.is_dir()

    assert apply_recovery(state, vault, transaction_id)["phase"] == "complete"
    assert not backups.exists()


def test_terminal_replay_cleans_stale_backups(tmp_path: Path) -> None:
    vault, config, view, _, _ = setup_concepts(tmp_path)
    result = apply_operations(
        view,
        config,
        [{"action": "update", "id": "recoverable", "body": "# R\n\nComplete.\n"}],
        context=CONTEXT,
        summary="Complete normally",
    )
    state = Path(config["transactions"]["state_dir"])
    transaction_dir = state / result.transaction.transaction_id
    backups = transaction_dir / "backups"
    backups.mkdir()
    (backups / "stale").write_text("stale")

    assert recovery_plan(state, vault, result.transaction.transaction_id)["action"] == "none"
    assert apply_recovery(state, vault, result.transaction.transaction_id)["action"] == "none"
    assert not backups.exists()


def test_multiconcept_commit_before_journal_completion_finalizes(tmp_path: Path) -> None:
    vault, config, view, _, _ = setup_concepts(tmp_path)

    def crash(point: str) -> None:
        if point == "after_commit":
            raise InjectedCrash(point)

    with pytest.raises(InjectedCrash):
        apply_operations(
            view,
            config,
            [
                {"action": "update", "id": "recoverable", "body": "# R\n\nCommitted.\n"},
                {"action": "update", "id": "companion", "body": "# C\n\nCommitted.\n"},
            ],
            context=CONTEXT,
            summary="Committed multi-concept crash",
            fault_hook=crash,
        )
    state = Path(config["transactions"]["state_dir"])
    transaction_id = next(
        path.parent.name
        for path in state.glob("*/journal.json")
        if '"phase":"committing"' in path.read_text()
    )
    assert recovery_plan(state, vault, transaction_id)["action"] == "finalize"
    assert apply_recovery(state, vault, transaction_id)["phase"] == "complete"
    assert "Committed" in (vault / "memory/concepts/recoverable.md").read_text()
    assert "Committed" in (vault / "memory/concepts/companion.md").read_text()


def test_commit_before_journal_completion_recovers_as_finalize(tmp_path: Path) -> None:
    vault, config, view, original, _ = setup_concepts(tmp_path)

    def crash(point: str) -> None:
        if point == "after_commit":
            raise InjectedCrash(point)

    with pytest.raises(InjectedCrash):
        apply_operations(
            view,
            config,
            [{"action": "update", "id": "recoverable", "body": "# New\n\nCommitted body.\n"}],
            context=CONTEXT,
            summary="Committed then crashed",
            fault_hook=crash,
        )
    assert (vault / "memory/concepts/recoverable.md").read_text() != original
    assert not git(vault, "status", "--short")

    state = Path(config["transactions"]["state_dir"])
    transaction_id = next(
        path.parent.name
        for path in state.glob("*/journal.json")
        if '"phase":"committing"' in path.read_text()
    )
    assert recovery_plan(state, vault, transaction_id)["action"] == "finalize"
    assert apply_recovery(state, vault, transaction_id)["phase"] == "complete"
