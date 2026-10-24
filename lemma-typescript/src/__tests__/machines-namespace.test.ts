import { afterEach, describe, expect, it, vi } from "vitest";

import { LemmaClient } from "../client.js";
import type { HttpClient } from "../http.js";
import {
  MachineConsentRequiredError,
  MachinesNamespace,
  machinePathRef,
  type MachineProcessEvent,
  type WebSocketFactory,
} from "../namespaces/machines.js";
import { WorkspaceNamespace } from "../namespaces/workspace.js";

interface Asked {
  method: string;
  path: string;
  options: { params?: Record<string, unknown>; body?: unknown };
}

/** An HttpClient that answers from `answers`, keyed by "METHOD path". */
function fakeHttp(answers: Record<string, unknown | ((asked: Asked) => unknown)> = {}) {
  const asked: Asked[] = [];
  const http = {
    getBaseUrl: () => "https://api.example.test",
    async request(method: string, path: string, options: Asked["options"] = {}) {
      const entry = { method, path, options };
      asked.push(entry);
      const answer = answers[`${method} ${path}`];
      return typeof answer === "function" ? (answer as (a: Asked) => unknown)(entry) : answer;
    },
  } as unknown as HttpClient;
  return { http, asked };
}

afterEach(() => vi.restoreAllMocks());

describe("machine refs in paths", () => {
  it("writes a pod machine's display form with a colon, each part escaped", () => {
    expect(machinePathRef("pod:abc-123/build box")).toBe("pod:abc-123:build%20box");
    expect(machinePathRef("pod:abc-123:build")).toBe("pod:abc-123:build");
    expect(machinePathRef("mine:laptop")).toBe("mine:laptop");
    expect(machinePathRef("me")).toBe("me");
    expect(machinePathRef("a/b")).toBe("a%2Fb");
  });

  it("calls the route for the encoded ref", async () => {
    const { http, asked } = fakeHttp({ "POST /machines/pod:p1:ci:restart": undefined });
    await new MachinesNamespace(http).restart("pod:p1/ci");
    expect(asked[0]).toMatchObject({ method: "POST", path: "/machines/pod:p1:ci:restart" });
  });

  it("lists a pod's machines by pod id", async () => {
    const { http, asked } = fakeHttp({ "GET /machines": { machines: [{ ref: "pod:p1/ci" }] } });
    const machines = await new MachinesNamespace(http).list({ podId: "p1" });
    expect(machines).toEqual([{ ref: "pod:p1/ci" }]);
    expect(asked[0].options.params).toEqual({ pod_id: "p1" });
  });
});

describe("the deprecated workspace alias", () => {
  it("asks /machines/me/startup for status", async () => {
    const { http, asked } = fakeHttp({
      "GET /machines/me/startup": { state: "downloading", done_mb: 10, total_mb: 40 },
    });
    const status = await new WorkspaceNamespace(http).status();
    expect(asked[0].path).toBe("/machines/me/startup");
    expect(status).toEqual({ state: "downloading", detail: null, done_mb: 10, total_mb: 40 });
  });

  it("keeps browserAccess's old answer, `app` included", async () => {
    const { http, asked } = fakeHttp({
      "POST /machines/me/browser:access": { url: "https://view.test/x", expires_at: "2026-10-22T00:00:00Z" },
    });
    const access = await new WorkspaceNamespace(http).browserAccess(60);
    expect(asked[0].options.body).toEqual({ ttl_seconds: 60 });
    expect(access).toEqual({ app: "browser", url: "https://view.test/x", expires_at: "2026-10-22T00:00:00Z" });
  });

  it("routes the browser and file calls to the default machine", async () => {
    const { http, asked } = fakeHttp({
      "GET /machines/me/browser": { state: "stopped" },
      "POST /machines/me/browser:display": { size: "800x600" },
      "GET /machines/me/browser/current-page-url": { url: null },
      "GET /machines/me/files": { path: "/home/user", entries: [] },
      "GET /machines/me/files:stat": { path: "/home/user/a" },
    });
    const workspace = new WorkspaceNamespace(http);
    expect(await workspace.browserStatus()).toEqual({ state: "stopped", detail: null });
    await workspace.browserResizeDisplay(800, 600);
    await workspace.browserCurrentPageUrl("https://site.test");
    await workspace.listFiles({ path: "/home/user", after: "b" });
    await workspace.statFile("/home/user/a");
    expect(asked.map((a) => `${a.method} ${a.path}`)).toEqual([
      "GET /machines/me/browser",
      "POST /machines/me/browser:display",
      "GET /machines/me/browser/current-page-url",
      "GET /machines/me/files",
      "GET /machines/me/files:stat",
    ]);
    expect(asked[1].options.body).toEqual({ width: 800, height: 600 });
    expect(asked[3].options.params).toEqual({ path: "/home/user", wake: undefined, after: "b" });
  });
});

describe("consent", () => {
  it("throws MachineConsentRequiredError with the page that asks", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(
        JSON.stringify({
          code: "MACHINE_CONSENT_REQUIRED",
          message: "This app needs your permission to use your machine.",
          details: { app_id: "app-42" },
        }),
        { status: 403, headers: { "content-type": "application/json" } },
      ),
    );
    const client = new LemmaClient({
      apiUrl: "https://api.example.test",
      authUrl: "https://lemma.example.test/auth",
      token: "t",
      maxRetries: 0,
    });

    const failure = await client.machines.me().catch((error: unknown) => error);

    expect(failure).toBeInstanceOf(MachineConsentRequiredError);
    const consent = failure as MachineConsentRequiredError;
    expect(consent.appId).toBe("app-42");
    expect(consent.statusCode).toBe(403);
    expect(consent.consentUrl).toBe("https://lemma.example.test/consent/machine?app=app-42");
  });

  it("leaves other refusals as they were", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ code: "MACHINE_NOT_FOUND", message: "no" }), {
        status: 404,
        headers: { "content-type": "application/json" },
      }),
    );
    const client = new LemmaClient({ apiUrl: "https://api.example.test", token: "t", maxRetries: 0 });
    const failure = await client.machines.get("mine:gone").catch((error: unknown) => error);
    expect(failure).not.toBeInstanceOf(MachineConsentRequiredError);
    expect((failure as { statusCode: number }).statusCode).toBe(404);
  });
});

interface ScriptedSocket {
  sent: string[];
  emit(message: unknown): void;
  close(): void;
}

/** A socket that runs `script` once it is open. */
function fakeSocket(script: (socket: ScriptedSocket) => void) {
  const opened: FakeSocket[] = [];
  class FakeSocket {
    readyState = 0;
    sent: string[] = [];
    onmessage: ((event: { data: string }) => void) | null = null;
    onclose: (() => void) | null = null;
    onerror: (() => void) | null = null;
    constructor(readonly url: string) {
      opened.push(this);
      queueMicrotask(() => {
        this.readyState = 1;
        script(this);
      });
    }
    emit(message: unknown) {
      this.onmessage?.({ data: JSON.stringify(message) });
    }
    send(data: string) {
      this.sent.push(data);
    }
    close() {
      this.readyState = 3;
      this.onclose?.();
    }
  }
  return { factory: FakeSocket as unknown as WebSocketFactory, opened };
}

async function collect(events: AsyncIterable<MachineProcessEvent>): Promise<MachineProcessEvent[]> {
  const seen: MachineProcessEvent[] = [];
  for await (const event of events) seen.push(event);
  return seen;
}

describe("exec", () => {
  const started = { id: "proc-1", command: "ls", cwd: "/home/user", state: "running" };

  it("streams output and the exit over the process socket", async () => {
    const { http, asked } = fakeHttp({ "POST /machines/me/processes": started });
    const { factory, opened } = fakeSocket((socket) => {
      socket.emit({ type: "output", sequence: 1, channel: "stdout", data: "a\n" });
      socket.emit({ type: "output", sequence: 2, channel: "stderr", data: "b\n" });
      socket.emit({ type: "exit", state: "succeeded", exit_code: 0 });
    });
    const machines = new MachinesNamespace(http, { WebSocket: factory, bearerToken: () => "tok" });

    const run = await machines.exec("me", { command: "ls", timeoutSeconds: 5, tty: { cols: 80, rows: 24 } });
    const events = await collect(run);

    expect(run.id).toBe("proc-1");
    expect(asked[0].options.body).toMatchObject({
      command: "ls",
      timeout_seconds: 5,
      tty: { cols: 80, rows: 24 },
    });
    expect(opened[0].url).toBe(
      "wss://api.example.test/machines/me/processes/proc-1/stream?access_token=tok",
    );
    expect(events).toEqual([
      { type: "output", sequence: 1, channel: "stdout", data: "a\n" },
      { type: "output", sequence: 2, channel: "stderr", data: "b\n" },
      { type: "exit", state: "succeeded", exit_code: 0 },
    ]);
  });

  it("carries on by polling from the last sequence when the socket drops", async () => {
    const { http, asked } = fakeHttp({
      "POST /machines/pod:p1:ci/processes": started,
      "GET /machines/pod:p1:ci/processes/proc-1/output": ({ options }: Asked) =>
        options.params?.after === 1
          ? { chunks: [{ sequence: 2, channel: "stdout", data: "two" }], next_sequence: 2, state: "failed", exit_code: 3 }
          : { chunks: [], next_sequence: 0, state: "running" },
    });
    const { factory } = fakeSocket((socket) => {
      socket.emit({ type: "output", sequence: 1, channel: "stdout", data: "one" });
      socket.close();
    });
    const machines = new MachinesNamespace(http, { WebSocket: factory });

    const events = await collect(await machines.exec("pod:p1/ci", { command: "make" }));

    expect(events.map((e) => (e.type === "output" ? e.data : `exit ${e.exit_code}`))).toEqual([
      "one",
      "two",
      "exit 3",
    ]);
    expect(asked.at(-1)?.options.params).toEqual({ after: 1, wait_seconds: 20 });
  });

  it("polls when there is no WebSocket at all", async () => {
    let page = 0;
    const { http } = fakeHttp({
      "POST /machines/me/processes": started,
      "GET /machines/me/processes/proc-1/output": () =>
        ++page === 1
          ? { chunks: [{ sequence: 1, channel: "pty", data: "$ " }], next_sequence: 1, state: "running" }
          : { chunks: [], next_sequence: 1, state: "cancelled", exit_code: null },
    });
    const machines = new MachinesNamespace(http, { WebSocket: null });

    const run = await machines.exec("me", { command: "bash" });

    expect(await collect(run)).toEqual([
      { type: "output", sequence: 1, channel: "pty", data: "$ " },
      { type: "exit", state: "cancelled", exit_code: null },
    ]);
  });

  it("types into the socket while it is open, and by request otherwise", async () => {
    const { http, asked } = fakeHttp({ "POST /machines/me/processes": started });
    let socketRef: ScriptedSocket | null = null;
    const { factory } = fakeSocket((socket) => {
      socketRef = socket;
    });
    const machines = new MachinesNamespace(http, { WebSocket: factory });
    const run = await machines.exec("me", { command: "bash" });

    await run.input("before");
    expect(asked.at(-1)).toMatchObject({ path: "/machines/me/processes/proc-1:input", options: { body: { data: "before" } } });

    const iterator = run[Symbol.asyncIterator]();
    const pending = iterator.next();
    await new Promise((resolve) => setTimeout(resolve, 0));
    await run.input("ls\n");
    await run.resize(100, 30);
    expect(socketRef!.sent.map((s) => JSON.parse(s))).toEqual([
      { type: "input", data: "ls\n" },
      { type: "resize", cols: 100, rows: 30 },
    ]);
    socketRef!.emit({ type: "exit", state: "succeeded", exit_code: 0 });
    expect((await pending).value).toEqual({ type: "exit", state: "succeeded", exit_code: 0 });
  });
});
