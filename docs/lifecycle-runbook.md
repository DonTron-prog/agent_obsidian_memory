# Lifecycle operations

Pi and Hermes adapters are installed later in Milestones 6 and 7. This runbook covers only the generic lifecycle core.

## Install or upgrade

Set `worker.state_dir` in `system/memory.yaml`, then run:

```bash
memory install-lifecycle
```

The command derives non-hidden `ready/`, `claimed/`, and unwatched `failed/` directories, renders both `DirectoryNotEmpty=` paths, and installs a `Type=oneshot` service with an absolute `memory` executable plus the selected `--config`/`--vault` context before `worker --once`. It runs the user daemon reload, enables the path, and attempts to enable user lingering. If lingering cannot be enabled, boot backlog recovers at the next login rather than before login. No timer or application daemon is installed.

A lifecycle event is durable only after its fsynced atomic descriptor publication completes. If a handler never runs or publication does not complete, recovery is not claimed. Publication errors should be reported by the native caller but must not block its compaction, reset, new-session, finalization, or exit behavior.

## Drain and recovery

Run a manual drain with:

```bash
memory worker --once
```

The worker takes one lock, drains `claimed/` before `ready/`, and exits. A crash after claim leaves watched claimed work for the next invocation. A crash after the Git commit but before deletion replays as an event-ID no-op and then deletes the descriptor. Context-access audit is materialized at checkpoints and always at reset, `/new`, and finalization.

Retryable failures receive bounded capped retries during that invocation. Exhausted work moves to unwatched `failed/`; it never remains delayed in `ready/`. Inspect health and republish explicitly:

```bash
memory doctor
memory retry <retry-id>
memory retry --all
memory worker --once
```

A missing, ambiguous, changed, or hash-mismatched native carrier records the literal `native summary unavailable`; it is not reconstructed from transcript, preserved tail, history, prompts, or tool output and no model is called. Queue, notification, status, error, and audit state reject or redact secrets.

## Hard crashes and start limits

Inspect both unit status and the user journal, diagnose and fix the worker crash first, then run exactly:

```bash
systemctl --user reset-failed agent-memory-lifecycle.path agent-memory-lifecycle.service
systemctl --user enable --now agent-memory-lifecycle.path
```

`memory doctor` reports worker-lock metadata, failed/start-limited units, and stranded ready, claimed, or failed queues.

## Uninstall

Disable and remove only the user units, preserving the vault and lifecycle state for review:

```bash
systemctl --user disable --now agent-memory-lifecycle.path
rm ~/.config/systemd/user/agent-memory-lifecycle.path ~/.config/systemd/user/agent-memory-lifecycle.service
systemctl --user daemon-reload
```

Delete lifecycle state only after confirming all queues are empty. Uninstall never deletes the vault, its Git history, or native Pi/Hermes stores.
