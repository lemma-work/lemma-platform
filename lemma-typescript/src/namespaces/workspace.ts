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
  /** Pass back as `after` for the next page; null on the last one. */
  next_after?: string | null;
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
  /** Whether the stored session still signs you in. */
  working: boolean;
  created_at: string;
  updated_at: string;
  last_used_at: string | null;
  expires_hint_at: string | null;
  has_password: boolean;
}

/** An agent waiting for somebody to sign a site in. */
export interface SignInRequest {
  id: string;
  origin: string;
  /** What the agent is doing, in its own words, to show the person. */
  reason: string;
  status: "PENDING" | "SIGNED_IN" | "DECLINED";
  created_at: string;
  /** Whether the login was kept for next time. */
  saved: boolean;
  /** Why it was not kept, when it was not. */
  saved_detail: string | null;
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

  /** What an agent is asking you to sign in to, and why. */
  signInRequest(requestId: string): Promise<SignInRequest> {
    return this.http.request<SignInRequest>(
      "GET",
      `/web-logins/sign-in-requests/${encodeURIComponent(requestId)}`,
    );
  }

  /**
   * Say you have signed in, so the waiting run can carry on.
   *
   * Refused with a 409 when the browser holds nothing for the site — which
   * usually means the sign-in did not finish. `force` is for sites the check
   * reads wrongly.
   */
  finishSignIn(requestId: string, options: { force?: boolean } = {}): Promise<SignInRequest> {
    return this.http.request<SignInRequest>(
      "POST",
      `/web-logins/sign-in-requests/${encodeURIComponent(requestId)}:finish`,
      { body: { force: Boolean(options.force) } },
    );
  }

  /** Say you cannot sign in, so the agent stops waiting and says so. */
  declineSignIn(requestId: string): Promise<SignInRequest> {
    return this.http.request<SignInRequest>(
      "POST",
      `/web-logins/sign-in-requests/${encodeURIComponent(requestId)}:decline`,
    );
  }
}

export class WorkspaceNamespace {
  constructor(private readonly http: HttpClient) {}

  listFiles(
    options: { path?: string; wake?: boolean; after?: string } = {},
  ): Promise<WorkspaceFileListResponse> {
    return this.http.request<WorkspaceFileListResponse>("GET", "/workspace/files", {
      params: {
        ...(options.path ? { path: options.path } : {}),
        ...(options.wake ? { wake: true } : {}),
        // From a previous response's `nextAfter`. A directory bigger than one
        // page was otherwise a dead end.
        ...(options.after ? { after: options.after } : {}),
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
   * Whether the browser can be watched, without starting anything.
   *
   * `asleep` the computer is paused; `stopped` it is up but the browser is not
   * (its resting state after two idle minutes); `running` there is one now;
   * `unavailable` the relay did not answer, which on an older image stays true
   * until it is replaced; `unsupported` this kind of computer cannot do it.
   */
  browserStatus(): Promise<{ state: string; detail: string | null }> {
    return this.http.request("GET", "/workspace/browser/status");
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
