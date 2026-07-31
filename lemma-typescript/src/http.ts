/**
 * Thin HTTP layer that wraps fetch with auth injection, error handling,
 * and automatic 401→unauthenticated state propagation.
 */

import type { AuthManager } from "./auth.js";
import { retryDelayForStatus, serverRetryAfterMs, sleep } from "./run-utils.js";
import {
  CLIENT_HEADER_NAME,
  CLIENT_HEADER_VALUE,
  shouldSendClientHeader,
} from "./version.js";

type RequestParams = Record<string, string | number | boolean | undefined | null>;

interface RequestOptions {
  params?: RequestParams;
  body?: unknown;
  isFormData?: boolean;
  headers?: HeadersInit;
  signal?: AbortSignal;
}

export interface HttpClientOptions {
  /** Per-request timeout in ms (default 30000). Streaming requests are exempt. */
  timeoutMs?: number;
  /** Max automatic retries on 429/502/503/504 (default 2). */
  maxRetries?: number;
}

const DEFAULT_TIMEOUT_MS = 30_000;
const DEFAULT_MAX_RETRIES = 2;

export class ApiError extends Error {
  /** Server correlation id (X-Request-Id) when present — quote it in bug reports. */
  requestId?: string;

  constructor(
    public readonly statusCode: number,
    message: string,
    public readonly code?: string,
    public readonly details?: unknown,
    public readonly rawResponse?: unknown,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

/** 401 — session missing/expired. */
export class UnauthorizedError extends ApiError { override name = "UnauthorizedError"; }
/** 403 — authenticated but not permitted (often an RLS/grant denial). */
export class ForbiddenError extends ApiError { override name = "ForbiddenError"; }
/** 404 — resource not found. */
export class NotFoundError extends ApiError { override name = "NotFoundError"; }
/** 409 — conflict (e.g. duplicate name). */
export class ConflictError extends ApiError { override name = "ConflictError"; }
/** 429 — rate limited; `retryAfterMs` is the server-advised wait if provided. */
export class RateLimitError extends ApiError {
  override name = "RateLimitError";
  constructor(message: string, code?: string, details?: unknown, rawResponse?: unknown, public readonly retryAfterMs?: number) {
    super(429, message, code, details, rawResponse);
  }
}
/** 5xx — server-side error. */
export class ServerError extends ApiError { override name = "ServerError"; }
/** Transport-level failure (DNS, connection refused, timeout) — no HTTP status. */
export class NetworkError extends Error {
  override name = "NetworkError";
  constructor(message: string, public readonly cause?: unknown) {
    super(message);
  }
}

/** Map an HTTP status to the most specific ApiError subclass. */
export function apiErrorFromStatus(
  status: number,
  message: string,
  code?: string,
  details?: unknown,
  rawResponse?: unknown,
  retryAfterMsValue?: number,
): ApiError {
  switch (status) {
    case 401: return new UnauthorizedError(status, message, code, details, rawResponse);
    case 403: return new ForbiddenError(status, message, code, details, rawResponse);
    case 404: return new NotFoundError(status, message, code, details, rawResponse);
    case 409: return new ConflictError(status, message, code, details, rawResponse);
    case 429: return new RateLimitError(message, code, details, rawResponse, retryAfterMsValue);
    default:
      return status >= 500
        ? new ServerError(status, message, code, details, rawResponse)
        : new ApiError(status, message, code, details, rawResponse);
  }
}

export class HttpClient {
  private readonly timeoutMs: number;
  private readonly maxRetries: number;

  constructor(
    private readonly apiUrl: string,
    private readonly auth: AuthManager,
    options: HttpClientOptions = {},
  ) {
    this.timeoutMs = options.timeoutMs ?? DEFAULT_TIMEOUT_MS;
    this.maxRetries = options.maxRetries ?? DEFAULT_MAX_RETRIES;
  }

  getBaseUrl(): string {
    return this.apiUrl;
  }

  /** fetch with a default timeout, normalizing transport failures into NetworkError. */
  private async fetchWithTimeout(url: string, init: RequestInit, userSignal?: AbortSignal): Promise<Response> {
    const controller = new AbortController();
    let timedOut = false;
    const timer = setTimeout(() => {
      timedOut = true;
      controller.abort();
    }, this.timeoutMs);
    const onAbort = () => controller.abort();
    if (userSignal) {
      if (userSignal.aborted) controller.abort();
      else userSignal.addEventListener("abort", onAbort, { once: true });
    }
    try {
      return await fetch(url, { ...init, signal: controller.signal });
    } catch (error) {
      if (timedOut) {
        throw new NetworkError(`Request timed out after ${this.timeoutMs}ms`, error);
      }
      if (userSignal?.aborted) {
        throw error; // caller-initiated abort — propagate the original AbortError
      }
      throw new NetworkError(
        `Network request failed: ${String((error as Error)?.message ?? error)}`,
        error,
      );
    } finally {
      clearTimeout(timer);
      userSignal?.removeEventListener("abort", onAbort);
    }
  }

  private buildUrl(path: string, params?: RequestParams): string {
    let url = `${this.apiUrl}${path}`;
    if (!params) {
      return url;
    }

    const qs = Object.entries(params)
      .filter(([, value]) => value !== undefined && value !== null)
      .map(([key, value]) => `${encodeURIComponent(key)}=${encodeURIComponent(String(value))}`)
      .join("&");

    if (qs) {
      url += `?${qs}`;
    }

    return url;
  }

  private mergeHeaders(base: RequestInit, extra?: HeadersInit): RequestInit {
    if (!extra) {
      return base;
    }

    const merged = new Headers(base.headers ?? {});
    const extraHeaders = new Headers(extra);
    extraHeaders.forEach((value, key) => merged.set(key, value));

    return {
      ...base,
      headers: merged,
    };
  }

  private async parseError(response: Response): Promise<ApiError> {
    let message = response.statusText || "Request failed";
    let code: string | undefined;
    let details: unknown;
    let raw: unknown = null;

    try {
      const body = await response.json();
      raw = body;
      if (body && typeof body === "object") {
        const record = body as Record<string, unknown>;
        if (typeof record.message === "string") {
          message = record.message;
        }
        if (typeof record.code === "string") {
          code = record.code;
        }
        details = record.details;
      }
    } catch {
      // non-JSON error body
    }

    const retryMs =
      response.status === 429
        ? serverRetryAfterMs(response.headers.get("retry-after")) ?? undefined
        : undefined;
    const error = apiErrorFromStatus(response.status, message, code, details, raw, retryMs);
    error.requestId = response.headers.get("x-request-id") ?? undefined;
    return error;
  }

  private getRequestBody(options: RequestOptions): BodyInit | undefined {
    if (options.body === undefined) {
      return undefined;
    }

    if (options.isFormData && options.body instanceof FormData) {
      return options.body;
    }

    return JSON.stringify(options.body);
  }

  private buildRequestInit(method: string, options: RequestOptions): RequestInit {
    const initBase: RequestInit = {
      method,
      body: this.getRequestBody(options),
      signal: options.signal,
    };

    const withAuth = this.auth.getRequestInit(initBase);

    const withClient = shouldSendClientHeader(this.apiUrl, method)
      ? this.mergeHeaders(withAuth, { [CLIENT_HEADER_NAME]: CLIENT_HEADER_VALUE })
      : withAuth;
    return this.mergeHeaders(withClient, options.headers);
  }

  async request<T = unknown>(
    method: string,
    path: string,
    options: RequestOptions = {},
  ): Promise<T> {
    const url = this.buildUrl(path, options.params);
    const init = this.buildRequestInit(method, options);

    for (let attempt = 0; ; attempt++) {
      const response = await this.fetchWithTimeout(url, init, options.signal);

      // Only 401 means the session is gone — 403 is a permission/RLS error, not an auth failure
      if (response.status === 401) {
        this.auth.markUnauthenticated();
      }

      const retryDelay = retryDelayForStatus(
        response.status,
        attempt,
        this.maxRetries,
        response.headers.get("retry-after"),
      );
      if (retryDelay !== null) {
        await sleep(retryDelay, options.signal);
        continue;
      }

      if (!response.ok) {
        throw await this.parseError(response);
      }

      if (response.status === 204) {
        return undefined as unknown as T;
      }

      const contentType = response.headers.get("content-type") ?? "";
      if (contentType.includes("application/json")) {
        return response.json() as Promise<T>;
      }

      return response.text() as unknown as T;
    }
  }

  async stream(
    path: string,
    options: Omit<RequestOptions, "isFormData"> & { method?: "GET" | "POST" | "PATCH" } = {},
  ): Promise<ReadableStream<Uint8Array>> {
    // Streams are deliberately timeout-exempt (they're long-lived), but we still
    // normalize transport failures into NetworkError so the typed-error contract
    // holds on the SSE path too.
    let response: Response;
    try {
      response = await fetch(
        this.buildUrl(path, options.params),
        this.buildRequestInit(options.method ?? "GET", {
          ...options,
          headers: {
            Accept: "text/event-stream",
            ...options.headers,
          },
        }),
      );
    } catch (error) {
      if (options.signal?.aborted) {
        throw error; // caller-initiated abort — propagate the original AbortError
      }
      throw new NetworkError(
        `Network request failed: ${String((error as Error)?.message ?? error)}`,
        error,
      );
    }

    if (response.status === 401) {
      this.auth.markUnauthenticated();
    }

    if (!response.ok) {
      throw await this.parseError(response);
    }

    if (!response.body) {
      throw new ApiError(response.status, "Stream response had no body.");
    }

    return response.body;
  }

  async requestBytes(method: string, path: string): Promise<Blob> {
    const url = `${this.apiUrl}${path}`;
    const response = await this.fetchWithTimeout(url, this.auth.getRequestInit({ method }));

    if (response.status === 401) {
      this.auth.markUnauthenticated();
    }

    if (!response.ok) {
      throw await this.parseError(response);
    }

    return response.blob();
  }
}
