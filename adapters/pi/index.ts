import { mkdtemp, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import type {
  ExtensionAPI,
  ExtensionContext,
  SessionCompactEvent,
  SessionShutdownEvent,
} from "@earendil-works/pi-coding-agent";

export const PI_COMPAT_VERSION = "0.84.2";
const CUSTOM_TYPE = "agent-memory-root-index";
const COMMAND_TIMEOUT_MS = 1500;
const MAX_JSON_BYTES = 1024 * 1024;

type JsonObject = Record<string, unknown>;

class CommandFailure extends Error {
  code: number;
  killed: boolean;

  constructor(code: number, killed: boolean) {
    super(killed ? "command timed out" : `exit code ${code}`);
    this.code = code;
    this.killed = killed;
  }
}

function locationArgs(): string[] {
  const args: string[] = [];
  if (process.env.AGENT_MEMORY_CONFIG) args.push("--config", process.env.AGENT_MEMORY_CONFIG);
  if (process.env.AGENT_MEMORY_VAULT) args.push("--vault", process.env.AGENT_MEMORY_VAULT);
  return args;
}

function model(ctx: ExtensionContext): string | undefined {
  return ctx.model ? `${ctx.model.provider}/${ctx.model.id}` : undefined;
}

function session(ctx: ExtensionContext): { id: string; startedAt: string } {
  const header = ctx.sessionManager.getHeader();
  return {
    id: ctx.sessionManager.getSessionId(),
    startedAt: header?.timestamp ?? new Date().toISOString(),
  };
}

function alreadyInjected(ctx: ExtensionContext, sessionId: string): boolean {
  return ctx.sessionManager.getEntries().some((entry) => {
    if (entry.type !== "custom_message" || entry.customType !== CUSTOM_TYPE) return false;
    const details = entry.details;
    return (
      typeof details === "object" &&
      details !== null &&
      (details as JsonObject).sessionId === sessionId
    );
  });
}

export default function agentMemory(pi: ExtensionAPI): void {
  const executable = process.env.AGENT_MEMORY_CLI || "memory";
  let checkingNotifications = false;

  async function runJson(args: string[]): Promise<JsonObject> {
    const result = await pi.exec(executable, [...locationArgs(), ...args, "--json"], {
      timeout: COMMAND_TIMEOUT_MS,
    });
    if (result.code !== 0 || result.killed) throw new CommandFailure(result.code, result.killed);
    if (Buffer.byteLength(result.stdout) > MAX_JSON_BYTES) throw new Error("JSON output too large");
    const value: unknown = JSON.parse(result.stdout);
    if (typeof value !== "object" || value === null || Array.isArray(value)) {
      throw new Error("invalid JSON response");
    }
    return value as JsonObject;
  }

  function notifyFailure(ctx: ExtensionContext, operation: string, error: unknown): void {
    const detail = error instanceof CommandFailure ? error.message : "invalid command response";
    ctx.ui.notify(`Agent memory ${operation} failed (${detail}).`, "error");
  }

  async function showPendingNotifications(ctx: ExtensionContext): Promise<void> {
    if (checkingNotifications) return;
    checkingNotifications = true;
    try {
      const response = await runJson(["session", "notifications", "--agent", "pi"]);
      const values = Array.isArray(response.notifications) ? response.notifications : [];
      const acknowledged: string[] = [];
      for (const item of values) {
        if (typeof item !== "object" || item === null) continue;
        const notification = item as JsonObject;
        if (typeof notification.retry_id !== "string" || typeof notification.message !== "string") {
          continue;
        }
        ctx.ui.notify(`Agent memory worker failure: ${notification.message.slice(0, 300)}`, "error");
        acknowledged.push(notification.retry_id);
      }
      if (acknowledged.length) {
        await runJson([
          "session",
          "notifications",
          "--agent",
          "pi",
          ...acknowledged.flatMap((id) => ["--ack", id]),
        ]);
      }
    } catch {
      // Persistent notification files remain for the next turn.
    } finally {
      checkingNotifications = false;
    }
  }

  async function publishStart(ctx: ExtensionContext): Promise<void> {
    const current = session(ctx);
    const currentModel = model(ctx);
    const args = [
      "session",
      "start",
      "--agent",
      "pi",
      "--agent-version",
      PI_COMPAT_VERSION,
      "--session-id",
      current.id,
      "--started-at",
      current.startedAt,
      "--occurred-at",
      new Date().toISOString(),
      "--native-event-id",
      "pi-session-start",
      "--native-store-ref",
      `pi-session:${current.id}`,
    ];
    if (currentModel) args.push("--model", currentModel);
    await runJson(args);
  }

  async function publishCompaction(event: SessionCompactEvent, ctx: ExtensionContext): Promise<void> {
    const current = session(ctx);
    const currentModel = model(ctx);
    const directory = await mkdtemp(join(tmpdir(), "agent-memory-pi-"));
    const summary = join(directory, "summary.md");
    try {
      await writeFile(summary, event.compactionEntry.summary, { encoding: "utf8", mode: 0o600, flag: "wx" });
      const args = [
        "session",
        "checkpoint",
        "--agent",
        "pi",
        "--agent-version",
        PI_COMPAT_VERSION,
        "--session-id",
        current.id,
        "--started-at",
        current.startedAt,
        "--occurred-at",
        event.compactionEntry.timestamp,
        "--native-event-id",
        event.compactionEntry.id,
        "--trigger",
        "compaction",
        "--summary-kind",
        "pi",
        "--compaction-entry-id",
        event.compactionEntry.id,
        "--native-summary-file",
        summary,
        "--native-store-ref",
        `pi-session:${current.id}`,
      ];
      if (currentModel) args.push("--model", currentModel);
      await runJson(args);
    } finally {
      await rm(directory, { recursive: true, force: true });
    }
  }

  async function publishFinalize(event: SessionShutdownEvent, ctx: ExtensionContext): Promise<void> {
    const current = session(ctx);
    const currentModel = model(ctx);
    const latestEntry = ctx.sessionManager.getEntries().at(-1)?.id || "empty";
    const args = [
      "session",
      "finalize",
      "--agent",
      "pi",
      "--agent-version",
      PI_COMPAT_VERSION,
      "--session-id",
      current.id,
      "--started-at",
      current.startedAt,
      "--occurred-at",
      new Date().toISOString(),
      "--native-event-id",
      `pi-shutdown-${event.reason}-${latestEntry}`,
      "--native-store-ref",
      `pi-session:${current.id}`,
    ];
    if (currentModel) args.push("--model", currentModel);
    await runJson(args);
  }

  pi.on("session_start", async (event, ctx) => {
    if (event.reason === "reload") return;
    try {
      await publishStart(ctx);
    } catch (error) {
      notifyFailure(ctx, "session start publication", error);
    }
    void showPendingNotifications(ctx);
  });

  pi.on("before_agent_start", async (_event, ctx) => {
    void showPendingNotifications(ctx);
    const current = session(ctx);
    if (alreadyInjected(ctx, current.id)) return;
    const currentModel = model(ctx);
    if (!currentModel) {
      ctx.ui.notify("Agent memory index injection failed (active model unavailable).", "error");
      return;
    }
    try {
      const response = await runJson([
        "session",
        "inject",
        "--session-id",
        current.id,
        "--agent",
        `pi/${PI_COMPAT_VERSION}`,
        "--model",
        currentModel,
      ]);
      if (typeof response.content !== "string") throw new Error("invalid index response");
      return {
        message: {
          customType: CUSTOM_TYPE,
          content: response.content,
          display: true,
          details: {
            sessionId: current.id,
            resource: "memory/index.md",
            adapterVersion: PI_COMPAT_VERSION,
          },
        },
      };
    } catch (error) {
      notifyFailure(ctx, "index injection", error);
    }
  });

  pi.on("session_compact", async (event, ctx) => {
    try {
      await publishCompaction(event, ctx);
    } catch (error) {
      notifyFailure(ctx, "compaction publication", error);
    }
  });

  pi.on("session_shutdown", async (event, ctx) => {
    if (event.reason === "reload") return;
    try {
      await publishFinalize(event, ctx);
    } catch (error) {
      notifyFailure(ctx, "session finalization publication", error);
    }
  });
}
