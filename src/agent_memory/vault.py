"""Read-only discovery and validation of a Markdown memory vault."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote, urlsplit

from agent_memory.markdown import FrontmatterDocument, FrontmatterError, parse_frontmatter
from agent_memory.models import validate_slug
from agent_memory.validation import (
    DEFAULT_TYPES,
    ValidationIssue,
    is_reserved_okf_path,
    validate_base_okf,
    validate_local_profile,
)

MARKDOWN_LINK = re.compile(
    r"(?<!!)\[([^\]]*)\]\(\s*(<[^>\n]+>|[^\s)\n]+)"
    r"(?:\s+(\"[^\"\n]*\"|'[^'\n]*'|\([^()\n]*\)))?\s*\)"
)
WIKI_LINK = re.compile(r"\[\[([^\]|#]+)(?:#[^\]|]+)?(?:\|[^\]]+)?\]\]")


class VaultError(ValueError):
    """Raised when a vault cannot be read safely."""


@dataclass(frozen=True)
class IndexLink:
    label: str
    target: str


@dataclass(frozen=True)
class ConceptDocument:
    concept_id: str
    slug: str
    path: Path
    document: FrontmatterDocument
    text: str


@dataclass(frozen=True)
class VaultIssue:
    path: str
    level: str
    field: str
    message: str


@dataclass(frozen=True)
class Vault:
    root: Path
    bundle: Path
    root_index: Path
    concept_index: Path


def _inside(path: Path, root: Path) -> bool:
    try:
        path.resolve(strict=False).relative_to(root.resolve(strict=False))
    except ValueError:
        return False
    return True


def discover_vault(root: str | Path) -> Vault:
    """Resolve the configured vault root and its ``memory/`` OKF bundle."""

    vault_root = Path(root).expanduser().resolve(strict=False)
    bundle = vault_root / "memory"
    if not vault_root.is_dir():
        raise VaultError(f"vault does not exist: {vault_root}")
    if bundle.is_symlink() or not bundle.is_dir() or not _inside(bundle, vault_root):
        raise VaultError(f"memory bundle is missing or unsafe: {bundle}")
    root_index = bundle / "index.md"
    concept_directory = bundle / "concepts"
    concept_index = concept_directory / "index.md"
    if concept_directory.is_symlink() or not concept_directory.is_dir():
        raise VaultError(f"concept directory is missing or unsafe: {concept_directory}")
    return Vault(vault_root, bundle, root_index, concept_index)


def _safe_file(path: Path, root: Path) -> None:
    if path.is_symlink() or not path.is_file() or not _inside(path, root):
        raise VaultError(f"unsafe vault file: {path}")


def parse_index(path: Path, vault_root: Path) -> tuple[IndexLink, ...]:
    """Parse local Markdown links in source order with vault-relative targets."""

    _safe_file(path, vault_root)
    links: list[IndexLink] = []
    for label, raw_target, _title in MARKDOWN_LINK.findall(path.read_text(encoding="utf-8")):
        target = raw_target.strip().strip("<>").split("#", 1)[0]
        split = urlsplit(target)
        if not target or split.scheme or split.netloc:
            continue
        resolved = (path.parent / unquote(split.path)).resolve(strict=False)
        if not _inside(resolved, vault_root):
            raise VaultError(f"index link escapes vault: {raw_target}")
        links.append(IndexLink(label.strip(), resolved.relative_to(vault_root).as_posix()))
    return tuple(links)


def _direct_concept_slug(target: str) -> str | None:
    path = Path(target)
    if path.parent != Path("memory/concepts") or path.suffix != ".md" or path.name == "index.md":
        return None
    return path.stem


def indexed_concept_ids(vault: Vault) -> tuple[str, ...]:
    """Follow the root's concept-index link and return its concept IDs."""

    root_links = parse_index(vault.root_index, vault.root)
    expected = vault.concept_index.relative_to(vault.root).as_posix()
    if expected not in {link.target for link in root_links}:
        raise VaultError("root index does not link to memory/concepts/index.md")
    ids: list[str] = []
    for link in parse_index(vault.concept_index, vault.root):
        slug = _direct_concept_slug(link.target)
        if slug is not None:
            validate_slug(slug)
            ids.append(f"concepts/{slug}")
    return tuple(ids)


def scan_concepts(vault: Vault) -> tuple[ConceptDocument, ...]:
    """Read authoritative concepts directly from Markdown, never from an index."""

    directory = vault.bundle / "concepts"
    records: list[ConceptDocument] = []
    for path in sorted(directory.glob("*.md"), key=lambda item: item.name.casefold()):
        if path.name == "index.md":
            continue
        _safe_file(path, vault.root)
        try:
            slug = validate_slug(path.stem)
            text = path.read_text(encoding="utf-8")
            document = parse_frontmatter(text)
        except (OSError, UnicodeError, FrontmatterError, ValueError) as exc:
            raise VaultError(f"cannot read concept {path.name}: {exc}") from exc
        records.append(ConceptDocument(f"concepts/{slug}", slug, path, document, text))
    return tuple(records)


def markdown_targets(text: str) -> tuple[str, ...]:
    """Return local Markdown and wiki-link targets for matching and validation."""

    targets = [target.strip().strip("<>") for _, target, _title in MARKDOWN_LINK.findall(text)]
    targets.extend(target.strip() for target in WIKI_LINK.findall(text))
    return tuple(targets)


def _issue(path: str, issue: ValidationIssue) -> VaultIssue:
    return VaultIssue(path, issue.level, issue.field, issue.message)


def _relative_target(source: Path, target: str, vault: Vault) -> Path | None:
    value = target.split("#", 1)[0].strip().strip("<>")
    if not value:
        return None
    split = urlsplit(value)
    if split.scheme or split.netloc:
        return None
    value = unquote(split.path)
    if value.startswith("/"):
        return Path(value)
    candidate = (source.parent / value).resolve(strict=False)
    if candidate.is_dir():
        return candidate
    if not candidate.suffix:
        candidate = candidate.with_suffix(".md")
    return candidate.resolve(strict=False)


def validate_vault(
    vault: Vault,
    *,
    configured_types: set[str] | frozenset[str] = DEFAULT_TYPES,
    max_words: int = 600,
) -> tuple[VaultIssue, ...]:
    """Validate OKF/local policy, indexes, and local Markdown links."""

    issues: list[VaultIssue] = []
    concept_ids: set[str] = set()

    try:
        root_text = vault.root_index.read_text(encoding="utf-8")
        root_links = parse_index(vault.root_index, vault.root)
    except (OSError, UnicodeError, VaultError) as exc:
        issues.append(VaultIssue("memory/index.md", "error", "index", str(exc)))
        root_text, root_links = "", ()
    if root_text.startswith("---"):
        try:
            metadata = parse_frontmatter(root_text).metadata
            if str(metadata.get("okf_version", "")) != "0.2":
                issues.append(
                    VaultIssue(
                        "memory/index.md", "warning", "okf_version", "okf_version should be 0.2"
                    )
                )
        except FrontmatterError as exc:
            issues.append(VaultIssue("memory/index.md", "error", "frontmatter", str(exc)))
    elif root_text:
        issues.append(
            VaultIssue(
                "memory/index.md",
                "warning",
                "frontmatter",
                "root index should declare okf_version 0.2 frontmatter",
            )
        )

    expected_index = vault.concept_index.relative_to(vault.root).as_posix()
    if expected_index not in {link.target for link in root_links}:
        issues.append(
            VaultIssue(
                "memory/index.md",
                "warning",
                "index",
                "root index does not link to memory/concepts/index.md",
            )
        )

    try:
        concept_index_text = vault.concept_index.read_text(encoding="utf-8")
        concept_links = parse_index(vault.concept_index, vault.root)
        if concept_index_text.startswith("---"):
            issues.append(
                VaultIssue(
                    "memory/concepts/index.md",
                    "error",
                    "frontmatter",
                    "concept index must not contain frontmatter",
                )
            )
    except (OSError, UnicodeError, VaultError) as exc:
        issues.append(VaultIssue("memory/concepts/index.md", "error", "index", str(exc)))
        concept_links = ()

    for path in sorted(vault.bundle.rglob("*.md"), key=lambda item: item.as_posix().casefold()):
        relative_bundle = path.relative_to(vault.bundle).as_posix()
        relative_vault = path.relative_to(vault.root).as_posix()
        try:
            _safe_file(path, vault.root)
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError, VaultError) as exc:
            issues.append(VaultIssue(relative_vault, "error", "path", str(exc)))
            continue

        link_text = text
        if is_reserved_okf_path(relative_bundle):
            if path != vault.root_index and path != vault.concept_index and text.startswith("---"):
                issues.append(
                    VaultIssue(
                        relative_vault,
                        "error",
                        "frontmatter",
                        "reserved index and log files must not contain frontmatter",
                    )
                )
        else:
            try:
                document = parse_frontmatter(text)
            except FrontmatterError as exc:
                issues.append(VaultIssue(relative_vault, "error", "frontmatter", str(exc)))
                continue
            if path.parent == vault.bundle / "concepts":
                try:
                    slug = validate_slug(path.stem)
                    concept_ids.add(f"concepts/{slug}")
                except ValueError as exc:
                    issues.append(VaultIssue(relative_vault, "error", "id", str(exc)))
                result = validate_local_profile(
                    document, configured_types=configured_types, max_words=max_words
                )
            else:
                result = validate_base_okf(document)
            for item in result.issues:
                issues.append(_issue(relative_vault, item))
            link_text = document.body

        for raw_target in markdown_targets(link_text):
            target = _relative_target(path, raw_target, vault)
            if target is None:
                continue
            if not _inside(target, vault.root):
                issues.append(
                    VaultIssue(
                        relative_vault, "warning", "link", f"link escapes vault: {raw_target}"
                    )
                )
            elif not target.exists():
                issues.append(
                    VaultIssue(relative_vault, "warning", "link", f"broken link: {raw_target}")
                )

    indexed = {
        f"concepts/{slug}"
        for link in concept_links
        if (slug := _direct_concept_slug(link.target)) is not None
    }
    for missing in sorted(concept_ids - indexed):
        issues.append(
            VaultIssue(
                "memory/concepts/index.md", "warning", "index", f"missing concept entry: {missing}"
            )
        )
    for extra in sorted(indexed - concept_ids):
        issues.append(
            VaultIssue(
                "memory/concepts/index.md", "warning", "index", f"unknown concept entry: {extra}"
            )
        )
    return tuple(
        sorted(
            issues,
            key=lambda item: (
                item.path.casefold(),
                0 if item.level == "error" else 1,
                item.field,
                item.message,
            ),
        )
    )
