/**
 * Live datastore change stream over a WebSocket.
 *
 * Connects to the backend `/pods/{podId}/datastore/changes` endpoint and invokes
 * `onChange` for every record insert/update/delete the caller is allowed to see.
 * RLS (per-user) tables deliver only the caller's own rows; shared tables deliver
 * every member's changes — the server enforces this, the client just renders.
 *
 * The browser WebSocket API cannot set request headers, so auth is carried as an
 * `?access_token=` query parameter (resolved from the session). Same-site cookie
 * sessions also work if you pass `useCookie: true` and omit the token.
 *
 * Reconnects with full-jitter backoff and resumes from the last seen `stream_id`
 * so a brief drop replays missed changes rather than losing them.
 */

export interface DatastoreChangeFrame {
  /** e.g. "datastore.record.insert" | ".update" | ".delete" */
  type: string;
  pod_id: string;
  table_name: string;
  record_id: string;
  operation: "insert" | "update" | "delete";
  /**
   * The row as it stands after the write — including columns this write never
   * touched and defaults the database filled in. On `delete` it is the row as
   * it stood before removal, not `{}`.
   *
   * Empty when `payload_truncated` is true: a row too large to carry is not
   * shipped over the stream. Read the record instead of treating `{}` as the
   * row's contents.
   */
  payload: Record<string, unknown>;
  /**
   * The row body was too large to carry and was dropped. `payload` is `{}`;
   * fetch the record by `record_id` if you need its values.
   */
  payload_truncated?: boolean;
  occurred_at?: string;
  /** Redis stream id — pass back as `since` to resume after this change. */
  stream_id?: string;
}

export type ChangeStreamStatus =
  | "connecting"
  | "open"
  | "reconnecting"
  | "closed";

/** Minimal token source; the SDK's AuthManager satisfies this structurally. */
export interface ChangeStreamTokenProvider {
  getAccessToken(): Promise<string>;
  refreshAccessToken(): Promise<string>;
}

export interface WatchChangesOptions {
  /** Restrict to one table; omit to watch every readable table in the pod. */
  table?: string;
  /** Resume after a previously seen `stream_id`. */
  since?: string;
  /** Invoked for every record change frame. */
  onChange: (frame: DatastoreChangeFrame) => void;
  /** Invoked once per connection when the stream is live; carries the resume cursor. */
  onReady?: (info: { since: string }) => void;
  /** Connection lifecycle notifications. */
  onStatus?: (status: ChangeStreamStatus) => void;
  /** Terminal errors (e.g. auth failed, max retries reached). */
  onError?: (error: Error) => void;
  /** Abort to stop the stream (equivalent to calling `handle.close()`). */
  signal?: AbortSignal;
  /** Rely on a same-site session cookie instead of a token in the URL. */
  useCookie?: boolean;
  /** Cap reconnect attempts; unlimited by default. */
  maxRetries?: number;
}

export interface ChangeStreamHandle {
  /** Stop the stream and prevent further reconnects. */
  close(): void;
  /** Whether the stream has been stopped. */
  readonly closed: boolean;
}

const RECONNECT_BASE_DELAY_MS = 500;
const RECONNECT_MAX_DELAY_MS = 30_000;
/** SuperTokens / FastAPI close code for a rejected session. */
const WS_POLICY_VIOLATION = 1008;

function reconnectDelayMs(attempt: number): number {
  const ceiling = Math.min(
    RECONNECT_MAX_DELAY_MS,
    RECONNECT_BASE_DELAY_MS * 2 ** Math.max(0, attempt),
  );
  return Math.random() * ceiling;
}

function changesWsUrl(
  apiUrl: string,
  podId: string,
  table: string | undefined,
  since: string | undefined,
  token: string | null,
): string {
  const root = apiUrl.replace(/\/$/, "").replace(/^http(s?):\/\//, "ws$1://");
  const url = new URL(`${root}/pods/${podId}/datastore/changes`);
  if (table) url.searchParams.set("table", table);
  if (since) url.searchParams.set("since", since);
  if (token) url.searchParams.set("access_token", token);
  return url.toString();
}

/**
 * Open a live datastore change stream. Returns a handle; call `close()` (or abort
 * the provided `signal`) to stop it.
 */
export function watchDatastoreChanges(
  apiUrl: string,
  auth: ChangeStreamTokenProvider,
  podId: string,
  options: WatchChangesOptions,
): ChangeStreamHandle {
  let socket: WebSocket | null = null;
  let cursor = options.since;
  let attempt = 0;
  let stopped = false;
  let reconnectTimer: ReturnType<typeof setTimeout> | null = null;

  const status = (next: ChangeStreamStatus): void => options.onStatus?.(next);

  const scheduleReconnect = (): void => {
    if (stopped) return;
    if (options.maxRetries != null && attempt >= options.maxRetries) {
      stopped = true;
      status("closed");
      options.onError?.(
        new Error("Datastore change stream: max reconnect attempts reached"),
      );
      return;
    }
    const delay = reconnectDelayMs(attempt);
    attempt += 1;
    status("reconnecting");
    reconnectTimer = setTimeout(() => void connect(), delay);
  };

  const connect = async (): Promise<void> => {
    if (stopped) return;
    status(attempt === 0 ? "connecting" : "reconnecting");

    let token: string | null = null;
    if (!options.useCookie) {
      try {
        token = await auth.getAccessToken();
      } catch {
        token = null; // fall back to cookie auth
      }
    }
    if (stopped) return;

    let ws: WebSocket;
    try {
      ws = new WebSocket(changesWsUrl(apiUrl, podId, options.table, cursor, token));
    } catch (error) {
      options.onError?.(error instanceof Error ? error : new Error(String(error)));
      scheduleReconnect();
      return;
    }
    socket = ws;

    ws.onopen = () => {
      attempt = 0; // reset backoff once connected
      status("open");
    };

    ws.onmessage = (event: MessageEvent) => {
      let frame: unknown;
      try {
        frame = JSON.parse(typeof event.data === "string" ? event.data : "");
      } catch {
        return;
      }
      if (!frame || typeof frame !== "object") return;
      const record = frame as Record<string, unknown>;
      if (record.type === "ready") {
        cursor = (record.since as string) || cursor;
        if (cursor) options.onReady?.({ since: cursor });
        return;
      }
      cursor = (record.stream_id as string) || cursor;
      options.onChange(record as unknown as DatastoreChangeFrame);
    };

    ws.onclose = (event: CloseEvent) => {
      socket = null;
      if (stopped) {
        status("closed");
        return;
      }
      // Auth rejected: refresh the token once, then reconnect.
      if (event.code === WS_POLICY_VIOLATION && !options.useCookie) {
        auth.refreshAccessToken().then(scheduleReconnect, scheduleReconnect);
        return;
      }
      scheduleReconnect();
    };

    // onerror is followed by onclose, where reconnect is handled.
    ws.onerror = () => {};
  };

  const close = (): void => {
    if (stopped) return;
    stopped = true;
    if (reconnectTimer != null) clearTimeout(reconnectTimer);
    if (socket) {
      try {
        socket.close(1000, "client closed");
      } catch {
        // ignore
      }
      socket = null;
    }
    status("closed");
  };

  if (options.signal) {
    if (options.signal.aborted) stopped = true;
    else options.signal.addEventListener("abort", close, { once: true });
  }

  if (!stopped) void connect();

  return {
    close,
    get closed() {
      return stopped;
    },
  };
}
