from __future__ import annotations

import fcntl
import json
import os
import subprocess
from pathlib import Path

import pytest
from ruamel.yaml import YAML

from agent_memory.initialization import initialize_vault
from agent_memory.locking import LockTimeoutError, writer_lock
from agent_memory.secrets import SecretError, reject_secret_content, reject_secret_path
from agent_memory.vault import discover_vault, validate_vault


def test_init_is_idempotent_non_destructive_and_has_exact_views(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    first = initialize_vault(vault)
    assert validate_vault(discover_vault(vault)) == ()
    root_index = vault / "memory/index.md"
    root_index.write_text("user content\n", encoding="utf-8")

    second = initialize_vault(vault)

    assert first
    assert second == ()
    assert root_index.read_text(encoding="utf-8") == "user content\n"
    base = YAML(typ="safe").load((vault / "memory/memories.base").read_text())
    assert [view["name"] for view in base["views"]] == [
        "Work",
        "Projects",
        "People",
        "Preferences",
        "Procedures",
        "Notes",
        "Tasks",
        "Decisions",
        "References",
    ]
    assert [view["filters"]["and"][0] for view in base["views"]] == [
        'scope == "work"',
        'type == "Project"',
        'type == "Person"',
        'type == "Preference"',
        'type == "Procedure"',
        'type == "Note"',
        'type == "Task"',
        'type == "Decision"',
        'type == "Reference"',
    ]


def test_lock_timeout_reports_live_owner_and_stale_metadata_is_replaced(tmp_path: Path) -> None:
    path = tmp_path / "writer.lock"
    path.write_text(json.dumps({"pid": 999_999, "command": "dead"}), encoding="utf-8")
    with writer_lock(path, timeout=0, command="first", actor="process:test"):
        assert json.loads(path.read_text())["command"] == "first"
        with path.open("a+") as competing:
            with pytest.raises(BlockingIOError):
                fcntl.flock(competing.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(LockTimeoutError, match=r"owner \(live\).+first"):
            with writer_lock(path, timeout=0, command="second", actor="process:test"):
                pass


@pytest.mark.parametrize(
    "path",
    [".env", "memory/auth.json", "memory/concepts/api-token.md", "system/state.db"],
)
def test_secret_filenames_are_rejected(path: str) -> None:
    with pytest.raises(SecretError):
        reject_secret_path(path)


@pytest.mark.parametrize(
    "text",
    [
        "-----BEGIN PRIVATE KEY-----\nabc",
        "api_key = abcdefghijklmnop",
        "Authorization: Bearer abcdefghijklmnop",
        "token: xoxb-1234567890-abcdefghij",
    ],
)
def test_explicit_secret_content_forms_are_rejected(text: str) -> None:
    with pytest.raises(SecretError):
        reject_secret_content(text, path="memory/concepts/example.md")


def test_ordinary_prose_is_not_rejected() -> None:
    for path in (
        "memory/concepts/oauth-procedure.md",
        "memory/concepts/monkey-behavior.md",
        "memory/concepts/authoring-guide.md",
    ):
        reject_secret_path(path)
    for text in (
        "# OAuth procedure\n\nStore API keys outside this vault; do not paste tokens here.",
        "api_key = ${MEMORY_API_KEY}",
        '"client_secret": "REDACTED"',
        'password = os.environ["PASSWORD"]',
    ):
        reject_secret_content(text, path="memory/concepts/oauth-procedure.md")


@pytest.mark.parametrize(
    "path",
    [
        "memory/passwords.md",
        "memory/session-cookie.md",
        "memory/credentials.json",
        "memory/private-key.pem",
    ],
)
def test_delimiter_aware_secret_paths(path: str) -> None:
    with pytest.raises(SecretError):
        reject_secret_path(path)


@pytest.mark.parametrize(
    "text",
    [
        "export PASSWORD=hunter-two",
        '"client_secret": "literal-secret-value"',
        "access_key: AKIA1234567890ABCDEF",
    ],
)
def test_additional_literal_secret_forms(text: str) -> None:
    with pytest.raises(SecretError):
        reject_secret_content(text, path="memory/concepts/example.md")


def test_lock_refuses_symlink(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.write_text("do not truncate")
    link = tmp_path / "writer.lock"
    link.symlink_to(target)
    with pytest.raises(OSError):
        with writer_lock(link, timeout=0, command="unsafe", actor="process:test"):
            pass
    assert target.read_text() == "do not truncate"


def test_init_completes_existing_repository_in_one_exact_path_commit(tmp_path: Path) -> None:
    vault = tmp_path / "existing-partial"
    vault.mkdir()
    subprocess.run(
        ["git", "-C", str(vault), "init", "-b", "memory"], check=True, capture_output=True
    )
    readme = vault / "README.md"
    readme.write_text("user-owned readme\n")
    env = os.environ.copy()
    env.update(
        {
            "GIT_AUTHOR_NAME": "Test",
            "GIT_AUTHOR_EMAIL": "test@example.invalid",
            "GIT_COMMITTER_NAME": "Test",
            "GIT_COMMITTER_EMAIL": "test@example.invalid",
        }
    )
    subprocess.run(["git", "-C", str(vault), "add", "README.md"], check=True, env=env)
    subprocess.run(
        ["git", "-C", str(vault), "commit", "-m", "partial"],
        check=True,
        env=env,
        capture_output=True,
    )
    state = tmp_path / "custom-state"

    created = initialize_vault(vault, state_dir=state, branch="memory")

    assert "README.md" not in created
    assert readme.read_text() == "user-owned readme\n"
    assert (
        subprocess.run(
            ["git", "-C", str(vault), "rev-list", "--count", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        == "2"
    )
    changed = subprocess.run(
        ["git", "-C", str(vault), "show", "--pretty=", "--name-only", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.splitlines()
    assert set(changed) == set(created)
    assert str(state) in (vault / "system/memory.yaml").read_text()
    assert initialize_vault(vault, state_dir=state, branch="memory") == ()


def test_init_rejects_symlink_root_and_existing_staged_state(tmp_path: Path) -> None:
    actual = tmp_path / "actual"
    actual.mkdir()
    link = tmp_path / "vault-link"
    link.symlink_to(actual, target_is_directory=True)
    with pytest.raises(ValueError, match="real directory"):
        initialize_vault(link)
    assert not (actual / ".git").exists()

    escaped = tmp_path / "escaped"
    escaped.mkdir()
    nested = tmp_path / "nested-link-vault"
    nested.mkdir()
    (nested / "memory").symlink_to(escaped, target_is_directory=True)
    with pytest.raises(ValueError, match="unsafe initialization parent"):
        initialize_vault(nested)
    assert not any(escaped.iterdir())
    assert not (nested / ".git").exists()

    vault = tmp_path / "existing"
    vault.mkdir()
    subprocess.run(["git", "-C", str(vault), "init", "-b", "main"], check=True, capture_output=True)
    tracked = vault / "existing.txt"
    tracked.write_text("existing\n")
    env = os.environ.copy()
    env.update(
        {
            "GIT_AUTHOR_NAME": "Test",
            "GIT_AUTHOR_EMAIL": "test@example.invalid",
            "GIT_COMMITTER_NAME": "Test",
            "GIT_COMMITTER_EMAIL": "test@example.invalid",
        }
    )
    subprocess.run(["git", "-C", str(vault), "add", "existing.txt"], check=True, env=env)
    subprocess.run(
        ["git", "-C", str(vault), "commit", "-m", "initial"],
        check=True,
        env=env,
        capture_output=True,
    )
    staged = vault / "staged.txt"
    staged.write_text("staged\n")
    subprocess.run(["git", "-C", str(vault), "add", "staged.txt"], check=True)

    with pytest.raises(ValueError, match="pre-existing staged"):
        initialize_vault(vault)
    assert not (vault / "memory").exists()
    assert staged.read_text() == "staged\n"
