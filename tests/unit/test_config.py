from pathlib import Path

import pytest

from agent_memory.config import ConfigError, load_config


def test_loads_defaults_and_normalizes_paths() -> None:
    config = load_config()

    assert config["version"] == 1
    assert Path(config["vault"]).is_absolute()
    assert config["notifications"]["errors_file"] == str(Path(config["vault"]) / "system/errors.md")
    assert Path(config["worker"]["state_dir"]).is_absolute()


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


def test_rejects_non_mapping_config(tmp_path: Path) -> None:
    path = tmp_path / "memory.yaml"
    path.write_text("- no\n", encoding="utf-8")

    with pytest.raises(ConfigError, match="root must be a mapping"):
        load_config(path)
