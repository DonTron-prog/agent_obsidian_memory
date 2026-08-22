# Syncthing, Obsidian, and private-backup operations

This runbook deploys the runtime vault to two trusted devices. Syncthing is
replication, not backup; the server vault's local Git repository remains the
history boundary. Set deployment values locally without recording private host,
device, or remote details:

```bash
export SERVER_HOST="SERVER_HOSTNAME"
export SERVER_VAULT="/home/donald/agent-memory"
export PRIVATE_REMOTE_URL="REVIEWED_PRIVATE_REMOTE_URL"
export CONCEPT_ID="CONCEPT_SLUG"
```

Official references: [getting started](https://docs.syncthing.net/intro/getting-started.html),
[ignore rules](https://docs.syncthing.net/users/ignoring.html), and
[automatic startup](https://docs.syncthing.net/users/autostart.html).

## Pair and share the vault

1. Run Syncthing as the login user on the server and computer. Keep both devices
   in **Send & Receive** mode. On a systemd server, enable it before later
   service commands:

   ```bash
   systemctl --user enable --now syncthing.service
   ```

2. Keep the server GUI bound to localhost. Access it remotely only through an
   SSH tunnel, then open `http://127.0.0.1:18384` locally:

   ```bash
   ssh -N -L 18384:127.0.0.1:8384 "$SERVER_HOST"
   ```

3. Exchange device IDs over a trusted channel and verify each complete ID on
   both devices before accepting. Do not save device IDs, GUI API keys,
   credentials, or screenshots containing them in this repository.
4. Add folder ID `agent-memory` using the configured `$SERVER_VAULT` path.
   Share it only with the approved computer. On the computer, accept it into the
   chosen local Obsidian path. Wait until both devices report **Up to Date**.

## Install machine-local ignores

A root `.stignore` never synchronizes. Create this UTF-8 file separately at the
root of the server vault and the computer replica:

```text
// Machine-local repository and Obsidian state
.git
.obsidian/workspace*.json
.obsidian/cache

// Transient runtime/editor state
system/.state
*.lock
*.tmp
*.swp
```

Never add `*.sync-conflict-*` to `.stignore`. Conflict copies must reach the
server so the CLI can detect them and fail closed. Confirm that `.git/`,
workspace/cache, and a harmless temporary `*.tmp` file do not propagate, while
ordinary Markdown does.

Use a harmless server-side conflict probe to prove reads remain available and a
managed write fails closed. The `create --dry-run` command must exit nonzero;
the trap removes both probes:

```bash
(
  set -eu
  stamp="$(date +%s)"
  probe_dir="$(mktemp -d)"
  conflict_probe="$SERVER_VAULT/memory/probe.sync-conflict-$stamp"
  body_probe="$probe_dir/body.md"
  error_probe="$probe_dir/error.txt"
  trap 'rm -f "$conflict_probe"; rm -rf "$probe_dir"' EXIT
  printf '# Conflict probe\n\nHarmless validation body.\n' >"$body_probe"
  touch "$conflict_probe"
  memory validate --strict --vault "$SERVER_VAULT" # read remains available
  if memory create --type Note --scope personal \
    --title "Syncthing Conflict Probe $stamp" \
    --description "A dry-run conflict safety probe." \
    --slug "syncthing-conflict-probe-$stamp" --content-owner agent \
    --body-file "$body_probe" --source "file://$conflict_probe" \
    --agent process:probe --dry-run --vault "$SERVER_VAULT" 2>"$error_probe"; then
    echo "error: managed write did not fail closed" >&2
    exit 1
  fi
  diagnostic="$(sed -n 's/^error: //p' "$error_probe")"
  case "$diagnostic" in
    "Syncthing conflict artifacts block writes:"*) ;;
    *) echo "error: managed write failed for an unexpected reason" >&2; exit 1 ;;
  esac
)
```

## Open and check Obsidian

1. In Obsidian, choose **Open folder as vault** and select the synchronized
   computer replica. Do not use SSHFS.
2. Enable the **Bases** core plugin.
3. Open `memory/memories.base` and verify exactly these nine named views:
   **Work, Projects, People, Preferences, Procedures, Notes, Tasks, Decisions,
   References**. Confirm the `verified` and `stale_after` properties are
   available as columns or manual filters; do not add more required views.
4. Confirm a server-side managed change appears without restarting Obsidian.

## Live outage and recovery check

Start from an **Up to Date**, clean vault. Stop Syncthing on the server and
confirm it is inactive:

```bash
systemctl --user stop syncthing.service
systemctl --user is-active syncthing.service
```

Create or update one harmless managed concept with explicit actor, source, and,
for an agent actor, exact model provenance. Record the command exit code and
commit hash. The write must succeed while Syncthing is stopped. Run:

```bash
memory validate --strict --vault "$SERVER_VAULT"
git -C "$SERVER_VAULT" status --short
git -C "$SERVER_VAULT" log -1 --oneline
```

Restart synchronization and wait for both devices to become **Up to Date**:

```bash
systemctl --user start syncthing.service
systemctl --user is-active syncthing.service
```

Verify the managed change appears in Obsidian without restart. After any outage,
inspect both devices for conflict copies before resuming managed writes.

## Measure propagation

Take at least three samples in each direction while both devices initially show
**Up to Date**. Use UTC timestamps and the same small concept: managed
create/update on the server, direct body correction on the computer. Reconcile
each computer-originated correction after it reaches the server:

```bash
memory reconcile "$CONCEPT_ID" --summary "Reconcile propagation sample" \
  --vault "$SERVER_VAULT"
```

| Direction | Sample | Source completed (UTC) | Destination visible (UTC) | Seconds |
|---|---:|---|---|---:|
| server → computer | 1 |  |  |  |
| server → computer | 2 |  |  |  |
| server → computer | 3 |  |  |  |
| computer → server | 1 |  |  |  |
| computer → server | 2 |  |  |  |
| computer → server | 3 |  |  |  |

Record min/median/max for each direction and note Syncthing/Obsidian versions,
network conditions, and whether Obsidian refreshed without restart. Do not put
private content or device IDs in the evidence.

## Resolve conflicts safely

1. Stop managed writes. List every conflict copy and inspect it beside the
   canonical file; do not delete an unreviewed copy.
2. Manually choose or merge the correct current content. Preserve a copy outside
   the vault if evidence is still needed.
3. Remove the reviewed conflict artifact. If canonical concept content changed,
   adopt it explicitly:

   ```bash
   memory reconcile "$CONCEPT_ID" --summary "Resolve reviewed Syncthing conflict" \
     --vault "$SERVER_VAULT"
   ```

4. Before writes resume, run:

   ```bash
   memory doctor --vault "$SERVER_VAULT"
   memory validate --strict --vault "$SERVER_VAULT"
   git -C "$SERVER_VAULT" status --short
   ```

The CLI rechecks target hashes immediately before replacement, but Syncthing is
not paused. A narrow race remains if a synchronized write lands after the final
hash check and before atomic replacement. Resolve any real collision manually;
add coordination only if observed collisions justify it.

## Review, push, and restore the private backup

Before adding a remote, verify that it is a separate private repository approved
for personal and work content. Review account access, organization policy,
retention, encryption/transport, and repository visibility. Keep credentials
out of remote URLs, command logs, and documentation.

From the runtime vault, configure and push with ordinary Git—not `memory`:

```bash
(
  set -eu
  existing_remote="$(git -C "$SERVER_VAULT" remote get-url origin 2>/dev/null || true)"
  if [ -n "$existing_remote" ]; then
    test "$existing_remote" = "$PRIVATE_REMOTE_URL"
  else
    git -C "$SERVER_VAULT" remote add origin "$PRIVATE_REMOTE_URL"
  fi
  git -C "$SERVER_VAULT" push -u origin main
)
```

Verify the remote remains private and its `main` commit matches local `HEAD`.
Remote configuration or availability must never block local managed writes.
Exercise restoration without touching the live vault:

```bash
(
  set -eu
  restore_root="$(mktemp -d)"
  trap 'rm -rf "$restore_root"' EXIT
  expected_commit="$(git -C "$SERVER_VAULT" rev-parse HEAD)"
  git clone --branch main --single-branch "$PRIVATE_REMOTE_URL" "$restore_root/vault"
  test "$(git -C "$restore_root/vault" rev-parse HEAD)" = "$expected_commit"
  memory validate --strict --vault "$restore_root/vault"
)
```

A live restore must first preserve and review the current vault and its Git
history. Never overwrite the live vault blindly; restore into a separate path,
compare histories and content, then perform an explicitly reviewed replacement.

## Milestone 8 gate evidence

Capture sanitized command output and exit codes for:

```bash
memory validate --strict --vault "$SERVER_VAULT"
memory doctor --vault "$SERVER_VAULT"
git -C "$SERVER_VAULT" status --short
git -C "$SERVER_VAULT" log -3 --oneline
systemctl --user is-enabled syncthing.service
systemctl --user is-active syncthing.service
```

Also retain sanitized notes or screenshots showing both devices **Up to Date**,
the nine Bases views plus verification/staleness columns or filters, the
server-created concept appearing without Obsidian restart, the computer
correction reaching the server and reconciling, outage write success,
propagation min/median/max, conflict handling, a tested private-backup clone,
and the manually pushed commit hash. Do not mark the gate complete until every
item is observed live.
