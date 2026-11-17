import { ApiError } from "../http.js";
import type { HttpClient } from "../http.js";
import type { BrowserAccessResponse } from "../openapi_client/models/BrowserAccessResponse.js";
import type { BrowserStatusResponse } from "../openapi_client/models/BrowserStatusResponse.js";
import type { CreatePodMachineRequest } from "../openapi_client/models/CreatePodMachineRequest.js";
import type { ForgetResponse } from "../openapi_client/models/ForgetResponse.js";
import type { MachineFileEntry } from "../openapi_client/models/MachineFileEntry.js";
import type { MachineFileListResponse } from "../openapi_client/models/MachineFileListResponse.js";
import type { MachineGrantsModel } from "../openapi_client/models/MachineGrantsModel.js";
import type { MachinePolicyResponse } from "../openapi_client/models/MachinePolicyResponse.js";
import type { MachineResponse } from "../openapi_client/models/MachineResponse.js";
import type { MachineStartupResponse } from "../openapi_client/models/MachineStartupResponse.js";
import type { NavigateResponse } from "../openapi_client/models/NavigateResponse.js";
import type { PreviewResponse } from "../openapi_client/models/PreviewResponse.js";
import type { ProcessOutputResponse } from "../openapi_client/models/ProcessOutputResponse.js";
import type { ProcessResponse } from "../openapi_client/models/ProcessResponse.js";
import type { ResetResponse } from "../openapi_client/models/ResetResponse.js";
import type { SyncResponse } from "../openapi_client/models/SyncResponse.js";
import type { UpdatePodMachineRequest } from "../openapi_client/models/UpdatePodMachineRequest.js";
import type { WebLoginListResponse } from "../openapi_client/models/WebLoginListResponse.js";

export type {
  BrowserAccessResponse as MachineBrowserAccess,
  BrowserStatusResponse as MachineBrowserStatus,
  MachineFileEntry,
  MachineFileListResponse,
  MachineGrantsModel as MachineGrants,
  MachinePolicyResponse as MachinePolicy,
  MachineResponse as Machine,
  MachineStartupResponse as MachineStartup,
  PreviewResponse as MachinePreview,
  ProcessOutputResponse as MachineProcessOutput,
  ProcessResponse as MachineProcess,
  ResetResponse as MachineResetResult,
};

/**
 * A machine ref as it goes in a URL path.
 *
 * A pod machine is `pod:<pod>:<name>`, the pod by id or by name: the form
 * that fits in one path segment. The older `pod:<pod>/<name>` is still
 * accepted and sent in the colon form. `pod:<name>` gains `podId` when one is
 * given, so a client bound to a pod can name its machines without repeating
 * it. Each part is escaped on its own, keeping the separating colons literal.
 */
export function machinePathRef(ref: string, podId?: string): string {
  const trimmed = ref.trim();
  if (!trimmed) throw new Error("A machine ref is required.");
  let pathForm = trimmed.replace(/^pod:([^/:]+)\/(.+)$/, "pod:$1:$2");
  if (podId && /^pod:[^:]+$/.test(pathForm)) {
    pathForm = `pod:${podId}:${pathForm.slice("pod:".length)}`;
  }
  return pathForm.split(":").map(encodeURIComponent).join(":");
}

/**
 * The most the server will return from one read, whatever is asked for.
 *
 * `_MAX_CONTENT_BYTES` in the files controller. Kept here so the chunked
 * reader clamps to it rather than discovering it a slice at a time.
 */
export const MAX_READ_BYTES = 8 * 1024 * 1024;

function rangeHeader(range: { start: number; end: number }): Record<string, string> {
  return { Range: `bytes=${range.start}-${range.end}` };
}

/**
 * Whether a response to `Range: bytes=<start>-…` is the slice asked for: a
 * 206, or a `Content-Range` that starts at `start`. A `Content-Range` that
 * starts anywhere else is a different slice, whatever the status.
 */
function rangeHonoured(status: number, contentRange: string | null, start: number): boolean {
  if (contentRange) {
    const match = /^bytes (\d+)-\d+\/(?:\d+|\*)$/.exec(contentRange.trim());
    return match !== null && Number(match[1]) === start;
  }
  return status === 206;
}

/** A process's states once it has stopped, by whatever means. */
const STOPPED_STATES = new Set(["succeeded", "failed", "cancelled", "timed_out"]);

/** One piece of what a process wrote, or how it ended. */
export type MachineProcessEvent =
  | { type: "output"; sequence: number; channel: "stdout" | "stderr" | "pty"; data: string }
  | { type: "exit"; state: string; exit_code: number | null };

export interface ExecOptions {
  command: string;
  cwd?: string;
  env?: Record<string, string>;
  /** A terminal of this size. Output then arrives on the `pty` channel. */
  tty?: { cols: number; rows: number };
  timeoutSeconds?: number;
  /** A long-running service, named so it can be found again. */
  service?: string;
  restartOnResume?: boolean;
  runId?: string;
}

/** The shape of the `WebSocket` constructor this module needs. */
export type WebSocketFactory = new (url: string) => WebSocket;

export interface MachinesOptions {
  /** The token a socket presents as `access_token`, when not using cookies. */
  bearerToken?: () => string | null;
  /** Overrides the global `WebSocket`; tests pass a fake. */
  WebSocket?: WebSocketFactory | null;
  /** How long one output poll waits on the server for something new. */
  pollWaitSeconds?: number;
  /** The client's pod: the default for creating and changing pod machines. */
  podId?: () => string | undefined;
}

/** What `run` returns: how the command ended, and everything it wrote. */
export interface MachineRunResult {
  exitCode: number | null;
  state: string;
  events: Array<Extract<MachineProcessEvent, { type: "output" }>>;
  /** Everything written, in order, whatever the channel. */
  output: string;
  /** stdout and terminal output. */
  stdout: string;
  stderr: string;
  /** Exited 0. */
  ok: boolean;
}

/**
 * A process started on a machine.
 *
 * Iterate it for its output and then exactly one `exit` event. Output comes
 * over the process's socket where there is a `WebSocket`, and from polling
 * `/output` where there is not or the socket drops; either way it resumes
 * from the last sequence seen, so nothing is repeated or skipped.
 */
export class MachineExecution implements AsyncIterable<MachineProcessEvent> {
  private socket: WebSocket | null = null;

  constructor(
    private readonly machines: MachinesNamespace,
    readonly ref: string,
    readonly process: ProcessResponse,
  ) {}

  get id(): string {
    return this.process.id;
  }

  [Symbol.asyncIterator](): AsyncIterator<MachineProcessEvent> {
    return this.events()[Symbol.asyncIterator]();
  }

  /** Every event from after `after`, ending with the exit. */
  async *events(after = 0): AsyncGenerator<MachineProcessEvent> {
    let sequence = after;
    const factory = this.machines.webSocketFactory();
    if (factory) {
      for await (const event of this.streamed(factory, sequence)) {
        if (event.type === "output") sequence = event.sequence;
        yield event;
        if (event.type === "exit") return;
      }
    }
    // No socket, or it closed before the process ended: carry on by polling.
    for (;;) {
      const page = await this.machines.processes.output(this.ref, this.id, {
        after: sequence,
        waitSeconds: this.machines.pollWaitSeconds,
      });
      for (const chunk of page.chunks) {
        yield { type: "output", sequence: chunk.sequence, channel: chunk.channel, data: chunk.data };
      }
      sequence = Math.max(sequence, page.next_sequence);
      if (page.exit_code != null || STOPPED_STATES.has(page.state)) {
        yield { type: "exit", state: page.state, exit_code: page.exit_code ?? null };
        return;
      }
    }
  }

  /** The exit, having read (and dropped) all the output before it. */
  async wait(): Promise<{ state: string; exit_code: number | null }> {
    for await (const event of this.events()) {
      if (event.type === "exit") return { state: event.state, exit_code: event.exit_code };
    }
    throw new Error("The process ended without an exit event.");
  }

  /** Type into the process: over its socket when one is open, else by request. */
  async input(data: string): Promise<void> {
    if (this.socket?.readyState === 1) {
      this.socket.send(JSON.stringify({ type: "input", data }));
      return;
    }
    await this.machines.processes.input(this.ref, this.id, { data });
  }

  async resize(cols: number, rows: number): Promise<void> {
    if (this.socket?.readyState === 1) {
      this.socket.send(JSON.stringify({ type: "resize", cols, rows }));
      return;
    }
    await this.machines.processes.resize(this.ref, this.id, { cols, rows });
  }

  kill(): Promise<void> {
    return this.machines.processes.kill(this.ref, this.id);
  }

  private async *streamed(
    factory: WebSocketFactory,
    after: number,
  ): AsyncGenerator<MachineProcessEvent> {
    const queue: MachineProcessEvent[] = [];
    let closed = false;
    let wake: (() => void) | null = null;
    const notify = () => {
      wake?.();
      wake = null;
    };
    let socket: WebSocket;
    try {
      socket = new factory(this.machines.processStreamUrl(this.ref, this.id, after));
    } catch {
      return;
    }
    this.socket = socket;
    socket.onmessage = (message: MessageEvent) => {
      try {
        const event = JSON.parse(String(message.data)) as MachineProcessEvent;
        if (event.type === "output" || event.type === "exit") queue.push(event);
      } catch {
        // Not JSON: nothing this protocol sends, so nothing to show.
      }
      notify();
    };
    socket.onclose = () => {
      closed = true;
      notify();
    };
    socket.onerror = () => {
      closed = true;
      notify();
    };
    try {
      for (;;) {
        const next = queue.shift();
        if (next) {
          yield next;
          if (next.type === "exit") return;
          continue;
        }
        if (closed) return;
        await new Promise<void>((resolve) => {
          wake = resolve;
        });
      }
    } finally {
      this.socket = null;
      if (socket.readyState === 0 || socket.readyState === 1) socket.close();
    }
  }
}

/**
 * The machines a person can use: their own default one (`me`), the ones a pod
 * keeps (`pod:<pod>:<name>`), and the ones they connected (`mine:<name>`).
 *
 * Hand-written rather than generated because a ref is not a plain path
 * parameter -- see `machinePathRef`. An app's page reaches the machines of its
 * own pod, as its viewer; never anybody's own (`me`).
 */
export class MachinesNamespace {
  readonly pollWaitSeconds: number;
  readonly processes: MachineProcessesApi;
  readonly files: MachineFilesApi;
  readonly previews: MachinePreviewsApi;
  readonly browser: MachineBrowserApi;

  constructor(
    private readonly http: HttpClient,
    private readonly options: MachinesOptions = {},
  ) {
    this.pollWaitSeconds = options.pollWaitSeconds ?? 20;
    this.processes = new MachineProcessesApi(this);
    this.files = new MachineFilesApi(this);
    this.previews = new MachinePreviewsApi(this);
    this.browser = new MachineBrowserApi(this);
  }

  private podMachines(podId: string | undefined): string {
    const chosen = podId ?? this.options.podId?.();
    if (!chosen) {
      throw new Error("podId is required for a pod's machines: pass it, or bind the client to a pod.");
    }
    return `/pods/${encodeURIComponent(chosen)}/machines`;
  }

  /** @internal A request. */
  call<T>(
    method: string,
    path: string,
    options: Parameters<HttpClient["request"]>[2] = {},
  ): Promise<T> {
    return this.http.request<T>(method, path, options);
  }

  /** @internal `call`, for a response that is bytes. */
  bytes(
    path: string,
    headers?: Record<string, string>,
  ): Promise<{ blob: Blob; status: number; contentRange: string | null }> {
    return this.http.requestBytesResponse("GET", path, { headers });
  }

  /** @internal */
  webSocketFactory(): WebSocketFactory | null {
    if (this.options.WebSocket !== undefined) return this.options.WebSocket;
    const global = (globalThis as { WebSocket?: WebSocketFactory }).WebSocket;
    return global ?? null;
  }

  /** @internal The URL of a process's output socket. */
  processStreamUrl(ref: string, processId: string, after = 0): string {
    const query = new URLSearchParams();
    if (after) query.set("after", String(after));
    // A browser cannot put a header on a socket handshake. Cookies go anyway;
    // a token has to travel in the query.
    const token = this.options.bearerToken?.();
    if (token) query.set("access_token", token);
    const base = this.http.getBaseUrl().replace(/^http/, "ws");
    const encoded = query.toString();
    const suffix = encoded ? `?${encoded}` : "";
    return `${base}${machinePath(ref)}/processes/${encodeURIComponent(processId)}/stream${suffix}`;
  }

  /** The person's own default machine. */
  me(): Promise<MachineResponse> {
    return this.get("me");
  }

  get(ref: string): Promise<MachineResponse> {
    return this.call("GET", machinePath(ref));
  }

  /** Every machine the caller can see, or only a pod's. */
  async list(options: { podId?: string } = {}): Promise<MachineResponse[]> {
    const response = await this.call<{ machines: MachineResponse[] }>("GET", "/machines", {
      params: { pod_id: options.podId },
    });
    return response.machines;
  }

  /**
   * Whether the machine is ready, without starting anything.
   *
   * `ready` it is running; `downloading` it is fetching its image, which the
   * first start after an update does; `starting` it is coming up; `asleep` it
   * is not running and starts on first use; `unavailable` it could not be asked.
   */
  startup(ref: string): Promise<MachineStartupResponse> {
    return this.call("GET", `${machinePath(ref)}/startup`);
  }

  /** Let the machine go to sleep now rather than when it idles out. */
  release(ref: string): Promise<void> {
    return this.call("POST", `${machinePath(ref)}:release`);
  }

  restart(ref: string): Promise<void> {
    return this.call("POST", `${machinePath(ref)}:restart`);
  }

  /**
   * Put parts of a machine back as they started. `home` erases files and
   * needs `confirm`; `forgetLogins` also signs the browser out.
   */
  reset(
    ref: string,
    options: {
      scopes: Array<"processes" | "runs" | "caches" | "docker" | "browser-session" | "home">;
      confirm?: boolean;
      forgetLogins?: boolean;
    },
  ): Promise<ResetResponse> {
    return this.call("POST", `${machinePath(ref)}:reset`, {
      body: {
        scopes: options.scopes,
        ...(options.confirm !== undefined ? { confirm: options.confirm } : {}),
        ...(options.forgetLogins !== undefined ? { forget_logins: options.forgetLogins } : {}),
      },
    });
  }

  /** The sizes and limits the caller's plan allows. */
  policy(options: { podId?: string } = {}): Promise<MachinePolicyResponse> {
    return this.call("GET", "/machines:policy", { params: { pod_id: options.podId } });
  }

  /** Start a command and follow it: see `MachineExecution`. */
  async exec(ref: string, options: ExecOptions): Promise<MachineExecution> {
    const process = await this.processes.start(ref, options);
    return new MachineExecution(this, ref, process);
  }

  /**
   * Run a command to the end and return its exit code and output -- the
   * Python SDK's `machines.run`.
   *
   *     const result = await client.machines.run("me", { command: "python --version" });
   *     console.log(result.exitCode, result.output);
   */
  async run(ref: string, options: ExecOptions): Promise<MachineRunResult> {
    const execution = await this.exec(ref, options);
    const events: MachineRunResult["events"] = [];
    let state = "";
    let exitCode: number | null = null;
    for await (const event of execution) {
      if (event.type === "output") {
        events.push(event);
      } else {
        state = event.state;
        exitCode = event.exit_code;
      }
    }
    const written = (keep: (channel: string) => boolean) =>
      events
        .filter((event) => keep(event.channel))
        .map((event) => event.data)
        .join("");
    return {
      exitCode,
      state,
      events,
      output: written(() => true),
      stdout: written((channel) => channel !== "stderr"),
      stderr: written((channel) => channel === "stderr"),
      ok: exitCode === 0,
    };
  }

  // -- a pod's machines: the client's pod unless `podId` says otherwise -------

  /** Create a machine for a pod. */
  create(options: {
    name: string;
    podId?: string;
    displayName?: string;
    template?: "workspace" | "workspace-docker";
    size?: string;
    runSlots?: number;
  }): Promise<MachineResponse> {
    const body: Partial<Record<keyof CreatePodMachineRequest, unknown>> = {
      name: options.name,
      display_name: options.displayName,
      template: options.template,
      size: options.size,
      run_slots: options.runSlots,
    };
    return this.call("POST", this.podMachines(options.podId), { body });
  }

  /** Change a pod machine's display name, size or run slots. */
  update(
    name: string,
    changes: {
      displayName?: string | null;
      size?: string | null;
      runSlots?: number | null;
    },
    options: { podId?: string } = {},
  ): Promise<MachineResponse> {
    const body: Partial<Record<keyof UpdatePodMachineRequest, unknown>> = {};
    if (changes.displayName !== undefined) body.display_name = changes.displayName;
    if (changes.size !== undefined) body.size = changes.size;
    if (changes.runSlots !== undefined) body.run_slots = changes.runSlots;
    return this.call("PATCH", `${this.podMachines(options.podId)}/${encodeURIComponent(name)}`, {
      body,
    });
  }

  delete(name: string, options: { podId?: string } = {}): Promise<void> {
    return this.call("DELETE", `${this.podMachines(options.podId)}/${encodeURIComponent(name)}`);
  }

  /** Which agents and functions may use a pod machine. */
  grants(name: string, options: { podId?: string } = {}): Promise<MachineGrantsModel> {
    return this.call("GET", `${this.podMachines(options.podId)}/${encodeURIComponent(name)}/grants`);
  }

  /** Replace which agents and functions may use a pod machine. People need
   *  no grant: their pod role decides. */
  setGrants(
    name: string,
    grants: MachineGrantsModel,
    options: { podId?: string } = {},
  ): Promise<MachineGrantsModel> {
    return this.call(
      "PUT",
      `${this.podMachines(options.podId)}/${encodeURIComponent(name)}/grants`,
      { body: grants },
    );
  }
}

function machinePath(ref: string): string {
  return `/machines/${machinePathRef(ref)}`;
}

export class MachineProcessesApi {
  constructor(private readonly machines: MachinesNamespace) {}

  start(ref: string, options: ExecOptions): Promise<ProcessResponse> {
    return this.machines.call("POST", `${machinePath(ref)}/processes`, {
      body: {
        command: options.command,
        cwd: options.cwd,
        env: options.env,
        tty: options.tty,
        timeout_seconds: options.timeoutSeconds,
        service: options.service,
        restart_on_resume: options.restartOnResume,
        run_id: options.runId,
      },
    });
  }

  async list(ref: string): Promise<ProcessResponse[]> {
    const response = await this.machines.call<{ processes: ProcessResponse[] }>(
      "GET",
      `${machinePath(ref)}/processes`,
    );
    return response.processes;
  }

  get(ref: string, processId: string): Promise<ProcessResponse> {
    return this.machines.call("GET", processPath(ref, processId));
  }

  /** A page of output after `after`, waiting up to `waitSeconds` for some. */
  output(
    ref: string,
    processId: string,
    options: { after?: number; waitSeconds?: number } = {},
  ): Promise<ProcessOutputResponse> {
    return this.machines.call("GET", `${processPath(ref, processId)}/output`, {
      params: { after: options.after || undefined, wait_seconds: options.waitSeconds || undefined },
    });
  }

  input(
    ref: string,
    processId: string,
    input: { data: string } | { dataBase64: string },
  ): Promise<void> {
    return this.machines.call("POST", `${processPath(ref, processId)}:input`, {
      body: "data" in input ? { data: input.data } : { data_base64: input.dataBase64 },
    });
  }

  resize(ref: string, processId: string, size: { cols: number; rows: number }): Promise<void> {
    return this.machines.call("POST", `${processPath(ref, processId)}:resize`, { body: size });
  }

  kill(ref: string, processId: string): Promise<void> {
    return this.machines.call("DELETE", processPath(ref, processId));
  }
}

function processPath(ref: string, processId: string): string {
  return `${machinePath(ref)}/processes/${encodeURIComponent(processId)}`;
}

export class MachineFilesApi {
  constructor(private readonly machines: MachinesNamespace) {}

  /**
   * One directory. Does not start a paused machine unless `wake` is set,
   * which is the point of `sleeping`: a file pane that started a machine on
   * every render would hold compute open for as long as it was on screen.
   */
  list(
    ref: string,
    options: { path?: string; wake?: boolean; after?: string } = {},
  ): Promise<MachineFileListResponse> {
    return this.machines.call("GET", `${machinePath(ref)}/files`, {
      params: {
        path: options.path || undefined,
        wake: options.wake || undefined,
        // From a previous response's `next_after`.
        after: options.after || undefined,
      },
    });
  }

  stat(ref: string, path: string): Promise<MachineFileEntry> {
    return this.machines.call("GET", `${machinePath(ref)}/files:stat`, { params: { path } });
  }

  /**
   * A file, or a slice of one. `range` sends an HTTP `Range` and gets a 206
   * back, which is how a file past the server's single-read ceiling is
   * reachable at all; `readWhole` does that for you.
   */
  async read(
    ref: string,
    path: string,
    options: { offset?: number; length?: number; range?: { start: number; end: number } } = {},
  ): Promise<Blob> {
    const answer = await this.machines.bytes(
      contentPath(ref, path, options),
      options.range ? rangeHeader(options.range) : undefined,
    );
    return answer.blob;
  }

  /**
   * A whole file, however big, in as many requests as that takes.
   *
   * The server caps one read at 8 MiB. Ranges are requested in order and
   * stitched until the server returns a short slice, so the size is
   * discovered rather than trusted: `sizeBytes` is only a hint that lets a
   * small file skip straight to one unranged read, and 0 is always correct.
   * `chunk` is clamped to the ceiling, because asking for more got 8 MiB back
   * and advanced the cursor past bytes never read.
   */
  async readWhole(
    ref: string,
    path: string,
    sizeBytes = 0,
    chunk = MAX_READ_BYTES,
  ): Promise<Blob> {
    const step = Math.min(Math.max(Math.floor(chunk), 1), MAX_READ_BYTES);
    const parts: Blob[] = [];
    let start = 0;
    if (sizeBytes > 0 && sizeBytes <= step) {
      const only = await this.read(ref, path);
      // Short of the ceiling means that was the whole file. Exactly the
      // ceiling means the hint was stale -- the file grew after the listing
      // that measured it -- so keep what arrived and read on from there.
      if (only.size < MAX_READ_BYTES) return only;
      parts.push(only);
      start = only.size;
    }
    for (;;) {
      let part: Blob;
      let honoured: boolean;
      try {
        const answer = await this.machines.bytes(
          contentPath(ref, path),
          rangeHeader({ start, end: start + step - 1 }),
        );
        part = answer.blob;
        honoured = rangeHonoured(answer.status, answer.contentRange, start);
      } catch (error) {
        // 416: the file ended exactly on a chunk boundary, or it is empty.
        // Both are "nothing at this offset", not a failure.
        if (error instanceof ApiError && error.statusCode === 416) break;
        throw error;
      }
      if (!honoured) {
        // The server ignored the Range and sent the file from byte 0; asking
        // again does the same forever. From the start and short of the
        // ceiling that is simply the whole file; anywhere else it cannot be
        // stitched into one.
        if (start === 0 && part.size < MAX_READ_BYTES) return part;
        throw new Error(
          `The server ignored the Range header reading "${path}" at byte ${start}; ` +
            "stopping rather than stitching repeated copies of the file.",
        );
      }
      if (part.size === 0) break;
      parts.push(part);
      start += part.size;
      if (part.size < step) break;
    }
    return new Blob(parts);
  }

  /** Write a file, replacing whatever was there. */
  write(
    ref: string,
    path: string,
    content: Blob | ArrayBuffer | Uint8Array | string,
  ): Promise<MachineFileEntry> {
    return this.machines.call("PUT", `${machinePath(ref)}/files:content`, {
      params: { path },
      rawBody: content as BodyInit,
      headers: { "Content-Type": "application/octet-stream" },
    });
  }

  delete(ref: string, path: string, options: { recursive?: boolean } = {}): Promise<void> {
    return this.machines.call("DELETE", `${machinePath(ref)}/files`, {
      params: { path, recursive: options.recursive || undefined },
    });
  }

  move(ref: string, source: string, destination: string): Promise<MachineFileEntry> {
    return this.machines.call("POST", `${machinePath(ref)}/files:move`, {
      body: { source, destination },
    });
  }

  /** Copy between the machine and a pod's files, in either direction. */
  sync(
    ref: string,
    options: {
      direction: "to_pod" | "from_pod";
      machinePath: string;
      podId: string;
      podPath: string;
    },
  ): Promise<SyncResponse> {
    return this.machines.call("POST", `${machinePath(ref)}/files:sync`, {
      body: {
        direction: options.direction,
        machine_path: options.machinePath,
        pod_id: options.podId,
        pod_path: options.podPath,
      },
    });
  }
}

/**
 * The query is built into the path because the byte reader takes no params:
 * it is the byte-returning sibling of `request`, not a full request builder.
 */
function contentPath(
  ref: string,
  path: string,
  options: { offset?: number; length?: number } = {},
): string {
  const query = new URLSearchParams({ path });
  if (options.offset) query.set("offset", String(options.offset));
  if (options.length) query.set("length", String(options.length));
  return `${machinePath(ref)}/files:content?${query.toString()}`;
}

export class MachinePreviewsApi {
  constructor(private readonly machines: MachinesNamespace) {}

  /**
   * A URL for a port on the machine. `private` previews ask whoever opens
   * them to be signed in as somebody who may use the machine; `link` ones
   * work for anyone holding the URL until they expire or are revoked.
   */
  create(
    ref: string,
    options: { port: number; visibility?: "private" | "link"; ttlSeconds?: number },
  ): Promise<PreviewResponse> {
    return this.machines.call("POST", `${machinePath(ref)}/previews`, {
      body: {
        port: options.port,
        visibility: options.visibility ?? "private",
        ...(options.ttlSeconds !== undefined ? { ttl_seconds: options.ttlSeconds } : {}),
      },
    });
  }

  async list(ref: string): Promise<PreviewResponse[]> {
    const response = await this.machines.call<{ previews: PreviewResponse[] }>(
      "GET",
      `${machinePath(ref)}/previews`,
    );
    return response.previews;
  }

  revoke(ref: string, previewId: string): Promise<void> {
    return this.machines.call(
      "DELETE",
      `${machinePath(ref)}/previews/${encodeURIComponent(previewId)}`,
    );
  }
}

export class MachineBrowserApi {
  constructor(private readonly machines: MachinesNamespace) {}

  /** Whether the machine's browser can be watched, without starting anything. */
  status(ref: string): Promise<BrowserStatusResponse> {
    return this.machines.call("GET", `${machinePath(ref)}/browser`);
  }

  /**
   * A signed, short-lived URL for the live browser view. Minting one starts
   * the machine if it is paused, so ask whether it is awake first.
   */
  access(ref: string, options: { ttlSeconds?: number } = {}): Promise<BrowserAccessResponse> {
    return this.machines.call("POST", `${machinePath(ref)}/browser:access`, {
      body: { ttl_seconds: options.ttlSeconds ?? 1800 },
    });
  }

  /**
   * Fit the browser's display to the pane showing it. `size` is what it
   * became, which may be smaller than asked; `null` when nothing could be
   * resized, which is not an error.
   */
  display(ref: string, size: { width: number; height: number }): Promise<{ size: string | null }> {
    return this.machines.call("POST", `${machinePath(ref)}/browser:display`, { body: size });
  }

  navigate(ref: string, url: string): Promise<NavigateResponse> {
    return this.machines.call("POST", `${machinePath(ref)}/browser:navigate`, { body: { url } });
  }

  /** What page the browser signing in to `origin` is actually showing. */
  currentPageUrl(ref: string, origin: string): Promise<{ url: string | null }> {
    return this.machines.call("GET", `${machinePath(ref)}/browser/current-page-url`, {
      params: { origin },
    });
  }

  /** The sites the browser is signed in to. `wake` starts a paused machine to ask. */
  logins(ref: string, options: { wake?: boolean } = {}): Promise<WebLoginListResponse> {
    return this.machines.call("GET", `${machinePath(ref)}/browser/logins`, {
      params: { wake: options.wake || undefined },
    });
  }

  /** Sign the browser out of a site. */
  forgetLogin(ref: string, site: string): Promise<ForgetResponse> {
    return this.machines.call(
      "DELETE",
      `${machinePath(ref)}/browser/logins/${encodeURIComponent(site)}`,
    );
  }
}

