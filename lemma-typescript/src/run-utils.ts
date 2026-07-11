import type { FunctionRunStatus, WorkflowRunStatus } from "./openapi_client/index.js";

export type AnyRunStatus = FunctionRunStatus | WorkflowRunStatus | (string & {});

interface BackoffOptions {
  baseMs?: number;
  maxMs?: number;
  factor?: number;
}

const FUNCTION_TERMINAL = new Set<string>(["COMPLETED", "FAILED", "CANCELLED"]);
const WORKFLOW_TERMINAL = new Set<string>(["COMPLETED", "FAILED", "CANCELLED"]);

export function normalizeRunStatus(status: unknown): string | undefined {
  if (typeof status !== "string") {
    return undefined;
  }

  const normalized = status.trim().toUpperCase();
  return normalized.length > 0 ? normalized : undefined;
}

export function isTerminalFunctionStatus(status: unknown): boolean {
  const normalized = normalizeRunStatus(status);
  return !!normalized && FUNCTION_TERMINAL.has(normalized);
}

export function isTerminalWorkflowStatus(status: unknown, options: { treatWaitingAsTerminal?: boolean } = {}): boolean {
  const normalized = normalizeRunStatus(status);
  if (!normalized) return false;

  if (normalized === "WAITING") {
    return options.treatWaitingAsTerminal === true;
  }

  return WORKFLOW_TERMINAL.has(normalized);
}

export async function sleep(ms: number, signal?: AbortSignal): Promise<void> {
  if (!Number.isFinite(ms) || ms <= 0) {
    return;
  }

  await new Promise<void>((resolve, reject) => {
    const timer = setTimeout(() => {
      signal?.removeEventListener("abort", onAbort);
      resolve();
    }, ms);

    const onAbort = () => {
      clearTimeout(timer);
      signal?.removeEventListener("abort", onAbort);
      reject(new DOMException("Operation aborted", "AbortError"));
    };

    if (signal?.aborted) {
      clearTimeout(timer);
      reject(new DOMException("Operation aborted", "AbortError"));
      return;
    }

    signal?.addEventListener("abort", onAbort, { once: true });
  });
}

export function nextBackoffDelay(attempt: number, options: BackoffOptions = {}): number {
  const baseMs = options.baseMs ?? 500;
  const maxMs = options.maxMs ?? 6000;
  const factor = options.factor ?? 2;

  const safeAttempt = Math.max(0, Math.floor(attempt));
  const delay = Math.round(baseMs * Math.pow(factor, safeAttempt));

  return Math.min(Math.max(baseMs, delay), maxMs);
}

/**
 * Statuses worth retrying with backoff. Deliberately conservative: 429 is an
 * explicit "back off", and 502/503/504 are gateway errors where the request
 * usually never reached the handler — so retrying is safe even for writes.
 * 500 is excluded (it may indicate a partial side effect).
 */
export const RETRYABLE_STATUS: ReadonlySet<number> = new Set([429, 502, 503, 504]);

/** Parse a server `Retry-After` header (delta-seconds or HTTP-date) into ms,
 *  capped at 30s. Returns null when the header is absent or unparseable. */
export function serverRetryAfterMs(retryAfter: string | null | undefined): number | null {
  if (!retryAfter) {
    return null;
  }
  const seconds = Number(retryAfter);
  if (Number.isFinite(seconds) && seconds >= 0) {
    return Math.min(seconds * 1000, 30_000);
  }
  const dateMs = Date.parse(retryAfter);
  if (!Number.isNaN(dateMs)) {
    return Math.max(0, Math.min(dateMs - Date.now(), 30_000));
  }
  return null;
}

/** Resolve a backoff delay, honoring a server `Retry-After` header when present. */
export function retryAfterMs(retryAfter: string | null | undefined, attempt: number): number {
  return serverRetryAfterMs(retryAfter) ?? nextBackoffDelay(attempt);
}

/** Equal jitter: keep half the delay fixed and randomize the other half, so the
 *  result lands in `[delay/2, delay]`. Spreads correlated retries (thundering
 *  herd) without ever waiting longer than the computed backoff. */
export function applyJitter(delayMs: number, random: () => number = Math.random): number {
  if (!Number.isFinite(delayMs) || delayMs <= 0) {
    return 0;
  }
  const half = delayMs / 2;
  return Math.round(half + random() * half);
}

/**
 * Single source of truth for the retry decision shared by the hand-written
 * `HttpClient` and the generated-client adapter. Returns the number of ms to
 * wait before retrying, or `null` when the status is non-retryable or retries
 * are exhausted. A server-advised `Retry-After` is honored verbatim (no jitter);
 * the computed exponential backoff gets equal jitter.
 */
export function retryDelayForStatus(
  status: number,
  attempt: number,
  maxRetries: number,
  retryAfterHeader: string | null | undefined,
  random: () => number = Math.random,
): number | null {
  if (!RETRYABLE_STATUS.has(status) || attempt >= maxRetries) {
    return null;
  }
  const serverMs = serverRetryAfterMs(retryAfterHeader);
  if (serverMs !== null) {
    return serverMs;
  }
  return applyJitter(nextBackoffDelay(attempt), random);
}
