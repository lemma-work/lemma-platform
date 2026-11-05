/**
 * Thin HTTP layer that wraps fetch with auth injection, error handling,
 * and automatic 401→unauthenticated state propagation.
 */

import type { AuthManager } from "./auth.js";
import { retryDelayForStatus, serverRetryAfterMs, sleep } from "./run-utils.js";
import {
  APP_HEADER_NAME,
  CLIENT_HEADER_NAME,
  clientHeaderValue,
  shouldSendClientHeader,
  type KnownClient,
} from "./version.js";

type RequestParams = Record<string, string | number | boolean | undefined | null>;

/** Set by the server on a response to a deprecated route or field: what was
 *  deprecated and what to call instead (`app/core/api/deprecation.py`). */
const DEPRECATION_HEADER = "x-lemma-deprecation";
const warnedDeprecations = new Set<string>();

/** Warn once per deprecated use, per page or process. The call succeeded; a
 *  warning never changes that. */
export function noteDeprecation(headers: Headers): void {
  const message = headers.get(DEPRECATION_HEADER);
  if (!message || warnedDeprecations.has(message)) return;
  warnedDeprecations.add(message);
  console.warn(`[lemma] ${message}`);
}

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
  /** Which Lemma client this is, sent as `X-Lemma-Client`. Defaults to
   *  `lemma-sdk-ts`. Naming it is what lets the backend tell a person in a
   *  browser from somebody's script. */
  client?: KnownClient;
  /** Sent as `X-Lemma-App` when set. */
  appId?: string;
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

/**
 * The codes a pod connector refuses with when it cannot run for this caller.
 * Each names the pod connector in `details.pod_connector`; the two a person
 * can fix themselves carry `details.connect_url`.
 */
export const CONNECTION_ERROR_CODES = [
  "CONNECTION_REQUIRED",
  "REAUTH_REQUIRED",
  "CONNECTOR_NEEDS_ACCOUNT",
  "CONNECTOR_UNAVAILABLE",
  "CONNECTION_REQUIRES_PERSON",
] as const;

export type ConnectionErrorCode = (typeof CONNECTION_ERROR_CODES)[number];

function detailString(details: unknown, key: string): string | null {
  if (!details || typeof details !== "object") return null;
  const value = (details as Record<string, unknown>)[key];
  return typeof value === "string" && value ? value : null;
}

/**
 * A pod connector that cannot run for the caller, and where to fix it.
 *
 * Thrown for every code in `CONNECTION_ERROR_CODES`, whatever the status, so
 * an app catches one class rather than reading codes:
 *
 * ```ts
 * try { await client.connectors.pod.execute("gmail", "gmail_send_email", payload); }
 * catch (error) {
 *   if (error instanceof ConnectionRequiredError && error.connectUrl) error.connect();
 *   else throw error;
 * }
 * ```
 *
 * `connectUrl` is null when the person reading cannot fix it themselves --
 * a shared account only its owner can reconnect, an installation an admin
 * turned off, a call with no member behind it.
 */
export class ConnectionRequiredError extends ApiError {
  override name = "ConnectionRequiredError";
  declare readonly code: ConnectionErrorCode;
  /** Where to connect or reconnect: the pod's Connections page, on this connector. */
  readonly connectUrl: string | null;
  /** The pod connector's slug. */
  readonly podConnector: string | null;
  /** The catalog connector behind it, e.g. `gmail`, when the server named it. */
  readonly connectorId: string | null;

  constructor(status: number, message: string, code: ConnectionErrorCode, details?: unknown, rawResponse?: unknown) {
    super(status, message, code, details, rawResponse);
    this.connectUrl = detailString(details, "connect_url");
    this.podConnector = detailString(details, "pod_connector");
    this.connectorId = detailString(details, "connector_id");
  }

  /** Reconnecting an expired sign-in, as against connecting for the first time. */
  get isReauth(): boolean {
    return this.code === "REAUTH_REQUIRED";
  }

  /**
   * Go and fix it: open `connectUrl`. Inside an embedded app the page leaves
   * the frame for a new tab, since a pod app's frame may not navigate the
   * window above it; elsewhere the current page goes there, and the
   * Connections page returns the person once connected. Returns false when
   * there is nowhere to go (no `connectUrl`, or no browser).
   */
  connect(options: { newTab?: boolean } = {}): boolean {
    if (!this.connectUrl) return false;
    const view = (globalThis as { window?: Window }).window;
    if (!view) return false;
    let framed = false;
    try {
      framed = view.top !== view;
    } catch {
      framed = true;
    }
    if (options.newTab || framed) {
      view.open(this.connectUrl, "_blank", "noopener");
      return true;
    }
    view.location.assign(this.connectUrl);
    return true;
  }
}

/** Whether a failure is a pod connector asking to be connected. */
export function isConnectionRequiredError(error: unknown): error is ConnectionRequiredError {
  return error instanceof ConnectionRequiredError;
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
  if (code && (CONNECTION_ERROR_CODES as readonly string[]).includes(code)) {
    return new ConnectionRequiredError(status, message, code as ConnectionErrorCode, details, rawResponse);
  }
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

/** Every code in this family relays what a connector's provider answered. */
const CONNECTOR_PROVIDER_CODE_PREFIX = "OPERATION_EXECUTION_";

/**
 * Whether a refusal says the caller's own Lemma session is gone.
 *
 * A 401, unless its code relays a connector's provider: a third party refusing
 * the connected account's API key is not this session ending, and signing
 * somebody out over it sends a person who is signed in to the sign-in page.
 * The server answers that refusal with 424 now; a server from before the
 * change answered 401, and this is what keeps this SDK from trusting it.
 */
export function isSessionRejection(status: number, code?: string): boolean {
  return status === 401 && !code?.startsWith(CONNECTOR_PROVIDER_CODE_PREFIX);
}

export class HttpClient {
  private readonly timeoutMs: number;
  private readonly maxRetries: number;
  private readonly clientHeader: string;
  private readonly appId?: string;

  constructor(
    private readonly apiUrl: string,
    private readonly auth: AuthManager,
    options: HttpClientOptions = {},
  ) {
    this.timeoutMs = options.timeoutMs ?? DEFAULT_TIMEOUT_MS;
    this.maxRetries = options.maxRetries ?? DEFAULT_MAX_RETRIES;
    this.clientHeader = clientHeaderValue(options.client);
    this.appId = options.appId;
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

  /** The error a refused response becomes, once the session has been told
   *  whether it was the one refused. The body has to be read first: the code
   *  in it is what tells a dead session from a connector's rejected key. */
  private async failure(response: Response): Promise<ApiError> {
    const error = await this.parseError(response);
    if (isSessionRejection(error.statusCode, error.code)) {
      this.auth.markUnauthenticated();
    }
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
      ? this.mergeHeaders(withAuth, {
          [CLIENT_HEADER_NAME]: this.clientHeader,
          ...(this.appId ? { [APP_HEADER_NAME]: this.appId } : {}),
        })
      : withAuth;
    return this.mergeHeaders(withClient, options.headers);
  }

  async request<T = unknown>(
    method: string,
    path: string,
    options: RequestOptions = {},
  ): Promise<T> {
    const url = this.buildUrl(path, options.params);
    await this.auth.ready();
    let init = this.buildRequestInit(method, options);
    let renewed = false;

    for (let attempt = 0; ; attempt++) {
      const response = await this.fetchWithTimeout(url, init, options.signal);

      // A framed app holds a short-lived token from the view around it; when
      // the session is refused, ask that view once for another before giving
      // up on it. A connector's rejected key is not the session (see
      // `isSessionRejection`), so it is thrown as it came.
      if (response.status === 401) {
        const error = await this.parseError(response);
        const sessionGone = isSessionRejection(error.statusCode, error.code);
        if (sessionGone && !renewed && (await this.auth.renewEmbeddedToken())) {
          renewed = true;
          init = this.buildRequestInit(method, options);
          continue;
        }
        if (sessionGone) {
          this.auth.markUnauthenticated();
        }
        throw error;
      }

      const retryDelay = retryDelayForStatus(
        response.status,
        method,
        attempt,
        this.maxRetries,
        response.headers.get("retry-after"),
      );
      if (retryDelay !== null) {
        await sleep(retryDelay, options.signal);
        continue;
      }

      if (!response.ok) {
        throw await this.failure(response);
      }
      noteDeprecation(response.headers);

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
    const response = await this.streamResponse(path, options);
    if (!response.body) {
      throw new ApiError(response.status, "Stream response had no body.");
    }
    return response.body;
  }

  /**
   * `stream`, but the whole successful `Response` rather than only its body.
   *
   * For a caller that has to read the status as well as the bytes: a ranged
   * read is only a slice if it came back 206, and a server that ignored the
   * `Range` answers 200 with the file from byte 0.
   */
  async streamResponse(
    path: string,
    options: Omit<RequestOptions, "isFormData"> & { method?: "GET" | "POST" | "PATCH" } = {},
  ): Promise<Response> {
    await this.auth.ready();
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

    if (!response.ok) {
      throw await this.failure(response);
    }

    return response;
  }

  /**
   * A binary response, optionally only part of one.
   *
   * `headers` exists for `Range`. Without it a file past the server's
   * single-read ceiling was simply unreachable through this client: the read
   * was capped and there was no way to ask for the rest.
   */
  async requestBytes(
    method: string,
    path: string,
    options: { headers?: Record<string, string> } = {},
  ): Promise<Blob> {
    return (await this.requestBytesResponse(method, path, options)).blob;
  }

  /**
   * `requestBytes`, with the status and `Content-Range` it came back with.
   *
   * A ranged read needs them: 206 (or a `Content-Range` starting where it
   * asked) is the only evidence the server honoured the `Range` rather than
   * answering with the whole file again.
   */
  async requestBytesResponse(
    method: string,
    path: string,
    options: { headers?: Record<string, string> } = {},
  ): Promise<{ blob: Blob; status: number; contentRange: string | null }> {
    const url = `${this.apiUrl}${path}`;
    await this.auth.ready();
    const init = this.auth.getRequestInit({ method });
    if (options.headers) {
      init.headers = { ...(init.headers as Record<string, string>), ...options.headers };
    }
    const response = await this.fetchWithTimeout(url, init);

    if (!response.ok) {
      throw await this.failure(response);
    }

    return {
      blob: await response.blob(),
      status: response.status,
      contentRange: response.headers.get("Content-Range"),
    };
  }
}
