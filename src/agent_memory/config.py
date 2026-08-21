"""Configuration loading and path normalization."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

from ruamel.yaml import YAML

DEFAULT_CONFIG: dict[str, Any] = {
    "version": 1,
    "vault": "/home/donald/agent-memory",
    "identity": {"human": "human:donald"},
    "limits": {"concept_words": 600},
    "locking": {"timeout_seconds": 10},
    "search": {"default_limit": 10},
    "transactions": {"state_dir": "/home/donald/.agent-memory-txn"},
    "git": {"branch": "main"},
    "syncthing": {"folder_id": "agent-memory"},
    "worker": {
        "state_dir": "~/.local/state/agent-memory/lifecycle",
        "publish_timeout_ms": 250,
    },
    "notifications": {
        "pi_tui": True,
        "hermes_origin": True,
        "telegram_owner_dm": True,
        "errors_file": "system/errors.md",
    },
}


class ConfigError(ValueError):
    """Raised for an invalid configuration document."""


def _merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            base[key] = _merge(base[key], value)
        else:
            base[key] = deepcopy(value)
    return base


def _resolved_path(value: object, base: Path) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ConfigError("configured paths must be non-empty strings")
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = base / path
    return str(path.resolve(strict=False))


def load_config(path: str | Path | None = None) -> dict[str, Any]:
    """Load defaults and deep-merge a configuration file.

    Relative vault and state paths are based on the configuration file directory.
    The notification error file is relative to the resolved vault.
    """

    config = deepcopy(DEFAULT_CONFIG)
    base = Path.cwd()
    if path is not None:
        config_path = Path(path).expanduser().resolve(strict=False)
        base = config_path.parent
        yaml = YAML(typ="safe", pure=True)
        yaml.allow_duplicate_keys = False
        try:
            loaded = yaml.load(config_path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise ConfigError(f"cannot load configuration: {exc}") from exc
        if loaded is None:
            loaded = {}
        if not isinstance(loaded, dict):
            raise ConfigError("configuration root must be a mapping")
        config = _merge(config, loaded)

    config["vault"] = _resolved_path(config["vault"], base)
    for section in ("transactions", "worker"):
        value = config.get(section)
        if not isinstance(value, dict) or "state_dir" not in value:
            raise ConfigError(f"{section}.state_dir is required")
        value["state_dir"] = _resolved_path(value["state_dir"], base)

    notifications = config.get("notifications")
    if not isinstance(notifications, dict) or "errors_file" not in notifications:
        raise ConfigError("notifications.errors_file is required")
    notifications["errors_file"] = _resolved_path(
        notifications["errors_file"], Path(config["vault"])
    )
    return config
