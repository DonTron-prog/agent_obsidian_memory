"""Small argv-safe Git boundary for managed vault transactions."""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path


class GitError(ValueError):
    """Raised when Git state is unsafe for a managed write."""


@dataclass(frozen=True)
class GitStatus:
    staged: tuple[str, ...]
    dirty: tuple[str, ...]


def _run(
    root: Path,
    args: list[str],
    *,
    check: bool = True,
    text: bool = False,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[bytes] | subprocess.CompletedProcess[str]:
    clean_env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    if env:
        clean_env.update(env)
    try:
        return subprocess.run(
            ["git", "-C", str(root), *args],
            check=check,
            capture_output=True,
            text=text,
            env=clean_env,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        message = getattr(exc, "stderr", None)
        if isinstance(message, bytes):
            message = message.decode("utf-8", "replace")
        raise GitError((message or str(exc)).strip()) from exc


def init_repository(root: Path, branch: str) -> None:
    _run(root, ["init", "-b", branch])


def ensure_repository(root: Path, branch: str) -> None:
    result = _run(root, ["rev-parse", "--show-toplevel"], check=False, text=True)
    if result.returncode != 0:
        raise GitError(f"vault is not a Git repository: {root}")
    if Path(result.stdout.strip()).resolve() != root.resolve():
        raise GitError("configured vault must be the Git repository root")
    current = _run(root, ["symbolic-ref", "--quiet", "--short", "HEAD"], text=True).stdout.strip()
    if current != branch:
        raise GitError(f"managed writes require branch {branch!r}; current branch is {current!r}")


def head(root: Path) -> str | None:
    result = _run(root, ["rev-parse", "--verify", "HEAD"], check=False, text=True)
    return result.stdout.strip() if result.returncode == 0 else None


def _names(root: Path, args: list[str]) -> tuple[str, ...]:
    output = _run(root, args).stdout
    return tuple(item.decode("utf-8", "surrogateescape") for item in output.split(b"\0") if item)


def staged_paths(root: Path) -> tuple[str, ...]:
    return _names(
        root,
        [
            "diff",
            "--cached",
            "--name-only",
            "--no-renames",
            "--ita-visible-in-index",
            "-z",
            "--diff-filter=ACDMRTUXB",
        ],
    )


def dirty_paths(root: Path, paths: tuple[str, ...]) -> tuple[str, ...]:
    """Return exact transaction paths with tracked or untracked working-tree changes."""

    dirty: list[str] = []
    for path in paths:
        literal = f":(literal){path}"
        output = _run(
            root,
            ["status", "--porcelain=v1", "-z", "--untracked-files=all", "--", literal],
        ).stdout
        if output:
            dirty.append(path)
    return tuple(dirty)


def stage_paths(root: Path, paths: tuple[str, ...]) -> None:
    if paths:
        _run(root, ["add", "-A", "--", *(f":(literal){path}" for path in paths)])


def unstage_paths(root: Path, paths: tuple[str, ...]) -> None:
    if not paths:
        return
    if head(root) is None:
        _run(
            root,
            [
                "rm",
                "--cached",
                "--ignore-unmatch",
                "-r",
                "--",
                *(f":(literal){path}" for path in paths),
            ],
        )
    else:
        _run(root, ["restore", "--staged", "--", *(f":(literal){path}" for path in paths)])


def commit(
    root: Path,
    *,
    subject: str,
    body: str,
    paths: tuple[str, ...],
) -> str:
    env = {
        "GIT_AUTHOR_NAME": "Agent Memory",
        "GIT_AUTHOR_EMAIL": "memory@localhost",
        "GIT_COMMITTER_NAME": "Agent Memory",
        "GIT_COMMITTER_EMAIL": "memory@localhost",
    }
    _run(
        root,
        [
            "-c",
            "core.hooksPath=/dev/null",
            "commit",
            "--no-gpg-sign",
            "--only",
            "-m",
            subject,
            "-m",
            body,
            "--",
            *(f":(literal){path}" for path in paths),
        ],
        env=env,
    )
    value = head(root)
    if value is None:  # pragma: no cover - commit guarantees this
        raise GitError("Git commit completed without a HEAD")
    return value


def index_file(root: Path, path: str) -> bytes | None:
    result = _run(root, ["show", f":./{path}"], check=False)
    return result.stdout if result.returncode == 0 else None


def committed_paths(root: Path, revision: str = "HEAD") -> tuple[str, ...]:
    return _names(
        root,
        [
            "diff-tree",
            "--root",
            "--no-commit-id",
            "--name-only",
            "--no-renames",
            "-r",
            "-z",
            revision,
        ],
    )


def commit_parent(root: Path, revision: str = "HEAD") -> str | None:
    result = _run(root, ["rev-parse", f"{revision}^"], check=False, text=True)
    return result.stdout.strip() if result.returncode == 0 else None


def commit_message(root: Path, revision: str = "HEAD") -> str:
    return _run(root, ["show", "-s", "--format=%B", revision], text=True).stdout


def show_file(root: Path, path: str) -> bytes | None:
    result = _run(root, ["show", f"HEAD:{path}"], check=False)
    return result.stdout if result.returncode == 0 else None


def head_concept_paths(root: Path) -> tuple[str, ...]:
    if head(root) is None:
        return ()
    return _names(root, ["ls-tree", "-r", "--name-only", "-z", "HEAD", "--", "memory/concepts"])
