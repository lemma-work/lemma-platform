import type { HttpClient } from "../http.js";

/** One entry in a workspace directory listing. */
export interface WorkspaceFileEntry {
  path: string;
  name: string;
  kind: "file" | "directory" | "symlink";
  size_bytes: number;
  modified_at: string;
}

export interface WorkspaceFileListResponse {
  path: string;
  /** The workspace is paused and was not started to answer. */
  sleeping: boolean;
  /** The directory holds more entries than were returned. */
  truncated: boolean;
  entries: WorkspaceFileEntry[];
}

/**
 * The caller's own sandbox files, read-only.
 *
 * Hand-written rather than generated because these routes are keyed by the
 * session's user rather than by a pod, so they carry no `podId` and sit outside
 * every pod-scoped service the generator produces.
 *
 * `list` does not start a paused workspace unless asked. That is the whole point
 * of `sleeping`: a file pane that started a sandbox on every render would hold
 * compute open for as long as it was on screen.
 */
export interface WebLogin {
  id: string;
  origin: string;
  label: string;
  kind: 'SESSION' | 'CREDENTIAL';
  created_at: string;
  updated_at: string;
  last_used_at: string | null;
  expires_hint_at: string | null;
  has_password: boolean;
}

export interface WebLoginAuditEntry {
  origin: string;
  action: string;
  outcome: string;
  actor: string | null;
  detail: string | null;
  created_at: string;
}

/**
 * Saved site logins.
 *
 * Nothing here ever returns a secret — not to an agent, and not to the person
 * who created it. `WebLogin` has no field to put one in.
 */
export class WebLoginsNamespace {
  constructor(private readonly http: HttpClient) {}

  list(): Promise<{ items: WebLogin[] }> {
    return this.http.request<{ items: WebLogin[] }>("GET", "/web-logins");
  }

  /**
   * Forget a site.
   *
   * Revokes Lemma's copy and nothing else: the session stays valid at the site
   * until it expires or the person logs out there.
   */
  remove(origin: string): Promise<WebLogin> {
    return this.http.request<WebLogin>("DELETE", "/web-logins", {
      params: { origin },
    });
  }

  history(limit = 100): Promise<{ items: WebLoginAuditEntry[] }> {
    return this.http.request<{ items: WebLoginAuditEntry[] }>(
      "GET",
      "/web-logins/history",
      { params: { limit } },
    );
  }
}

export type TakeoverStatus = 'pending' | 'done' | 'cancelled';

export interface TakeoverRequest {
  request_id: string;
  origin: string;
  reason: string;
  status: TakeoverStatus;
  conversation_id: string | null;
  created_at: string;
}

export interface TakeoverSession extends TakeoverRequest {
  /** Signed URL of the live browser view. */
  url: string;
  expires_at: string;
}

export class WorkspaceNamespace {
  constructor(private readonly http: HttpClient) {}

  listFiles(
    options: { path?: string; wake?: boolean } = {},
  ): Promise<WorkspaceFileListResponse> {
    return this.http.request<WorkspaceFileListResponse>("GET", "/workspace/files", {
      params: {
        ...(options.path ? { path: options.path } : {}),
        ...(options.wake ? { wake: true } : {}),
      },
    });
  }

  statFile(path: string): Promise<WorkspaceFileEntry> {
    return this.http.request<WorkspaceFileEntry>("GET", "/workspace/files:stat", {
      params: { path },
    });
  }

  /**
   * A signed, short-lived URL for the live browser view.
   *
   * Minting one starts the workspace if it is paused, so ask whether it is
   * awake before calling this rather than after.
   */
  browserAccess(ttlSeconds = 1800): Promise<{
    app: string;
    url: string;
    expires_at: string;
  }> {
    return this.http.request("POST", "/workspace/apps/browser/access", {
      body: { ttl_seconds: ttlSeconds },
    });
  }

  /**
   * Keep the browser awake while somebody is watching.
   *
   * Watching is not a command, and `agent-browser` closes Chrome after two
   * minutes without one — so a live view with nobody typing goes dark on its
   * own unless something touches it.
   */
  heartbeatBrowser(): Promise<void> {
    return this.http.request<void>("POST", "/workspace/apps/browser/heartbeat");
  }

  /** Ask the person to drive the browser, and get the id that addresses it. */
  createTakeover(body: {
    origin: string;
    conversation_id?: string;
    reason?: string;
  }): Promise<TakeoverRequest> {
    return this.http.request<TakeoverRequest>("POST", "/workspace/takeover", { body });
  }

  /**
   * Open a takeover.
   *
   * The id is a lookup, never a credential: the server checks it against the
   * caller's own session, which is what makes the link safe to send through a
   * chat platform whose unfurl bot fetches every URL it is shown.
   */
  openTakeover(requestId: string): Promise<TakeoverSession> {
    return this.http.request<TakeoverSession>(
      "GET",
      `/workspace/takeover/${encodeURIComponent(requestId)}`,
    );
  }

  /** Keep the browser alive while somebody is still typing into it. */
  heartbeatTakeover(requestId: string): Promise<void> {
    return this.http.request<void>(
      "POST",
      `/workspace/takeover/${encodeURIComponent(requestId)}:heartbeat`,
    );
  }

  resolveTakeover(requestId: string, done: boolean): Promise<TakeoverRequest> {
    return this.http.request<TakeoverRequest>(
      "POST",
      `/workspace/takeover/${encodeURIComponent(requestId)}:resolve`,
      { params: { done } },
    );
  }

  /**
   * Raw bytes of one file, from `offset`, at most `length` bytes.
   *
   * The query is built into the path because `requestBytes` takes no options —
   * it is the byte-returning sibling of `request`, not a full request builder.
   */
  readFile(
    path: string,
    options: { offset?: number; length?: number } = {},
  ): Promise<Blob> {
    const query = new URLSearchParams({ path });
    if (options.offset) query.set("offset", String(options.offset));
    if (options.length) query.set("length", String(options.length));
    return this.http.requestBytes("GET", `/workspace/files:content?${query.toString()}`);
  }
}
