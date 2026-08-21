"""Command-line entry point."""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Mapping, Sequence
from datetime import date, datetime
from pathlib import Path
from typing import Any

from ruamel.yaml import YAML

from agent_memory import __version__
from agent_memory.audit import AuditError, RetrievalContext, append_access_event
from agent_memory.config import ConfigError, load_config, validate_worker_state_dir
from agent_memory.git import ensure_repository, staged_paths
from agent_memory.initialization import initialize_vault
from agent_memory.lifecycle import build_descriptor, now_utc, publish_descriptor
from agent_memory.locking import writer_lock
from agent_memory.mutations import (
    MutationContext,
    MutationResult,
    apply_operations,
    rebuild_index,
    reconcile_concept,
)
from agent_memory.search import (
    SearchFilters,
    is_stale,
    resolve_concept,
    search_concepts,
    trust_tier,
)
from agent_memory.sessions import recover_incomplete
from agent_memory.systemd import install_units, lifecycle_health
from agent_memory.transactions import (
    apply_recovery,
    incomplete_transactions,
    recovery_plan,
    syncthing_conflicts,
)
from agent_memory.validation import DEFAULT_TYPES
from agent_memory.vault import VaultError, discover_vault, scan_concepts, validate_vault
from agent_memory.worker import drain_once, retry_failed


def _add_location(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--config", help="path to system/memory.yaml")
    parser.add_argument("--vault", help="override the configured vault path")


def _add_context(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--session-id", help="active logical session ID")
    parser.add_argument("--agent", help="active agent identity")
    parser.add_argument("--model", help="exact provider/model identifier")


def _add_mutation_options(parser: argparse.ArgumentParser, *, summary: bool = True) -> None:
    if summary:
        parser.add_argument("--summary")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--json", action="store_true", dest="json_output")
    _add_location(parser)
    _add_context(parser)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="memory",
        description="Manage local-first agent memory.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("--config", dest="global_config", help="path to system/memory.yaml")
    parser.add_argument("--vault", dest="global_vault", help="override the configured vault path")
    commands = parser.add_subparsers(dest="command")

    search = commands.add_parser("search", help="search concept Markdown deterministically")
    search.add_argument("query")
    search.add_argument("--type", dest="concept_type")
    search.add_argument("--scope")
    search.add_argument("--tag")
    search.add_argument("--creator")
    search.add_argument("--status")
    search.add_argument(
        "--verification",
        choices=("unverified", "machine-confirmed", "human-reviewed"),
    )
    search.add_argument("--stale", action="store_true")
    search.add_argument("--limit", type=int)
    search.add_argument("--reason")
    search.add_argument("--json", action="store_true", dest="json_output")
    _add_location(search)
    _add_context(search)

    show = commands.add_parser("show", help="show one concept")
    show.add_argument("concept_id")
    show.add_argument("--reason")
    show.add_argument("--no-audit", action="store_true")
    show.add_argument("--json", action="store_true", dest="json_output")
    _add_location(show)
    _add_context(show)

    validate = commands.add_parser("validate", help="validate the OKF bundle")
    validate.add_argument("concept_id", nargs="?")
    validate.add_argument("--strict", action="store_true")
    validate.add_argument("--json", action="store_true", dest="json_output")
    _add_location(validate)

    init = commands.add_parser("init", help="initialize a vault without overwriting files")
    init.add_argument("--json", action="store_true", dest="json_output")
    _add_location(init)

    doctor = commands.add_parser("doctor", help="diagnose managed-write and lifecycle state")
    doctor.add_argument("--json", action="store_true", dest="json_output")
    _add_location(doctor)

    worker = commands.add_parser("worker", help="drain durable lifecycle work")
    worker.add_argument("--once", action="store_true", required=True)
    worker.add_argument("--json", action="store_true", dest="json_output")
    _add_location(worker)

    retry = commands.add_parser("retry", help="republish failed lifecycle work")
    retry_target = retry.add_mutually_exclusive_group(required=True)
    retry_target.add_argument("retry_id", nargs="?")
    retry_target.add_argument("--all", action="store_true", dest="all_failed")
    retry.add_argument("--json", action="store_true", dest="json_output")
    _add_location(retry)

    install = commands.add_parser("install-lifecycle", help="install systemd user lifecycle units")
    install.add_argument("--json", action="store_true", dest="json_output")
    _add_location(install)

    session = commands.add_parser("session", help="publish or recover logical session state")
    session_commands = session.add_subparsers(dest="session_command", required=True)
    for name in ("start", "checkpoint", "finalize"):
        item = session_commands.add_parser(name)
        item.add_argument("--agent", required=True, choices=("pi", "hermes"))
        item.add_argument("--agent-version", required=True)
        item.add_argument("--session-id", required=True)
        item.add_argument("--started-at", required=True)
        item.add_argument("--occurred-at")
        item.add_argument("--native-event-id", "--event-id", dest="native_event_id")
        item.add_argument("--model")
        item.add_argument("--platform")
        item.add_argument("--native-store-ref")
        item.add_argument("--json", action="store_true", dest="json_output")
        _add_location(item)
    checkpoint = session_commands.choices["checkpoint"]
    checkpoint.add_argument(
        "--trigger",
        required=True,
        choices=("compaction", "compression", "reset", "new", "finalization"),
    )
    checkpoint.add_argument(
        "--summary-kind", choices=("pi", "hermes-0.20.0", "unavailable"), default="unavailable"
    )
    checkpoint.add_argument("--compaction-entry-id")
    checkpoint.add_argument("--native-summary-file")
    checkpoint.add_argument("--old-session-id")
    checkpoint.add_argument("--in-place", action="store_true")
    checkpoint.add_argument("--compression-count", type=int)
    checkpoint.add_argument("--previous-message-row-id", type=int)
    checkpoint.add_argument("--current-message-row-id", type=int)
    checkpoint.add_argument("--candidate-row-id", type=int)
    checkpoint.add_argument("--candidate-summary-sha256")
    access_session = session_commands.add_parser("access")
    access_session.add_argument("--session-id", required=True)
    access_session.add_argument("--agent", required=True)
    access_session.add_argument("--model", required=True)
    access_session.add_argument("--mode", required=True, choices=("injected", "search", "show"))
    access_session.add_argument("--query")
    access_session.add_argument("--reason")
    access_session.add_argument("--concept", action="append", default=[])
    access_session.add_argument("--json", action="store_true", dest="json_output")
    _add_location(access_session)
    recover_session = session_commands.add_parser("recover")
    recover_session.add_argument("--agent", choices=("pi", "hermes"))
    recover_session.add_argument("--json", action="store_true", dest="json_output")
    _add_location(recover_session)

    create = commands.add_parser("create", help="create a managed concept")
    create.add_argument("--type", required=True, dest="concept_type")
    create.add_argument("--scope", required=True)
    create.add_argument("--title", required=True)
    create.add_argument("--description", required=True)
    create.add_argument("--body-file", required=True)
    create.add_argument("--source", action="append", default=[])
    create.add_argument("--resource")
    create.add_argument("--slug")
    create.add_argument("--tag", action="append", default=[])
    create.add_argument("--status")
    create.add_argument("--content-owner", choices=("user", "agent"))
    create.add_argument("--allow-long", action="store_true")
    _add_mutation_options(create)

    update = commands.add_parser("update", help="update a managed concept")
    update.add_argument("concept_id")
    update.add_argument("--body-file", required=True)
    update.add_argument("--type", dest="concept_type")
    update.add_argument("--scope")
    update.add_argument("--title")
    update.add_argument("--description")
    update.add_argument("--source", action="append")
    update.add_argument("--resource")
    update.add_argument("--tag", action="append")
    update.add_argument("--status")
    update.add_argument("--allow-long", action="store_true")
    _add_mutation_options(update)

    delete = commands.add_parser("delete", help="delete a managed concept")
    delete.add_argument("concept_id")
    delete.add_argument("--reason", required=True)
    delete.add_argument("--authorized-by")
    delete.add_argument("--authorization-source")
    _add_mutation_options(delete, summary=False)

    rename = commands.add_parser("rename", help="rename a managed concept and its links")
    rename.add_argument("concept_id")
    rename.add_argument("new_slug")
    rename.add_argument("--reason", required=True)
    _add_mutation_options(rename, summary=False)

    verify = commands.add_parser("verify", help="record explicit human verification")
    verify.add_argument("concept_id")
    verify.add_argument("--authorization-source")
    verify.add_argument("--note")
    _add_mutation_options(verify)

    reconcile = commands.add_parser("reconcile", help="adopt one direct concept edit")
    reconcile.add_argument("concept_id")
    reconcile.add_argument("--summary", required=True)
    reconcile.add_argument("--dry-run", action="store_true")
    reconcile.add_argument("--json", action="store_true", dest="json_output")
    _add_location(reconcile)

    rebuild = commands.add_parser(
        "rebuild-index", help="fully rebuild the index from a clean concept corpus"
    )
    rebuild.add_argument("--dry-run", action="store_true")
    rebuild.add_argument("--json", action="store_true", dest="json_output")
    _add_location(rebuild)

    apply = commands.add_parser("apply", help="apply a version-1 batch transaction")
    apply.add_argument("transaction_file")
    _add_mutation_options(apply, summary=False)

    recover = commands.add_parser("recover", help="preview or apply transaction recovery")
    recover.add_argument("--transaction", required=True)
    recover.add_argument("--apply", action="store_true")
    recover.add_argument("--json", action="store_true", dest="json_output")
    _add_location(recover)
    return parser


def _plain(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        return [_plain(item) for item in value]
    if isinstance(value, datetime | date):
        return value.isoformat()
    return value


def _json(data: object) -> None:
    print(json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":")))


def _selected_config_path(args: argparse.Namespace) -> str | None:
    config_path = args.config or args.global_config
    vault_override = args.vault or args.global_vault
    if config_path is None and vault_override:
        local_config = Path(vault_override).expanduser() / "system/memory.yaml"
        if local_config.is_file():
            config_path = str(local_config)
    return config_path


def _config_and_vault(args: argparse.Namespace) -> tuple[dict[str, Any], Any]:
    config_path = _selected_config_path(args)
    vault_override = args.vault or args.global_vault
    config = load_config(config_path)
    if vault_override:
        config["vault"] = str(Path(vault_override).expanduser().resolve(strict=False))
    validate_worker_state_dir(config["worker"]["state_dir"], config["vault"])
    return config, discover_vault(config["vault"])


def _context(args: argparse.Namespace) -> RetrievalContext:
    environment = os.environ
    pi_session = environment.get("PI_SESSION_ID")
    hermes_session = environment.get("HERMES_SESSION_ID")
    session_id = (
        args.session_id
        or environment.get("MEMORY_SESSION_ID")
        or pi_session
        or hermes_session
        or ""
    )
    agent = (
        args.agent
        or environment.get("MEMORY_AGENT")
        or ("pi" if pi_session else None)
        or ("hermes-agent" if hermes_session else None)
        or ""
    )
    pi_provider = environment.get("PI_PROVIDER")
    pi_model = environment.get("PI_MODEL")
    explicit_model_free_actor = isinstance(args.agent, str) and args.agent.startswith(
        ("human:", "process:")
    )
    model = args.model or (
        ""
        if explicit_model_free_actor
        else (
            environment.get("MEMORY_MODEL")
            or (f"{pi_provider}/{pi_model}" if pi_provider and pi_model else None)
            or environment.get("HERMES_MODEL")
            or ""
        )
    )
    return RetrievalContext(session_id=session_id, agent=agent, model=model)


def _audit_state(config: Mapping[str, Any], vault_root: Path) -> Path:
    return validate_worker_state_dir(config["worker"]["state_dir"], vault_root)


def _configured_types(config: Mapping[str, Any]) -> frozenset[str]:
    value = config.get("types")
    if value is None:
        return DEFAULT_TYPES
    if not isinstance(value, list) or not all(isinstance(item, str) and item for item in value):
        raise ConfigError("types must be a list of non-empty strings")
    return frozenset(value)


def _search(args: argparse.Namespace) -> int:
    config, vault = _config_and_vault(args)
    concepts = scan_concepts(vault)
    filters = SearchFilters(
        concept_type=args.concept_type,
        scope=args.scope,
        tag=args.tag,
        creator=args.creator,
        status=args.status,
        verification=args.verification,
        stale=args.stale,
    )
    limit = args.limit if args.limit is not None else config["search"]["default_limit"]
    results = search_concepts(concepts, args.query, filters=filters, limit=limit)
    append_access_event(
        _audit_state(config, vault.root),
        _context(args),
        mode="search",
        query=args.query,
        reason=args.reason,
        concepts=[result.concept.concept_id for result in results],
    )
    payload = {
        "query": args.query,
        "results": [
            {
                "id": result.concept.concept_id,
                "title": str(result.concept.document.metadata.get("title", "")),
                "description": str(result.concept.document.metadata.get("description", "")),
                "type": result.concept.document.metadata.get("type"),
                "scope": result.concept.document.metadata.get("scope"),
                "status": result.status,
                "verification": result.trust_tier,
                "stale": result.stale,
                "matched_fields": list(result.matched_fields),
            }
            for result in results
        ],
    }
    if args.json_output:
        _json(_plain(payload))
    elif not results:
        print("No matching concepts.")
    else:
        for item in payload["results"]:
            freshness = "stale" if item["stale"] else "current"
            print(
                f"{item['id']}\t{item['title']}\t{item['verification']}\t{freshness}"
                f"\tmatched: {', '.join(item['matched_fields'])}"
            )
    return 0


def _show(args: argparse.Namespace) -> int:
    config, vault = _config_and_vault(args)
    concept = resolve_concept(scan_concepts(vault), args.concept_id)
    if not args.no_audit:
        append_access_event(
            _audit_state(config, vault.root),
            _context(args),
            mode="show",
            reason=args.reason,
            concepts=[concept.concept_id],
        )
    if args.json_output:
        _json(
            _plain(
                {
                    "id": concept.concept_id,
                    "metadata": concept.document.metadata,
                    "body": concept.document.body,
                    "verification": trust_tier(concept.document.metadata),
                    "stale": is_stale(concept.document.metadata),
                }
            )
        )
    else:
        freshness = "stale" if is_stale(concept.document.metadata) else "current"
        print(f"Verification: {trust_tier(concept.document.metadata)} | Freshness: {freshness}")
        print(concept.text, end="" if concept.text.endswith("\n") else "\n")
    return 0


def _validate(args: argparse.Namespace) -> int:
    config, vault = _config_and_vault(args)
    issues = list(
        validate_vault(
            vault,
            configured_types=_configured_types(config),
            max_words=config["limits"]["concept_words"],
        )
    )
    if args.concept_id:
        concept = resolve_concept(scan_concepts(vault), args.concept_id)
        relative = concept.path.relative_to(vault.root).as_posix()
        issues = [issue for issue in issues if issue.path == relative]
    invalid = any(issue.level == "error" or args.strict for issue in issues)
    payload = {
        "ok": not invalid,
        "strict": args.strict,
        "issues": [
            {
                "path": issue.path,
                "level": issue.level,
                "field": issue.field,
                "message": issue.message,
            }
            for issue in issues
        ],
    }
    if args.json_output:
        _json(payload)
    elif not issues:
        print("Vault is valid.")
    else:
        for issue in issues:
            print(f"{issue.level}: {issue.path}: {issue.field}: {issue.message}")
    return 1 if invalid else 0


def _doctor(args: argparse.Namespace) -> int:
    config, vault = _config_and_vault(args)
    issues: list[str] = []
    state_dir = Path(config["transactions"]["state_dir"])
    try:
        state_dir.resolve(strict=False).relative_to(vault.root.resolve())
    except ValueError:
        pass
    else:
        issues.append("transaction state directory is inside the vault")
    existing_parent = state_dir
    while not existing_parent.exists() and existing_parent != existing_parent.parent:
        existing_parent = existing_parent.parent
    if existing_parent.exists() and existing_parent.stat().st_dev != vault.root.stat().st_dev:
        issues.append("transaction state directory is on another filesystem")
    try:
        ensure_repository(vault.root, str(config["git"]["branch"]))
    except ValueError as exc:
        issues.append(str(exc))
    staged = staged_paths(vault.root)
    if staged:
        issues.append(f"pre-existing staged paths: {', '.join(staged)}")
    conflicts = syncthing_conflicts(vault.root)
    if conflicts:
        issues.append(f"Syncthing conflict artifacts: {', '.join(conflicts)}")
    pending = incomplete_transactions(state_dir, vault.root)
    transactions = [recovery_plan(state_dir, vault.root, item) for item in pending]
    if pending:
        issues.append(f"incomplete transactions: {', '.join(pending)}")
    lock_owner: dict[str, Any] | None = None
    lock_path = state_dir / "writer.lock"
    if lock_path.is_file() and lock_path.stat().st_size:
        try:
            value = json.loads(lock_path.read_text(encoding="utf-8"))
            if isinstance(value, dict):
                lock_owner = value
                pid = value.get("pid")
                try:
                    if not isinstance(pid, int):
                        raise ProcessLookupError
                    os.kill(pid, 0)
                    lock_owner["state"] = "live"
                except ProcessLookupError:
                    lock_owner["state"] = "stale"
        except (OSError, ValueError):
            issues.append("writer lock metadata is unreadable")
    lifecycle = lifecycle_health(config["worker"]["state_dir"])
    issues.extend(lifecycle["issues"])
    payload = {
        "ok": not issues,
        "issues": issues,
        "transactions": transactions,
        "lock_owner": lock_owner,
        "lifecycle": lifecycle,
    }
    if args.json_output:
        _json(payload)
    elif not issues:
        print("Managed-write state is healthy.")
    else:
        for issue in issues:
            print(f"error: {issue}")
        for transaction in transactions:
            print(
                f"transaction {transaction['transaction_id']}: "
                f"{transaction['phase']} -> {transaction['action']}"
            )
        if lifecycle["failed_units"] or lifecycle["start_limited_units"]:
            print("After fixing the crash, recover with:")
            print(lifecycle["recovery"])
    return 0 if not issues else 1


def _init(args: argparse.Namespace) -> int:
    config_path = args.config or args.global_config
    vault_override = args.vault or args.global_vault
    config = load_config(config_path)
    root = Path(vault_override or config["vault"]).expanduser()
    state_dir = (
        Path(config["transactions"]["state_dir"])
        if config_path is not None
        else root.parent / ".agent-memory-txn"
    )
    created = initialize_vault(
        root,
        state_dir=state_dir,
        timeout=float(config["locking"]["timeout_seconds"]),
        branch=str(config["git"]["branch"]),
    )
    payload = {"vault": str(root.resolve(strict=False)), "created": list(created)}
    if args.json_output:
        _json(payload)
    else:
        print(f"Initialized {root}; created {len(created)} files.")
    return 0


def _mutation_context(args: argparse.Namespace) -> MutationContext:
    environment = os.environ
    pi_session = environment.get("PI_SESSION_ID")
    hermes_session = environment.get("HERMES_SESSION_ID")
    actor = (
        args.agent
        or environment.get("MEMORY_AGENT")
        or ("pi" if pi_session else None)
        or ("hermes-agent" if hermes_session else None)
        or "process:memory-cli"
    )
    session_id = (
        args.session_id
        or environment.get("MEMORY_SESSION_ID")
        or pi_session
        or hermes_session
        or None
    )
    if actor.startswith(("human:", "process:")):
        model = args.model
    else:
        pi_provider = environment.get("PI_PROVIDER")
        pi_model = environment.get("PI_MODEL")
        model = (
            args.model
            or environment.get("MEMORY_MODEL")
            or (f"{pi_provider}/{pi_model}" if pi_provider and pi_model else None)
            or environment.get("HERMES_MODEL")
        )
    return MutationContext(actor=actor, model=model, session_id=session_id)


def _clean_mapping(value: Mapping[str, Any]) -> dict[str, Any]:
    return {key: item for key, item in value.items() if item is not None}


def _single_operation(args: argparse.Namespace) -> tuple[dict[str, Any], str]:
    if args.command == "create":
        operation = _clean_mapping(
            {
                "action": "create",
                "type": args.concept_type,
                "scope": args.scope,
                "title": args.title,
                "description": args.description,
                "body_file": args.body_file,
                "sources": args.source,
                "resource": args.resource,
                "slug": args.slug,
                "tags": args.tag or None,
                "status": args.status,
                "content_owner": args.content_owner,
                "allow_long": args.allow_long,
            }
        )
        return operation, args.summary or f"Create {args.title}"
    if args.command == "update":
        operation = _clean_mapping(
            {
                "action": "update",
                "id": args.concept_id,
                "body_file": args.body_file,
                "type": args.concept_type,
                "scope": args.scope,
                "title": args.title,
                "description": args.description,
                "sources": args.source,
                "resource": args.resource,
                "tags": args.tag,
                "status": args.status,
                "allow_long": args.allow_long,
            }
        )
        return operation, args.summary or f"Update {args.concept_id}"
    if args.command == "delete":
        return (
            _clean_mapping(
                {
                    "action": "delete",
                    "id": args.concept_id,
                    "authorized_by": args.authorized_by,
                    "authorization_source": args.authorization_source,
                }
            ),
            args.reason,
        )
    if args.command == "verify":
        return (
            _clean_mapping(
                {
                    "action": "verify",
                    "id": args.concept_id,
                    "authorization_source": args.authorization_source,
                    "note": args.note,
                }
            ),
            args.summary or f"Verify {args.concept_id}",
        )
    return {"action": "rename", "id": args.concept_id, "new_slug": args.new_slug}, args.reason


def _read_batch(path: str) -> dict[str, Any]:
    yaml = YAML(typ="safe", pure=True)
    yaml.allow_duplicate_keys = False
    value = yaml.load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict) or value.get("version") != 1:
        raise ValueError("batch transaction must be a version-1 mapping")
    if not isinstance(value.get("operations"), list) or not all(
        isinstance(operation, dict) for operation in value["operations"]
    ):
        raise ValueError("batch operations must be a list of mappings")
    return value


def _print_mutation_result(
    args: argparse.Namespace,
    result: MutationResult,
    *,
    include_candidates: bool = False,
    empty_message: str | None = None,
) -> int:
    transaction = result.transaction
    payload = {
        "transaction_id": transaction.transaction_id,
        "changed_paths": list(transaction.changed_paths),
        "commit_hash": transaction.commit_hash,
        "dry_run": transaction.dry_run,
    }
    if include_candidates:
        payload["duplicate_candidates"] = list(result.duplicate_candidates)
    if args.json_output:
        _json(payload)
    elif transaction.changed_paths:
        mode = "Would change" if transaction.dry_run else "Changed"
        print(f"{mode}: {', '.join(transaction.changed_paths)}")
        if transaction.commit_hash:
            print(f"Commit: {transaction.commit_hash}")
        if result.duplicate_candidates:
            print(f"Candidates: {', '.join(result.duplicate_candidates)}")
    elif empty_message:
        print(empty_message)
    return 0


def _mutate(args: argparse.Namespace) -> int:
    config, vault = _config_and_vault(args)
    interactive_verification = False
    if args.command == "verify" and args.authorization_source is None:
        if not sys.stdin.isatty():
            raise ValueError("noninteractive human verification requires --authorization-source")
        answer = input(f"Confirm human verification of {args.concept_id}? [y/N] ")
        if answer.strip().casefold() not in {"y", "yes"}:
            raise ValueError("human verification was not confirmed")
        interactive_verification = True
    if args.command == "apply":
        batch = _read_batch(args.transaction_file)
        actor = batch.get("actor")
        if not isinstance(actor, Mapping) or not isinstance(actor.get("by"), str):
            raise ValueError("batch actor.by is required")
        context = MutationContext(
            actor=str(args.agent or actor["by"]),
            model=args.model or actor.get("model"),
            session_id=args.session_id or actor.get("session_id"),
        )
        operations = batch["operations"]
        summary = batch.get("summary")
        if not isinstance(summary, str) or not summary.strip():
            raise ValueError("batch summary must be non-empty text")
    else:
        operation, summary = _single_operation(args)
        operations = [operation]
        context = (
            MutationContext(str(config["identity"]["human"]))
            if interactive_verification
            else _mutation_context(args)
        )
    result = apply_operations(
        vault,
        config,
        operations,
        context=context,
        summary=summary,
        dry_run=args.dry_run,
        interactive_verification=interactive_verification,
    )
    return _print_mutation_result(args, result, include_candidates=True)


def _reconcile(args: argparse.Namespace) -> int:
    config, vault = _config_and_vault(args)
    result = reconcile_concept(
        vault,
        config,
        args.concept_id,
        summary=args.summary,
        dry_run=args.dry_run,
    )
    return _print_mutation_result(args, result)


def _rebuild_index(args: argparse.Namespace) -> int:
    config, vault = _config_and_vault(args)
    result = rebuild_index(vault, config, dry_run=args.dry_run)
    return _print_mutation_result(args, result, empty_message="Concept index is already current.")


def _recover(args: argparse.Namespace) -> int:
    config, vault = _config_and_vault(args)
    state_dir = Path(config["transactions"]["state_dir"])
    with writer_lock(
        state_dir / "writer.lock",
        timeout=float(config["locking"]["timeout_seconds"]),
        command="recover",
        actor="process:memory-cli",
    ):
        plan = (
            apply_recovery(state_dir, vault.root, args.transaction)
            if args.apply
            else recovery_plan(state_dir, vault.root, args.transaction)
        )
    if args.json_output:
        _json(plan)
    else:
        print(f"Transaction {plan['transaction_id']}: {plan['phase']} -> {plan['action']}")
        print(f"Paths: {', '.join(plan['changed_paths'])}")
    return 0


def _worker(args: argparse.Namespace) -> int:
    config, vault = _config_and_vault(args)
    _audit_state(config, vault.root)
    result = drain_once(vault.root, config)
    if args.json_output:
        _json(result)
    else:
        print(f"Processed {result['processed']} lifecycle event(s); failed {result['failed']}.")
    return 1 if result["failed"] else 0


def _retry_lifecycle(args: argparse.Namespace) -> int:
    config, vault = _config_and_vault(args)
    _audit_state(config, vault.root)
    values = retry_failed(
        config["worker"]["state_dir"], retry_id=args.retry_id, all_failed=args.all_failed
    )
    payload = {"republished": list(values)}
    if args.json_output:
        _json(payload)
    else:
        print(f"Republished {len(values)} lifecycle event(s).")
    return 0


def _install_lifecycle(args: argparse.Namespace) -> int:
    config, vault = _config_and_vault(args)
    _audit_state(config, vault.root)
    config_path = _selected_config_path(args)
    result = install_units(
        config["worker"]["state_dir"],
        config_path=config_path,
        vault=vault.root,
    )
    if args.json_output:
        _json(result)
    else:
        print(f"Installed {result['path_unit']} and {result['service_unit']}.")
        if result["warning"]:
            print(f"warning: {result['warning']}")
    return 0


def _session(args: argparse.Namespace) -> int:
    config, vault = _config_and_vault(args)
    state = _audit_state(config, vault.root)
    if args.session_command == "recover":
        changed = recover_incomplete(vault.root, config, agent=args.agent)
        payload = {"changed_paths": list(changed)}
    elif args.session_command == "access":
        path = append_access_event(
            state,
            RetrievalContext(args.session_id, args.agent, args.model),
            mode=args.mode,
            query=args.query,
            reason=args.reason,
            concepts=args.concept,
        )
        payload = {"spooled": str(path)}
    else:
        source: dict[str, Any] = {"kind": "unavailable"}
        trigger = "start" if args.session_command == "start" else "finalization"
        event_kind = "session_start" if args.session_command == "start" else "finalize"
        if args.session_command == "checkpoint":
            event_kind = "finalize" if args.trigger == "finalization" else "checkpoint"
            trigger = args.trigger
            if args.summary_kind == "pi":
                if not args.compaction_entry_id or not args.native_summary_file:
                    raise ValueError("Pi checkpoints require entry ID and native summary file")
                source = {
                    "kind": "pi",
                    "compaction_entry_id": args.compaction_entry_id,
                    "summary": Path(args.native_summary_file).read_text(encoding="utf-8"),
                }
            elif args.summary_kind == "hermes-0.20.0":
                source = {
                    "kind": "hermes-0.20.0",
                    "platform": args.platform,
                    "session_id": args.session_id,
                    "old_session_id": args.old_session_id,
                    "in_place": args.in_place,
                    "compression_count": args.compression_count,
                    "previous_message_row_id": args.previous_message_row_id,
                    "current_message_row_id": args.current_message_row_id,
                    "candidate_row_id": args.candidate_row_id,
                    "candidate_summary_sha256": args.candidate_summary_sha256,
                }
        descriptor = build_descriptor(
            event_kind=event_kind,
            agent=args.agent,
            agent_version=args.agent_version,
            session_id=args.session_id,
            started_at=args.started_at,
            trigger=trigger,
            occurred_at=args.occurred_at or now_utc(),
            state_dir=state,
            summary_source=source,
            native_event_id=args.native_event_id,
            model=args.model,
            platform=args.platform,
            native_store_ref=args.native_store_ref,
        )
        path = publish_descriptor(
            state,
            descriptor,
            timeout_ms=config["worker"]["publish_timeout_ms"],
        )
        payload = {"published": str(path), "event_id": descriptor["event_id"]}
    if args.json_output:
        _json(payload)
    else:
        print(next(iter(payload.values())))
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not args.command:
        build_parser().print_help()
        return 0
    handlers = {
        "init": _init,
        "doctor": _doctor,
        "worker": _worker,
        "retry": _retry_lifecycle,
        "install-lifecycle": _install_lifecycle,
        "session": _session,
        "search": _search,
        "show": _show,
        "validate": _validate,
        "create": _mutate,
        "update": _mutate,
        "delete": _mutate,
        "rename": _mutate,
        "verify": _mutate,
        "reconcile": _reconcile,
        "rebuild-index": _rebuild_index,
        "apply": _mutate,
        "recover": _recover,
    }
    try:
        return handlers[args.command](args)
    except (AuditError, ConfigError, OSError, ValueError, VaultError) as exc:
        if getattr(args, "json_output", False):
            _json({"error": str(exc)})
        else:
            print(f"error: {exc}", file=sys.stderr)
        return 2
