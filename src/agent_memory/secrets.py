"""Conservative explicit secret checks for managed filenames and text."""

from __future__ import annotations

import re
from pathlib import PurePosixPath


class SecretError(ValueError):
    """Raised before filesystem or Git mutation when secret-like data is found."""


_PRIVATE_KEY = re.compile(r"-----BEGIN (?:[A-Z0-9 ]+ )?PRIVATE KEY-----")
_PROVIDER_PREFIX = re.compile(
    r"\b(?:"
    r"sk-(?:live-|test-|ant-)?[A-Za-z0-9_-]{16,}|"
    r"gh[pousr]_[A-Za-z0-9_]{20,}|"
    r"xox[baprs]-[A-Za-z0-9-]{10,}|"
    r"AKIA[0-9A-Z]{16}|"
    r"AIza[0-9A-Za-z_-]{20,}"
    r")\b"
)
_ASSIGNMENT = re.compile(
    r"(?im)^\s*(?:export\s+)?(?:"
    r"authorization\s*:\s*bearer\s+(?P<bearer>[^\r\n]+)|"
    r"[\"']?(?:password|passwd|api[_-]?key|access[_-]?token|refresh[_-]?token|"
    r"bot[_-]?token|oauth[_-]?(?:token|secret)|client[_-]?secret|secret)"
    r"[\"']?\s*[:=]\s*(?P<value>[^\r\n]+)"
    r")$"
)
_INLINE_ASSIGNMENT = re.compile(
    r"(?i)(?:authorization\s*:\s*bearer\s+|"
    r"(?:password|passwd|api[_-]?key|access[_-]?token|refresh[_-]?token|"
    r"bot[_-]?token|oauth[_-]?(?:token|secret)|client[_-]?secret|secret)"
    r"\s*[:=]\s*)([^\s,;]+)"
)
_PLACEHOLDER = re.compile(
    r"(?i)^(?:"
    r"\$|<|\{|os\.environ|os\.getenv|env\.|process\.env|settings\.|config\.|"
    r"redacted\b|change[_-]?me\b|placeholder\b|example\b|dummy\b|x{3,}\b"
    r")"
)


def _secret_component(component: str) -> bool:
    lowered = component.casefold()
    if lowered == ".env" or lowered.startswith(".env."):
        return True
    if lowered in {"auth.json", "authentication.json", "state.db"} or lowered.startswith(
        "state.db."
    ):
        return True
    stem = lowered.rsplit(".", 1)[0]
    words = tuple(word for word in re.split(r"[-_.]+", stem) if word)
    if any(
        word
        in {
            "password",
            "passwords",
            "passwd",
            "credential",
            "credentials",
            "cookie",
            "cookies",
            "secret",
            "secrets",
            "token",
            "tokens",
        }
        for word in words
    ):
        return True
    pairs = set(zip(words, words[1:]))
    return bool(
        pairs
        & {
            ("api", "key"),
            ("access", "key"),
            ("private", "key"),
            ("client", "secret"),
            ("refresh", "token"),
            ("access", "token"),
            ("bot", "token"),
            ("oauth", "secret"),
        }
    ) or stem in {"id_rsa", "id_dsa", "id_ecdsa", "id_ed25519"}


def reject_secret_path(path: str) -> None:
    if any(_secret_component(component) for component in PurePosixPath(path).parts):
        raise SecretError(f"secret-bearing managed filename is not allowed: {path}")


def _literal_secret(value: str) -> bool:
    candidate = value.strip().strip("\"'").strip()
    return len(candidate) >= 6 and not _PLACEHOLDER.match(candidate)


def reject_secret_content(text: str, *, path: str) -> None:
    if _PRIVATE_KEY.search(text) or _PROVIDER_PREFIX.search(text):
        raise SecretError(f"secret-bearing content is not allowed: {path}")
    for match in _ASSIGNMENT.finditer(text):
        if _literal_secret(match.group("bearer") or match.group("value") or ""):
            raise SecretError(f"secret-bearing content is not allowed: {path}")


def contains_secret(text: str) -> bool:
    """Return whether text would be rejected without exposing the matching value."""

    try:
        reject_secret_content(text, path="content")
    except SecretError:
        return True
    return any(_literal_secret(match.group(1)) for match in _INLINE_ASSIGNMENT.finditer(text))


def redact_sensitive_text(text: object, *, limit: int = 240) -> str:
    """Produce concise diagnostic text without retaining secret-bearing input."""

    value = str(text).replace("\r", " ").replace("\n", " ")
    if contains_secret(value):
        return "[redacted sensitive content]"
    return value[:limit] + ("…" if len(value) > limit else "")
