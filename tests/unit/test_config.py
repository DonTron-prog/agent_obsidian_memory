from pathlib import Path

import pytest

from agent_memory.config import ConfigError, load_config


def test_loads_defaults_and_normalizes_paths() -> None:
    config = load_config()

    assert config["version"] == 1
    assert Path(config["vault"]).is_absolute()
    assert config["notifications"]["errors_file"] == str(Path(config["vault"]) / "system/errors.md")
    assert Path(config["worker"]["state_dir"]).is_absolute()
    assert config["worker"]["publish_timeout_ms"] == 250


def test_deep_merges_and_preserves_unknown_config(tmp_path: Path) -> None:
    path = tmp_path / "system" / "memory.yaml"
    path.parent.mkdir()
    path.write_text(
        """\
vault: ../vault
limits:
  concept_words: 42
  producer_option: kept
worker:
  state_dir: ../state
unknown_section:
  enabled: true
""",
        encoding="utf-8",
    )

    config = load_config(path)

    assert config["limits"] == {"concept_words": 42, "producer_option": "kept"}
    assert config["locking"]["timeout_seconds"] == 10
    assert config["unknown_section"] == {"enabled": True}
    assert config["vault"] == str((path.parent / "../vault").resolve())
    assert config["worker"]["state_dir"] == str((path.parent / "../state").resolve())
    assert config["notifications"]["errors_file"] == str(
        (path.parent / "../vault/system/errors.md").resolve()
    )


def test_worker_state_rejects_vault_and_git_worktree_but_allows_temp_sibling(
    tmp_path: Path,
) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / ".git").mkdir()
    config_path = tmp_path / "memory.yaml"
    config_path.write_text(
        f"vault: {vault}\nworker:\n  state_dir: {vault}/state\n", encoding="utf-8"
    )
    with pytest.raises(ConfigError, match="synchronized vault"):
        load_config(config_path)

    worktree_state = vault / "../vault/runtime"
    config_path.write_text(
        f"vault: {tmp_path / 'other-vault'}\nworker:\n  state_dir: {worktree_state}\n",
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match="Git worktree"):
        load_config(config_path)

    config_path.write_text(
        f"vault: {vault}\nworker:\n  state_dir: {tmp_path / 'state'}\n", encoding="utf-8"
    )
    assert load_config(config_path)["worker"]["state_dir"] == str(tmp_path / "state")


@pytest.mark.parametrize("value", (True, -1, "fast"))
def test_rejects_invalid_publish_timeout(tmp_path: Path, value: object) -> None:
    path = tmp_path / "memory.yaml"
    path.write_text(f"worker:\n  publish_timeout_ms: {str(value).lower()}\n", encoding="utf-8")

    with pytest.raises(ConfigError, match="publish_timeout_ms"):
        load_config(path)


def test_rejects_non_mapping_config(tmp_path: Path) -> None:
    path = tmp_path / "memory.yaml"
    path.write_text("- no\n", encoding="utf-8")

    with pytest.raises(ConfigError, match="root must be a mapping"):
        load_config(path)
