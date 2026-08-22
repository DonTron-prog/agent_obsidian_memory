import assert from "node:assert/strict";
import { execFile, spawn } from "node:child_process";
import { chmod, mkdir, mkdtemp, readFile, readdir, rm, stat, symlink, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { promisify } from "node:util";
import test from "node:test";

import agentMemory, { PI_COMPAT_VERSION } from "../index.ts";

const execFileAsync = promisify(execFile);
const here = dirname(new URL(import.meta.url).pathname);
const repo = resolve(here, "../../..");

type Handler = (event: any, context: any) => Promise<any> | any;

async function fixture(t: any) {
  const root = await mkdtemp(join(tmpdir(), "agent-memory-pi-test-"));
  t.after(() => rm(root, { recursive: true, force: true }));
  const vault = join(root, "vault");
  const state = join(root, "lifecycle");
  const initialized = await execFileAsync(
    "uv",
    ["run", "--project", repo, "memory", "init", "--vault", vault, "--json"],
    { cwd: repo },
  );
  assert.equal(JSON.parse(initialized.stdout).vault, vault);
  const config = join(vault, "system/memory.yaml");
  const text = await readFile(config, "utf8");
  await writeFile(
    config,
    text.replace("state_dir: ~/.local/state/agent-memory/lifecycle", `state_dir: ${state}`),
  );
  const wrapper = join(root, "memory");
  await writeFile(wrapper, `#!/bin/sh\nexec uv run --project '${repo}' memory "$@"\n`);
  await chmod(wrapper, 0o700);
  return { root, vault, state, config, wrapper };
}

function harness(wrapper: string, config: string, trace: string[][] = []) {
  process.env.AGENT_MEMORY_CLI = wrapper;
  process.env.AGENT_MEMORY_CONFIG = config;
  const handlers = new Map<string, Handler>();
  const calls: string[][] = [];
  const timeouts: Array<number | undefined> = [];
  let activeCommands = 0;
  const pi = {
    on(name: string, handler: Handler) {
      handlers.set(name, handler);
    },
    async exec(command: string, args: string[], options: { timeout?: number }) {
      calls.push(args);
      trace.push(args);
      timeouts.push(options.timeout);
      activeCommands += 1;
      try {
        const result = await execFileAsync(command, args, {
          timeout: options.timeout,
          env: { ...process.env, AGENT_MEMORY_CONFIG: config, AGENT_MEMORY_CLI: wrapper },
        });
        return { stdout: result.stdout, stderr: result.stderr, code: 0, killed: false };
      } catch (error: any) {
        return {
          stdout: error.stdout || "",
          stderr: error.stderr || "",
          code: typeof error.code === "number" ? error.code : 1,
          killed: Boolean(error.killed),
        };
      } finally {
        activeCommands -= 1;
      }
    },
  };
  agentMemory(pi as any);
  return { handlers, calls, timeouts, isIdle: () => activeCommands === 0 };
}

function context(id: string, entries: any[], notices: string[], provider = "openai", model = "one") {
  const value: any = {
    model: { provider, id: model },
    sessionManager: {
      getSessionId: () => id,
      getHeader: () => ({ id, timestamp: "2026-01-02T03:04:05Z" }),
      getEntries: () => entries,
    },
    ui: { notify: (message: string) => notices.push(message) },
  };
  return value;
}

async function emit(harnessValue: ReturnType<typeof harness>, name: string, event: any, ctx: any) {
  const handler = harnessValue.handlers.get(name);
  assert.ok(handler, `missing ${name} handler`);
  return handler(event, ctx);
}

function persistMessage(entries: any[], result: any) {
  if (!result?.message) return;
  entries.push({ type: "custom_message", ...result.message });
}

async function jsonCount(directory: string): Promise<number> {
  try {
    return (await readdir(directory)).filter((name) => name.endsWith(".json")).length;
  } catch {
    return 0;
  }
}

async function descriptors(directory: string): Promise<any[]> {
  const names = (await readdir(directory)).filter((name) => name.endsWith(".json"));
  return Promise.all(names.map(async (name) => JSON.parse(await readFile(join(directory, name), "utf8"))));
}

async function waitFor(check: () => boolean | Promise<boolean>, timeoutMs: number): Promise<void> {
  const deadline = Date.now() + timeoutMs;
  while (!(await check())) {
    if (Date.now() >= deadline) throw new Error(`condition not met within ${timeoutMs}ms`);
    await new Promise<void>((resolveValue) => setImmediate(resolveValue));
  }
}

const compactionChild = `
import assert from "node:assert/strict";
import { execFile } from "node:child_process";
import { promisify } from "node:util";
const execFileAsync = promisify(execFile);
const { default: agentMemory } = await import(process.env.ADAPTER_MODULE);
const handlers = new Map();
agentMemory({
  on(name, handler) { handlers.set(name, handler); },
  async exec(command, args, options) {
    try {
      const result = await execFileAsync(command, args, { timeout: options.timeout, env: process.env });
      return { stdout: result.stdout, stderr: result.stderr, code: 0, killed: false };
    } catch (error) {
      return { stdout: error.stdout || "", stderr: error.stderr || "", code: typeof error.code === "number" ? error.code : 1, killed: Boolean(error.killed) };
    }
  },
});
if (process.env.INVOKE_HANDLER === "1") {
  const handler = handlers.get("session_compact");
  assert.ok(handler);
  await handler({ compactionEntry: { id: "child-entry", summary: "Published before child exit.", timestamp: "2026-01-02T04:00:00Z" } }, {
    model: { provider: "openai", id: "child-model" },
    sessionManager: {
      getSessionId: () => "child-session",
      getHeader: () => ({ timestamp: "2026-01-02T03:04:05Z" }),
      getEntries: () => [],
    },
    ui: { notify() {} },
  });
}
`;

async function runCompactionChild(files: Awaited<ReturnType<typeof fixture>>, invoke: boolean) {
  return execFileAsync(process.execPath, ["--input-type=module", "--eval", compactionChild], {
    timeout: 10_000,
    env: {
      ...process.env,
      ADAPTER_MODULE: new URL("../index.ts", import.meta.url).href,
      INVOKE_HANDLER: invoke ? "1" : "0",
      AGENT_MEMORY_CLI: files.wrapper,
      AGENT_MEMORY_CONFIG: files.config,
    },
  });
}

async function runRealPiLifecycle(files: Awaited<ReturnType<typeof fixture>>) {
  const agentDir = join(files.root, "pi-agent");
  await mkdir(join(agentDir, "extensions"), { recursive: true });
  await symlink(resolve(here, ".."), join(agentDir, "extensions/agent-memory"));
  const args = [
    "--mode", "rpc",
    "--no-session",
    "--offline",
    "--no-tools",
    "--no-skills",
    "--no-context-files",
    "--model", "openai/gpt-4o-mini",
  ];
  assert.equal(args.includes("-e") || args.includes("--extension"), false);

  return new Promise<{ stdout: string; stderr: string; code: number | null }>((resolveValue, reject) => {
    const child = spawn("pi", args, {
      cwd: repo,
      env: {
        ...process.env,
        PI_CODING_AGENT_DIR: agentDir,
        AGENT_MEMORY_CLI: files.wrapper,
        AGENT_MEMORY_CONFIG: files.config,
      },
    });
    let stdout = "";
    let stderr = "";
    let inputClosed = false;
    const timeout = setTimeout(() => {
      child.kill("SIGKILL");
      reject(new Error(`Pi RPC lifecycle timed out: ${stderr}`));
    }, 15_000);
    child.stdout.setEncoding("utf8");
    child.stderr.setEncoding("utf8");
    child.stdout.on("data", (chunk) => {
      stdout += chunk;
      if (!inputClosed && stdout.split("\n").slice(0, -1).some((line) => {
        if (!line) return false;
        const value = JSON.parse(line);
        return value.type === "response" && value.id === "state" && value.success === true;
      })) {
        inputClosed = true;
        child.stdin.end();
      }
    });
    child.stderr.on("data", (chunk) => { stderr += chunk; });
    child.on("error", (error) => {
      clearTimeout(timeout);
      reject(error);
    });
    child.on("close", (code) => {
      clearTimeout(timeout);
      resolveValue({ stdout, stderr, code });
    });
    child.stdin.write('{"id":"state","type":"get_state"}\n');
  });
}

test("0.84.2 lifecycle contract is durable, idempotent, model-exact, and reload-safe", async (t) => {
  const files = await fixture(t);
  process.env.AGENT_MEMORY_CLI = files.wrapper;
  process.env.AGENT_MEMORY_CONFIG = files.config;
  const oldEntries: any[] = [];
  const notices: string[] = [];
  const trace: string[][] = [];
  const old = harness(files.wrapper, files.config, trace);
  const oldContext = context("old-session", oldEntries, notices, "provider-a", "model-a");

  await emit(old, "session_start", { reason: "startup" }, oldContext);
  persistMessage(
    oldEntries,
    await emit(old, "before_agent_start", { prompt: "never serialized" }, oldContext),
  );
  assert.equal(oldEntries.length, 1);
  assert.equal(oldEntries[0].display, true);
  assert.match(oldEntries[0].content, /# Agent Memory/);
  assert.equal(
    await emit(old, "before_agent_start", { prompt: "also never serialized" }, oldContext),
    undefined,
  );
  assert.equal((await readdir(join(files.state, "audit"))).length, 1);
  const spool = await readFile(join(files.state, "audit", (await readdir(join(files.state, "audit")))[0]), "utf8");
  assert.equal(spool.trim().split("\n").length, 1);
  assert.match(spool, /"mode":"injected"/);
  assert.match(spool, /"model":"provider-a\/model-a"/);
  assert.match(spool, /memory\/index\.md/);

  const beforeReload = await jsonCount(join(files.state, "ready"));
  await emit(old, "session_shutdown", { reason: "reload" }, oldContext);
  await emit(old, "session_start", { reason: "reload" }, oldContext);
  assert.equal(await jsonCount(join(files.state, "ready")), beforeReload);

  const first = {
    compactionEntry: {
      id: "saved-entry-one",
      summary: "Native summary one.",
      timestamp: "2026-01-02T04:00:00Z",
    },
  };
  await emit(old, "session_compact", first, oldContext);
  await emit(old, "session_compact", first, oldContext);
  oldContext.model = { provider: "provider-b", id: "model-b" };
  await emit(
    old,
    "session_compact",
    {
      compactionEntry: {
        id: "saved-entry-two",
        summary: "Native summary two.",
        timestamp: "2026-01-02T05:00:00Z",
      },
    },
    oldContext,
  );
  await emit(old, "session_shutdown", { reason: "new" }, oldContext);
  assert.equal(await jsonCount(join(files.state, "ready")), 4);

  const newEntries: any[] = [];
  const replacement = harness(files.wrapper, files.config, trace);
  const newContext = context("new-session", newEntries, notices, "provider-c", "model-c");
  await emit(replacement, "session_start", { reason: "new" }, newContext);
  persistMessage(
    newEntries,
    await emit(replacement, "before_agent_start", { prompt: "not passed" }, newContext),
  );
  assert.equal(newEntries.length, 1);
  await emit(replacement, "session_shutdown", { reason: "quit" }, newContext);
  assert.equal(await jsonCount(join(files.state, "ready")), 6);
  const queued = await descriptors(join(files.state, "ready"));
  const finalization = queued.find(
    (value) => value.event_kind === "finalize" && value.session.session_id === "new-session",
  );
  assert.ok(finalization);
  const auditNames = await readdir(join(files.state, "audit"));
  const auditPayloads = await Promise.all(
    auditNames.map(async (name) => ({ name, text: await readFile(join(files.state, "audit", name), "utf8") })),
  );
  const pendingAudit = auditPayloads.find(({ text }) => text.includes('"session_id":"new-session"'));
  assert.ok(pendingAudit);
  assert.equal(
    finalization.audit_through_offset,
    (await stat(join(files.state, "audit", pendingAudit.name))).size,
  );
  assert.ok([...old.timeouts, ...replacement.timeouts].every((timeout) => timeout === 1500));
  const finalizeCall = trace.findIndex((args) => args.includes("finalize"));
  const newInjectCall = trace.findIndex(
    (args, index) => index > finalizeCall && args.includes("inject"),
  );
  assert.ok(finalizeCall >= 0 && newInjectCall > finalizeCall);

  const drained = await execFileAsync(files.wrapper, ["--config", files.config, "worker", "--once", "--json"]);
  assert.equal(JSON.parse(drained.stdout).failed, 0);
  const summary = await readFile(join(files.vault, "sessions/pi/2026/old-session.md"), "utf8");
  assert.equal((summary.match(/<!-- lifecycle-event:/g) || []).length, 3);
  assert.equal((summary.match(/saved-entry-one/g) || []).length, 2);
  assert.match(summary, /Native summary one\./);
  assert.match(summary, /Native summary two\./);
  assert.match(summary, /provider-a\/model-a/);
  assert.match(summary, /provider-b\/model-b/);
  assert.match(summary, /\| injected \| new session \| memory\/index\.md \| provider-a\/model-a \|/);
  assert.doesNotMatch(summary, /\.jsonl|agent-memory-pi-test/);
  const replacementSummary = await readFile(
    join(files.vault, "sessions/pi/2026/new-session.md"),
    "utf8",
  );
  assert.match(replacementSummary, /status: closed/);
  assert.match(replacementSummary, /\| injected \| new session \| memory\/index\.md \| provider-c\/model-c \|/);
  await waitFor(() => old.isIdle() && replacement.isIdle(), 4_000);
});

test("new, resume, fork, and quit finalize while reload is ignored", async () => {
  const handlers = new Map<string, Handler>();
  const calls: string[][] = [];
  const pi = {
    on: (name: string, handler: Handler) => handlers.set(name, handler),
    exec: async (_command: string, args: string[]) => {
      calls.push(args);
      return { stdout: "{}", stderr: "", code: 0, killed: false };
    },
  };
  agentMemory(pi as any);
  const handler = handlers.get("session_shutdown");
  assert.ok(handler);
  const ctx = context("shutdown-session", [], []);
  for (const reason of ["new", "resume", "fork", "quit", "reload"]) {
    await handler({ reason }, ctx);
  }
  assert.equal(calls.length, 4);
  for (const reason of ["new", "resume", "fork", "quit"]) {
    assert.ok(calls.some((args) => args.includes(`pi-shutdown-${reason}-empty`)));
  }
});

test("publication failure never rejects the native action and is bounded in the TUI", async () => {
  const handlers = new Map<string, Handler>();
  const notices: string[] = [];
  const pi = {
    on: (name: string, handler: Handler) => handlers.set(name, handler),
    exec: async () => ({ stdout: "", stderr: "api_key=must-not-appear", code: 2, killed: false }),
  };
  agentMemory(pi as any);
  const ctx = context("failure-session", [], notices);
  const handler = handlers.get("session_compact");
  assert.ok(handler);
  await assert.doesNotReject(() =>
    handler(
      {
        compactionEntry: {
          id: "failed-entry",
          summary: "Native action still succeeds.",
          timestamp: "2026-01-02T04:00:00Z",
        },
      },
      ctx,
    ),
  );
  assert.match(notices[0], /exit code 2/);
  assert.doesNotMatch(notices.join("\n"), /must-not-appear|api_key/);
});

test("materialization failure after native compaction cannot cancel the completed Pi action", async (t) => {
  const files = await fixture(t);
  const value = harness(files.wrapper, files.config);
  const ctx = context("materialization-failure", [], []);
  let nativeActionCompleted = false;
  const nativeEvent = {
    compactionEntry: {
      id: "materialization-entry",
      summary: "Publication succeeds before worker materialization.",
      timestamp: "2026-01-02T04:00:00Z",
    },
  };
  nativeActionCompleted = true;

  await assert.doesNotReject(() => emit(value, "session_compact", nativeEvent, ctx));
  assert.equal(await jsonCount(join(files.state, "ready")), 1);

  const target = join(files.vault, "sessions/pi/2026/materialization-failure.md");
  await mkdir(dirname(target), { recursive: true });
  await writeFile(target, `---\nagent: pi\nsession_id: wrong-session\nstarted_at: 2026-01-02T03:04:05Z\nstatus: active\n---\n# Wrong session\n`);
  let workerResult: any;
  try {
    await execFileAsync(files.wrapper, ["--config", files.config, "worker", "--once", "--json"]);
    assert.fail("worker should report materialization failure");
  } catch (error: any) {
    workerResult = JSON.parse(error.stdout);
  }
  assert.deepEqual(workerResult, { processed: 0, failed: 1, noop: 0 });
  assert.equal(nativeActionCompleted, true);
  assert.equal(await jsonCount(join(files.state, "ready")), 0);
  assert.equal(await jsonCount(join(files.state, "claimed")), 0);
  const failed = await descriptors(join(files.state, "failed"));
  assert.equal(failed.length, 1);
  assert.equal(failed[0].descriptor.summary_source.summary, "Publication succeeds before worker materialization.");
  assert.equal(failed[0].retry_state, "blocked");
});

test("completed child compaction publication survives exit but a child without the handler does not", async (t) => {
  const files = await fixture(t);
  const completed = await runCompactionChild(files, true);
  assert.equal(completed.stderr, "");
  assert.equal(await jsonCount(join(files.state, "ready")), 1);

  const recovered = await execFileAsync(
    files.wrapper,
    ["--config", files.config, "worker", "--once", "--json"],
  );
  assert.deepEqual(JSON.parse(recovered.stdout), { processed: 1, failed: 0, noop: 0 });
  const summaryPath = join(files.vault, "sessions/pi/2026/child-session.md");
  const recoveredSummary = await readFile(summaryPath, "utf8");
  assert.match(recoveredSummary, /Published before child exit\./);

  const noHandler = await runCompactionChild(files, false);
  assert.equal(noHandler.stderr, "");
  assert.equal(await jsonCount(join(files.state, "ready")), 0);
  const noRecovery = await execFileAsync(
    files.wrapper,
    ["--config", files.config, "worker", "--once", "--json"],
  );
  assert.deepEqual(JSON.parse(noRecovery.stdout), { processed: 0, failed: 0, noop: 0 });
  assert.equal(await readFile(summaryPath, "utf8"), recoveredSummary);
});

test("persistent worker notifications are shown and then acknowledged", async (t) => {
  const files = await fixture(t);
  const directory = join(files.state, "notifications");
  await mkdir(directory, { recursive: true });
  await writeFile(
    join(directory, "retry-one.json"),
    JSON.stringify({
      schema: "agent-memory.notification/v1",
      retry_id: "retry-one",
      agent: "pi",
      session_id: "old-session",
      severity: "error",
      message: "retryable worker failure",
      created_at: "2026-01-02T03:04:05Z",
    }),
    { mode: 0o600 },
  );
  const notices: string[] = [];
  const value = harness(files.wrapper, files.config);
  await emit(value, "session_start", { reason: "startup" }, context("current", [], notices));
  await waitFor(
    async () => notices.some((message) => message.includes("retryable worker failure"))
      && await jsonCount(directory) === 0,
    4_000,
  );
  assert.ok(value.calls.some((args) => args.includes("--ack") && args.includes("retry-one")));
  assert.ok(value.timeouts.every((timeout) => timeout === 1500));
  await waitFor(value.isIdle, 4_000);
});

test("Pi 0.84.2 global auto-discovery emits a real RPC startup and quit lifecycle", async (t) => {
  const files = await fixture(t);
  let result;
  try {
    const version = await execFileAsync("pi", ["--version"], { timeout: 5_000 });
    assert.equal(version.stdout.trim(), PI_COMPAT_VERSION);
    result = await runRealPiLifecycle(files);
  } catch (error: any) {
    if (error.code === "ENOENT") {
      t.skip("deployed Pi executable is unavailable");
      return;
    }
    throw error;
  }
  assert.equal(result.code, 0, result.stderr);
  assert.equal(result.stderr, "");
  const rpc = result.stdout.split("\n").filter(Boolean).map((line) => JSON.parse(line));
  const state = rpc.find((value) => value.type === "response" && value.id === "state");
  assert.equal(rpc.some((value) => value.type === "agent_start"), false);
  assert.equal(state.data.model.provider, "openai");
  assert.equal(state.data.model.id, "gpt-4o-mini");

  const queued = await descriptors(join(files.state, "ready"));
  assert.deepEqual(queued.map((value) => value.event_kind).sort(), ["finalize", "session_start"]);
  assert.ok(queued.some((value) => value.lifecycle.native_event_id.startsWith("pi-shutdown-quit-")));
  assert.equal(new Set(queued.map((value) => value.session.session_id)).size, 1);
  const sessionId = queued[0].session.session_id;
  const sessionYear = queued[0].session.started_at.slice(0, 4);
  const drained = await execFileAsync(
    files.wrapper,
    ["--config", files.config, "worker", "--once", "--json"],
  );
  assert.deepEqual(JSON.parse(drained.stdout), { processed: 2, failed: 0, noop: 0 });
  const summary = await readFile(join(files.vault, `sessions/pi/${sessionYear}/${sessionId}.md`), "utf8");
  assert.match(summary, /status: closed/);
  assert.match(summary, /openai\/gpt-4o-mini/);
});

test("source contains no Pi raw-session reader", async () => {
  const source = await readFile(resolve(here, "../index.ts"), "utf8");
  assert.doesNotMatch(source, /getSessionFile|\.jsonl|readFile/);
  assert.equal(PI_COMPAT_VERSION, "0.84.2");
});
