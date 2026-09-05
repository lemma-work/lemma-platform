import { useCallback, useEffect, useRef, useState } from "react";
import type { LemmaClient } from "../client.js";
import { isTerminalFunctionStatus, normalizeRunStatus, sleep } from "../run-utils.js";
import type { FunctionRun, FunctionRunSummary } from "../types.js";
import { normalizeError, resolvePodClient, resolvePodId } from "./utils.js";

export interface UseFunctionSessionOptions {
  client: LemmaClient;
  podId?: string;
  functionName?: string;
  runId?: string | null;
  autoPoll?: boolean;
  pollIntervalMs?: number;
  onRun?: (run: FunctionRun) => void;
  onError?: (error: unknown) => void;
}

export interface UseFunctionSessionResult {
  runId: string | null;
  run: FunctionRun | null;
  status?: string;
  isPolling: boolean;
  error: Error | null;
  setRunId: (runId: string | null) => void;
  start: (options?: {
    functionName?: string;
    input?: Record<string, unknown>;
    connect?: boolean;
    /**
     * Run a specific built revision instead of the live one -- a revision
     * number ("r12") or a hash prefix. Requires `function.update`.
     */
    revision?: string;
  }) => Promise<FunctionRun>;
  refresh: (runId?: string | null) => Promise<FunctionRun | null>;
  listHistory: (options?: {
    functionName?: string;
    limit?: number;
    pageToken?: string;
  }) => Promise<FunctionRunSummary[]>;
}

function resolveFunctionName(base?: string, override?: string): string {
  const resolved = override ?? base;
  if (!resolved) {
    throw new Error("functionName is required.");
  }
  return resolved;
}

export function useFunctionSession({
  client,
  podId,
  functionName,
  runId: externalRunId = null,
  autoPoll = true,
  pollIntervalMs = 2000,
  onRun,
  onError,
}: UseFunctionSessionOptions): UseFunctionSessionResult {
  const [runId, setRunIdState] = useState<string | null>(externalRunId);
  const [run, setRun] = useState<FunctionRun | null>(null);
  const [status, setStatus] = useState<string | undefined>(undefined);
  const [isPolling, setIsPolling] = useState(false);
  const [error, setError] = useState<Error | null>(null);

  const onRunRef = useRef(onRun);
  const onErrorRef = useRef(onError);

  useEffect(() => { onRunRef.current = onRun; }, [onRun]);
  useEffect(() => { onErrorRef.current = onError; }, [onError]);

  const setRunId = useCallback((nextRunId: string | null) => {
    setRunIdState(nextRunId);
    if (!nextRunId) {
      setRun(null);
      setStatus(undefined);
    }
  }, []);

  useEffect(() => {
    setRunIdState((current) => current === externalRunId ? current : externalRunId);
    if (!externalRunId) {
      setRun(null);
      setStatus(undefined);
    }
  }, [externalRunId]);

  const refresh = useCallback(async (explicitRunId?: string | null): Promise<FunctionRun | null> => {
    const id = explicitRunId ?? runId;
    if (!id) return null;

    try {
      const scopedClient = resolvePodClient(client, resolvePodId(client, podId));
      const name = resolveFunctionName(functionName);
      const nextRun = await scopedClient.functions.runs.get(name, id);

      setRun(nextRun);
      const nextStatus = normalizeRunStatus(nextRun.status);
      setStatus(nextStatus);
      onRunRef.current?.(nextRun);

      return nextRun;
    } catch (refreshError) {
      const normalized = normalizeError(refreshError, "Failed to fetch function run.");
      setError(normalized);
      onErrorRef.current?.(refreshError);
      return null;
    }
  }, [client, functionName, podId, runId]);

  const listHistory = useCallback(async (options: {
    functionName?: string;
    limit?: number;
    pageToken?: string;
  } = {}): Promise<FunctionRunSummary[]> => {
    try {
      const scopedClient = resolvePodClient(client, resolvePodId(client, podId));
      const name = resolveFunctionName(functionName, options.functionName);
      const response = await scopedClient.functions.runs.list(name, {
        limit: options.limit,
        pageToken: options.pageToken,
      });

      return response.items ?? [];
    } catch (listError) {
      const normalized = normalizeError(listError, "Failed to list function runs.");
      setError(normalized);
      onErrorRef.current?.(listError);
      return [];
    }
  }, [client, functionName, podId]);

  const start = useCallback(async (options: {
    functionName?: string;
    input?: Record<string, unknown>;
    connect?: boolean;
    revision?: string;
  } = {}): Promise<FunctionRun> => {
    setError(null);

    const scopedClient = resolvePodClient(client, resolvePodId(client, podId));
    const name = resolveFunctionName(functionName, options.functionName);

    const created = await scopedClient.functions.runs.create(name, {
      input: options.input,
      revision: options.revision,
    });

    setRun(created);
    setRunIdState(created.id);
    const nextStatus = normalizeRunStatus(created.status);
    setStatus(nextStatus);
    onRunRef.current?.(created);

    if (options.connect !== false) {
      await refresh(created.id);
    }

    return created;
  }, [client, functionName, podId, refresh]);

  useEffect(() => {
    if (!runId) {
      return;
    }

    const controller = new AbortController();
    let cancelled = false;
    (async () => {
      try {
        await refresh(runId);
      } catch {
        if (!cancelled) {
          // refresh handles errors internally
        }
      }
    })();
    return () => {
      cancelled = true;
      controller.abort();
    };
  }, [refresh, runId]);

  useEffect(() => {
    if (!autoPoll || !runId) {
      return;
    }

    let active = true;
    const abortController = new AbortController();

    const loop = async () => {
      setIsPolling(true);

      while (active) {
        const latest = await refresh(runId);
        if (!latest) {
          break;
        }
        const latestStatus = normalizeRunStatus(latest?.status);

        if (latestStatus && isTerminalFunctionStatus(latestStatus)) {
          break;
        }

        try {
          await sleep(pollIntervalMs, abortController.signal);
        } catch (sleepError) {
          if (sleepError instanceof Error && sleepError.name === "AbortError") {
            break;
          }
          throw sleepError;
        }
      }

      setIsPolling(false);
    };

    void loop().catch((pollError) => {
      const normalized = normalizeError(pollError, "Failed while polling function run.");
      setError(normalized);
      onErrorRef.current?.(pollError);
      setIsPolling(false);
    });

    return () => {
      active = false;
      abortController.abort();
    };
  }, [autoPoll, pollIntervalMs, refresh, runId]);

  return {
    runId,
    run,
    status,
    isPolling,
    error,
    setRunId,
    start,
    refresh,
    listHistory,
  };
}
