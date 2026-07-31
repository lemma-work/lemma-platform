// Framework-agnostic workflow run controller + helpers.
//
// This is the run state machine extracted from `useFlowSession`: create/resume
// (form submit), refresh, history, cancel, and a terminal-aware polling loop —
// with NO React and NO DOM. Workflow apps are too bespoke to ship a UI for, so
// alongside the controller we export pure helpers (status/output derivation,
// form-field derivation, poll heuristics) that apps compose into their own
// timelines and forms. The React hooks are thin adapters over this.
import type { LemmaClient } from "../../client.js";
import { isTerminalWorkflowStatus, normalizeRunStatus, sleep } from "../../run-utils.js";
import {
  buildSchemaFormFields,
  type JsonSchemaLike,
  type SchemaFormField,
} from "../../schema-form.js";
import type { FlowRun, WorkflowRunInputs, WorkflowRunSummary } from "../../types.js";

export interface WorkflowScope {
  podId?: string | null;
  flowName?: string | null;
}

/** Observable, render-agnostic snapshot of a single workflow run. */
export interface WorkflowSessionState {
  runId: string | null;
  run: FlowRun | null;
  status?: string;
  isPolling: boolean;
  error: Error | null;
}

export interface WorkflowControllerOptions {
  client: LemmaClient;
  scope?: WorkflowScope;
  autoPoll?: boolean;
  pollIntervalMs?: number;
  initialRunId?: string | null;
  onRun?: (run: FlowRun) => void;
  onError?: (error: unknown) => void;
}

export interface WorkflowStartOptions {
  flowName?: string;
  /** Submitted to the entry form when the created run waits on one. */
  inputs?: WorkflowRunInputs;
  connect?: boolean;
}

export interface WorkflowResumeOptions {
  runId?: string | null;
  nodeId?: string;
  inputs?: WorkflowRunInputs;
  connect?: boolean;
}

/** Derived, render-ready view of a run — the shared workflow "selector". */
export interface WorkflowOutputs {
  status?: string;
  isFinished: boolean;
  isWaitingForInput: boolean;
  output: FlowRun["execution_context"] | null;
  finalOutput: FlowRun["execution_context"] | null;
}

// --- pure helpers -----------------------------------------------------------

function normalizeError(error: unknown, fallback: string): Error {
  if (error instanceof Error) return error;
  if (typeof error === "string" && error.trim()) return new Error(error.trim());
  return new Error(fallback);
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return !!value && typeof value === "object" && !Array.isArray(value);
}

function resolvePodId(client: LemmaClient, podId?: string | null): string {
  const resolved = podId ?? client.podId;
  if (!resolved) {
    throw new Error("podId is required. Pass podId or set it on LemmaClient.");
  }
  return resolved;
}

function resolvePodClient(client: LemmaClient, podId: string): LemmaClient {
  if (!podId || podId === client.podId) return client;
  return client.withPod(podId);
}

function resolveFlowName(base?: string | null, override?: string): string {
  const resolved = override ?? base;
  if (!resolved) {
    throw new Error("flowName is required.");
  }
  return resolved;
}

function resolveRunId(base?: string | null, override?: string | null): string {
  const resolved = override ?? base;
  if (!resolved) {
    throw new Error("runId is required.");
  }
  return resolved;
}

/**
 * Derive the render-ready view of a run (status, finished/waiting flags, output).
 * Pure — shared by the hooks and any app UI so "is it waiting / done / what's
 * the output" is computed one way everywhere.
 */
export function selectWorkflowOutputs(run: FlowRun | null | undefined): WorkflowOutputs {
  const status = normalizeRunStatus(run?.status);
  const isFinished = isTerminalWorkflowStatus(status);
  const isWaitingForInput = status === "WAITING" && run?.active_wait?.wait_type === "HUMAN";
  const output = run?.execution_context ?? null;
  return {
    status,
    isFinished,
    isWaitingForInput,
    output,
    finalOutput: isFinished ? output : null,
  };
}

/**
 * Derive form fields for a run that is waiting on a human form, from the
 * resolved `active_wait.payload.input_schema`. Returns `[]` when the run isn't
 * waiting on a form. Apps render these however they like.
 */
export function getRunInputFields(run: FlowRun | null | undefined): SchemaFormField[] {
  const wait = run?.active_wait;
  if (!wait || wait.wait_type !== "HUMAN") return [];
  const payload = wait.payload;
  const schema = isRecord(payload) ? payload.input_schema : undefined;
  return buildSchemaFormFields(schema as JsonSchemaLike | null | undefined);
}

/**
 * Build the form-submit payload for a run parked on a HUMAN wait: the node id to
 * submit to plus the collected input values. Returns null when the run isn't
 * waiting on a form. Pure — apps (or `useWorkflowForm`) feed `inputs` and hand
 * the result to `WorkflowController.resume`.
 */
export function buildWorkflowFormSubmit(
  run: FlowRun | null | undefined,
  inputs: Record<string, unknown> = {},
): { nodeId: string; inputs: Record<string, unknown> } | null {
  const wait = run?.active_wait;
  if (!wait || wait.wait_type !== "HUMAN") return null;
  return { nodeId: wait.node_id, inputs };
}

/**
 * Whether a run is worth polling: active runs yes, terminal runs no, and a run
 * parked on a HUMAN form wait no (it only advances on submit, not on poll).
 * An unknown status keeps polling until the first status is learned.
 */
export function shouldPollWorkflowRun(run: FlowRun | null | undefined): boolean {
  const status = normalizeRunStatus(run?.status);
  if (!status) return true;
  if (isTerminalWorkflowStatus(status)) return false;
  if (status === "WAITING" && run?.active_wait?.wait_type === "HUMAN") return false;
  return true;
}

// --- the controller ---------------------------------------------------------

export class WorkflowController {
  private options: WorkflowControllerOptions;
  private state: WorkflowSessionState;
  private readonly listeners = new Set<() => void>();
  private pollAbort: AbortController | null = null;

  constructor(options: WorkflowControllerOptions) {
    this.options = options;
    this.state = {
      runId: options.initialRunId ?? null,
      run: null,
      status: undefined,
      isPolling: false,
      error: null,
    };
  }

  // -- store ------------------------------------------------------------------

  subscribe = (listener: () => void): (() => void) => {
    this.listeners.add(listener);
    return () => {
      this.listeners.delete(listener);
    };
  };

  getState = (): WorkflowSessionState => this.state;

  setOptions = (options: WorkflowControllerOptions): void => {
    this.options = options;
  };

  private emit(): void {
    for (const listener of this.listeners) listener();
  }

  private patch(partial: Partial<WorkflowSessionState>): void {
    this.state = { ...this.state, ...partial };
    this.emit();
  }

  private get client(): LemmaClient {
    return this.options.client;
  }

  private get scope(): WorkflowScope {
    return this.options.scope ?? {};
  }

  // -- polling ----------------------------------------------------------------

  private ensurePolling(): void {
    if (this.options.autoPoll === false) return;
    if (!this.state.runId) return;
    if (this.pollAbort) return; // already polling
    this.startPolling();
  }

  private startPolling(): void {
    const abort = new AbortController();
    this.pollAbort = abort;
    this.patch({ isPolling: true });

    void (async () => {
      try {
        while (!abort.signal.aborted) {
          const id = this.state.runId;
          if (!id) break;
          const latest = await this.refresh(id);
          if (!latest) break;
          const latestStatus = normalizeRunStatus(latest.status);
          if (latestStatus && isTerminalWorkflowStatus(latestStatus)) break;
          try {
            await sleep(this.options.pollIntervalMs ?? 2000, abort.signal);
          } catch (sleepError) {
            if (sleepError instanceof Error && sleepError.name === "AbortError") break;
            throw sleepError;
          }
        }
      } catch (pollError) {
        const normalized = normalizeError(pollError, "Failed while polling flow run.");
        this.patch({ error: normalized });
        this.options.onError?.(pollError);
      } finally {
        if (this.pollAbort === abort) this.pollAbort = null;
        this.patch({ isPolling: false });
      }
    })();
  }

  private stopPolling(): void {
    this.pollAbort?.abort();
    this.pollAbort = null;
  }

  // -- lifecycle --------------------------------------------------------------

  setRunId = (nextRunId: string | null): void => {
    this.stopPolling();
    if (!nextRunId) {
      this.patch({ runId: null, run: null, status: undefined });
      return;
    }
    this.patch({ runId: nextRunId });
    if (this.options.autoPoll === false) {
      void this.refresh(nextRunId);
    } else {
      this.ensurePolling();
    }
  };

  /** Abort polling and drop listeners. Call when the owner unmounts. */
  destroy = (): void => {
    this.stopPolling();
    this.listeners.clear();
  };

  // -- operations -------------------------------------------------------------

  refresh = async (explicitRunId?: string | null): Promise<FlowRun | null> => {
    const id = explicitRunId ?? this.state.runId;
    if (!id) return null;
    try {
      const resolvedPodId = resolvePodId(this.client, this.scope.podId);
      const nextRun = await this.client.workflows.runs.get(id, resolvedPodId);
      this.patch({ run: nextRun, status: normalizeRunStatus(nextRun.status) });
      this.options.onRun?.(nextRun);
      return nextRun;
    } catch (refreshError) {
      const normalized = normalizeError(refreshError, "Failed to fetch flow run.");
      this.patch({ error: normalized });
      this.options.onError?.(refreshError);
      return null;
    }
  };

  listHistory = async (options: {
    flowName?: string;
    limit?: number;
    pageToken?: string;
  } = {}): Promise<WorkflowRunSummary[]> => {
    try {
      const scopedClient = resolvePodClient(this.client, resolvePodId(this.client, this.scope.podId));
      const name = resolveFlowName(this.scope.flowName, options.flowName);
      const response = await scopedClient.workflows.runs.list(name, {
        limit: options.limit,
        pageToken: options.pageToken,
      });
      return response.items ?? [];
    } catch (listError) {
      const normalized = normalizeError(listError, "Failed to list flow runs.");
      this.patch({ error: normalized });
      this.options.onError?.(listError);
      return [];
    }
  };

  start = async (options: WorkflowStartOptions = {}): Promise<FlowRun> => {
    this.patch({ error: null });

    const scopedClient = resolvePodClient(this.client, resolvePodId(this.client, this.scope.podId));
    const name = resolveFlowName(this.scope.flowName, options.flowName);

    // Runs are created without inputs; when the run starts on a form and inputs
    // were provided, submit them to that form immediately.
    let created = await scopedClient.workflows.runs.create(name);
    const activeWait = created.active_wait;
    if (
      created.id
      && activeWait?.wait_type === "HUMAN"
      && options.inputs
      && Object.keys(options.inputs).length > 0
    ) {
      created = await scopedClient.workflows.runs.submitForm(created.id, {
        node_id: activeWait.node_id,
        inputs: options.inputs,
      });
    }

    this.patch({ run: created, runId: created.id ?? null, status: normalizeRunStatus(created.status) });
    this.options.onRun?.(created);

    if (options.connect !== false && created.id) {
      await this.refresh(created.id);
    }
    this.ensurePolling();
    return created;
  };

  resume = async (options: WorkflowResumeOptions): Promise<FlowRun> => {
    this.patch({ error: null });

    const resolvedPodId = resolvePodId(this.client, this.scope.podId);
    const id = resolveRunId(this.state.runId, options.runId);
    let nodeId = options.nodeId;
    if (!nodeId) {
      const sourceRun = this.state.run?.id === id
        ? this.state.run
        : await this.client.workflows.runs.get(id, resolvedPodId);
      const wait = sourceRun?.active_wait;
      if (!wait || wait.wait_type !== "HUMAN") {
        throw new Error("Run is not waiting on a form; pass nodeId explicitly.");
      }
      nodeId = wait.node_id;
    }
    const resumed = await this.client.workflows.runs.submitForm(
      id,
      { node_id: nodeId, inputs: options.inputs ?? {} },
      resolvedPodId,
    );

    this.patch({ run: resumed, runId: resumed.id ?? id, status: normalizeRunStatus(resumed.status) });
    this.options.onRun?.(resumed);

    if (options.connect !== false) {
      await this.refresh(resumed.id ?? id);
    }
    this.ensurePolling();
    return resumed;
  };

  cancel = async (explicitRunId?: string | null): Promise<void> => {
    try {
      const resolvedPodId = resolvePodId(this.client, this.scope.podId);
      const id = resolveRunId(this.state.runId, explicitRunId);
      await this.client.workflows.runs.cancel(id, resolvedPodId);
      await this.refresh(id);
    } catch (cancelError) {
      const normalized = normalizeError(cancelError, "Failed to cancel flow run.");
      this.patch({ error: normalized });
      this.options.onError?.(cancelError);
    }
  };
}
