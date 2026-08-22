# Pi adapter

Pinned compatibility: `@earendil-works/pi-coding-agent` **0.84.2** on Node **24.13.1**.
The extension uses only documented 0.84.2 events and the `memory` JSON CLI. It does not read Pi JSONL or call a model.

## Install

Install the Python CLI and lifecycle worker first, then link this directory into Pi's global auto-discovery path:

From the repository checkout:

```bash
pi_agent_dir="${PI_CODING_AGENT_DIR:-$HOME/.pi/agent}"
adapter_dir="$(cd adapters/pi && pwd)"
mkdir -p "$pi_agent_dir/extensions"
ln -s "$adapter_dir" "$pi_agent_dir/extensions/agent-memory"
memory doctor --json
```

Pi loads `$PI_CODING_AGENT_DIR/extensions/agent-memory/index.ts` (or `$HOME/.pi/agent/extensions/agent-memory/index.ts` when unset) automatically. Use `/reload` after installation; reload neither finalizes the logical session nor reinjects an index already represented by the extension's persistent custom message.

By default the adapter runs `memory`. Optional deployment overrides are:

- `AGENT_MEMORY_CLI`: path to the `memory` executable;
- `AGENT_MEMORY_CONFIG`: path passed as `memory --config`; and
- `AGENT_MEMORY_VAULT`: path passed as `memory --vault`.

The adapter publishes lifecycle descriptors synchronously within a fixed command timeout, then leaves Git/materialization to `memory worker --once`. Publication failures appear in the Pi TUI. Persistent worker failures are delivered from the existing lifecycle notification state on a later Pi turn and acknowledged only after notification.

## Validate

```bash
npm test --prefix adapters/pi
```

The adapter suite includes a no-`-e` RPC lifecycle through Pi's global auto-discovery path without making a provider/model call.
