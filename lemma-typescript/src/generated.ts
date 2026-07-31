import type { AuthManager } from "./auth.js";
import { NetworkError, apiErrorFromStatus } from "./http.js";
import { ApiError as GeneratedApiError } from "./openapi_client/core/ApiError.js";
import { CancelablePromise } from "./openapi_client/core/CancelablePromise.js";
import { OpenAPI } from "./openapi_client/core/OpenAPI.js";
import { retryDelayForStatus, sleep } from "./run-utils.js";
import {
  CLIENT_HEADER_NAME,
  CLIENT_HEADER_VALUE,
  shouldSendClientHeader,
} from "./version.js";

const DEFAULT_MAX_RETRIES = 2;
const DEFAULT_TIMEOUT_MS = 30_000;

function extractMessage(body: unknown, fallback: string): string {
  if (body && typeof body === "object" && typeof (body as Record<string, unknown>).message === "string") {
    return (body as Record<string, string>).message;
  }
  return fallback;
}

function extractCode(body: unknown): string | undefined {
  if (body && typeof body === "object" && typeof (body as Record<string, unknown>).code === "string") {
    return (body as Record<string, string>).code;
  }
  return undefined;
}

function extractDetails(body: unknown): unknown {
  if (body && typeof body === "object" && "details" in (body as Record<string, unknown>)) {
    return (body as Record<string, unknown>).details;
  }
  return undefined;
}

export class GeneratedClientAdapter {
  private readonly maxRetries: number;
  private readonly timeoutMs: number;

  constructor(
    private readonly apiUrl: string,
    private readonly auth: AuthManager,
    options: { maxRetries?: number; timeoutMs?: number } = {},
  ) {
    this.maxRetries = options.maxRetries ?? DEFAULT_MAX_RETRIES;
    this.timeoutMs = options.timeoutMs ?? DEFAULT_TIMEOUT_MS;
  }

  private configure(): void {
    OpenAPI.BASE = this.apiUrl;
    OpenAPI.WITH_CREDENTIALS = true;
    OpenAPI.CREDENTIALS = this.auth.isTokenMode ? "omit" : "include";
    OpenAPI.TOKEN = this.auth.getBearerToken() ?? undefined;
    OpenAPI.HEADERS = async (options): Promise<Record<string, string>> => {
      if (shouldSendClientHeader(this.apiUrl, options.method)) {
        return { [CLIENT_HEADER_NAME]: CLIENT_HEADER_VALUE };
      }
      return {};
    };
  }

  async request<T>(operation: () => PromiseLike<T>): Promise<T> {
    this.configure();

    for (let attempt = 0; ; attempt++) {
      try {
        return await this.runWithTimeout(operation);
      } catch (error) {
        if (error instanceof GeneratedApiError) {
          if (error.status === 401) {
            this.auth.markUnauthenticated();
          }

          // Retry transient gateway/rate-limit statuses. The generated client
          // doesn't expose response headers, so there's no Retry-After to honor
          // (retryDelayForStatus falls back to jittered backoff).
          const retryDelay = retryDelayForStatus(error.status, attempt, this.maxRetries, null);
          if (retryDelay !== null) {
            await sleep(retryDelay);
            continue;
          }

          throw apiErrorFromStatus(
            error.status,
            extractMessage(error.body, error.message),
            extractCode(error.body),
            extractDetails(error.body),
            error.body,
          );
        }

        // Not an HTTP error (timeout/cancellation, programming error, raw
        // transport failure) — preserve it untouched.
        throw error;
      }
    }
  }

  /**
   * Enforce a per-attempt timeout on the generated client (which exposes no
   * timeout of its own). The generated operation returns a CancelablePromise;
   * on timeout we cancel it (aborting the underlying fetch) and surface a
   * NetworkError, matching HttpClient.fetchWithTimeout. Non-cancelable or
   * disabled-timeout cases fall through untouched.
   */
  private async runWithTimeout<T>(operation: () => PromiseLike<T>): Promise<T> {
    const op = operation();
    if (this.timeoutMs <= 0 || !(op instanceof CancelablePromise)) {
      return op as Promise<T>;
    }
    let timer: ReturnType<typeof setTimeout> | undefined;
    try {
      return await Promise.race([
        op as Promise<T>,
        new Promise<never>((_, reject) => {
          timer = setTimeout(() => {
            op.cancel();
            reject(new NetworkError(`Request timed out after ${this.timeoutMs}ms`));
          }, this.timeoutMs);
        }),
      ]);
    } finally {
      if (timer) {
        clearTimeout(timer);
      }
    }
  }
}
