# Hermes adapter

Pinned compatibility: **Hermes Agent 0.20.0 (2026.8.3)**, commit
`bc80a0be5c1b496a6212a1c6c594b3c5a78e31c6`.

The user plugin injects `memory/index.md` through `pre_llm_call`, so continued and resumed
sessions do not depend on `on_session_start`. The gateway hook treats `session:compress` only
as a committed five-field signal. Both adapters invoke the `memory` JSON CLI; they never import
the memory package, read conversation history, or call a model.

## Install and enable

Install the Python CLI and lifecycle worker first. From this repository checkout:

```bash
hermes_home="${HERMES_HOME:-$HOME/.hermes}"
adapter_dir="$(cd adapters/hermes && pwd)"
mkdir -p "$hermes_home/plugins" "$hermes_home/hooks"
ln -s "$adapter_dir" "$hermes_home/plugins/agent-memory"
ln -s "$adapter_dir/gateway-hook" "$hermes_home/hooks/agent-memory"
hermes plugins enable agent-memory
hermes gateway restart
memory doctor --json
```

Hermes discovers the plugin from `~/.hermes/plugins/agent-memory/plugin.yaml`; explicit
`hermes plugins enable agent-memory` adds it to `plugins.enabled`. Gateway hooks are discovered
independently from `~/.hermes/hooks/agent-memory/HOOK.yaml` and need no plugin allow-list entry.
Restart CLI sessions and the gateway after installation.

Optional deployment overrides:

- `AGENT_MEMORY_CLI`: path to the `memory` executable;
- `AGENT_MEMORY_CONFIG`: path passed as global `memory --config`;
- `AGENT_MEMORY_VAULT`: path passed as global `memory --vault`;
- `HERMES_STATE_DB`: Hermes SQLite path (defaults to `$HERMES_HOME/state.db`).

The plugin stores only durable binding identities in the configured lifecycle state directory.
It hashes sender identity, never stores `pre_llm_call` history or prompts, and shows persistent
worker warnings only in the originating session. Telegram warnings fail closed unless a reliable
host context identifies a DM and sender; the pinned 0.20.0 `pre_llm_call` surface does not expose
chat type, so it cannot safely perform a separate owner-DM delivery. Live Telegram validation is
reserved for Milestone 9.

## Validate

```bash
uv run pytest tests/integration/test_milestone7_hermes.py tests/unit/test_hermes_summary.py
uv run memory doctor --json
```

A gateway compression descriptor contains the installed five lineage fields, persisted previous
and current message-row boundaries, and a nullable isolated candidate row/SHA-256. It contains no
summary text or raw transcript. `memory worker --once` later fetches and verifies only that exact
row. CLI reset, `/new`, rotation/GC finalization, and CLI exit publish lifecycle/audit state with
`native summary unavailable`.
