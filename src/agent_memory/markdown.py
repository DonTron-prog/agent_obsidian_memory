"""Safe YAML-frontmatter parsing with round-trip metadata preservation."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from io import StringIO
from typing import Any

from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap


class FrontmatterError(ValueError):
    """Raised for malformed or unsafe frontmatter."""


@dataclass
class FrontmatterDocument:
    metadata: CommentedMap
    body: str


def _yaml() -> YAML:
    yaml = YAML(typ="rt", pure=True)
    yaml.allow_duplicate_keys = False
    yaml.preserve_quotes = True
    yaml.width = 4096
    return yaml


def _reject_unsafe_nodes(value: Any) -> None:
    tag = getattr(getattr(value, "tag", None), "value", None)
    if tag:
        raise FrontmatterError(f"custom YAML tag is not allowed: {tag}")
    if isinstance(value, Mapping):
        for key, item in value.items():
            _reject_unsafe_nodes(key)
            _reject_unsafe_nodes(item)
    elif isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        for item in value:
            _reject_unsafe_nodes(item)
    elif value is not None and not isinstance(value, str | int | float | bool | date | datetime):
        raise FrontmatterError(f"unsupported YAML value: {type(value).__name__}")


def parse_frontmatter(text: str) -> FrontmatterDocument:
    """Parse a Markdown document whose first line is ``---``.

    The returned body is byte-for-byte equal to the text after the closing delimiter.
    """

    lines = text.splitlines(keepends=True)
    if not lines or lines[0].rstrip("\r\n") != "---":
        raise FrontmatterError("document must begin with YAML frontmatter")

    closing = next(
        (index for index, line in enumerate(lines[1:], start=1) if line.rstrip("\r\n") == "---"),
        None,
    )
    if closing is None:
        raise FrontmatterError("frontmatter closing delimiter is missing")

    try:
        metadata = _yaml().load("".join(lines[1:closing]))
    except Exception as exc:
        raise FrontmatterError(f"invalid YAML frontmatter: {exc}") from exc
    if not isinstance(metadata, CommentedMap):
        raise FrontmatterError("frontmatter must be a mapping")
    _reject_unsafe_nodes(metadata)
    return FrontmatterDocument(metadata=metadata, body="".join(lines[closing + 1 :]))


def render_frontmatter(document: FrontmatterDocument) -> str:
    """Render frontmatter while leaving the Markdown body untouched."""

    _reject_unsafe_nodes(document.metadata)
    stream = StringIO()
    _yaml().dump(document.metadata, stream)
    yaml_text = stream.getvalue()
    if yaml_text and not yaml_text.endswith("\n"):
        yaml_text += "\n"
    return f"---\n{yaml_text}---\n{document.body}"
