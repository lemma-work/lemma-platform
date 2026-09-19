import { ApiError } from "../http.js";
import type { HttpClient } from "../http.js";

/**
 * The most the server will return from one read, whatever is asked for.
 *
 * `_MAX_CONTENT_BYTES` in the files controller. Kept here so the chunked
 * reader clamps to it rather than discovering it a slice at a time.
 */
export const MAX_READ_BYTES = 8 * 1024 * 1024;

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
  /**
   * The durable root, and the furthest up a caller may browse.
   *
   * Served rather than assumed. This path has moved once already, and the
   * clients holding a hardcoded copy went on asking for a directory that no
   * longer existed — which lists identically to an empty one.
   */
  home_root: string;
  /** Where projects live, inside `home_root`. Where a browser should open. */
  workspace_root: string;
  /** The workspace is paused and was not started to answer. */
  sleeping: boolean;
  /** The directory holds more entries than were returned. */
  truncated: boolean;
  /** Pass back as `after` for the next page; null on the last one. */
  next_after?: string | null;
  /** False when the directory is not there, as against merely empty. */
  exists: boolean;
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
  /**
   * The site, as a person would name it.
   *
   * Grouped by registrable domain, so `asur.work` and `api.asur.work` are one
   * login rather than two — the second being the half nobody visited on
   * purpose.
   */
  site: string;
  /** How many cookies it has. A rough sense of scale, not a health check. */
  cookie_count: number;
  /**
   * When the soonest of them lapses, which is the closest a browser can come
   * to "when will I have to sign in again". Null when they are all session
   * cookies.
   */
  expires: string | null;
  /**
   * Whether somebody answered "yes, I signed in" to a sign-in request for
   * this site.
   *
   * The cookies cannot say this on their own, and that is measured rather
   * than assumed: a real profile held two HttpOnly session cookies for a
   * site somebody was signed in to, and six HttpOnly cookies for one that
   * had merely had a video played on it. `false` means "nobody said so", not
   * "no session" — the browser may well still be signed in.
   */
  signed_in: boolean;
}

/** An agent waiting for somebody to sign a site in.
 *
 * There is no status here and no id of its own: the paused tool call is the
 * request, so "still waiting" is whether this comes back at all.
 */
export interface PendingSignIn {
  tool_call_id: string;
  origin: string;
  /** What the agent is doing, in its own words, to show the person. */
  reason: string;
}

/** What came of somebody answering. */
export interface SignInOutcome {
  origin: string;
  signed_in: boolean;
  /**
   * Whether the site stopped asking for a login straight afterwards.
   * Reported, not enforced — the check is a heuristic and the person has
   * already done what was asked.
   */
  working: boolean;
}

/**
 * The sites your sandbox's browser is signed in to.
 *
 * Read from the browser every time, not from a table: it keeps its own
 * profile, so what it holds is the only true answer. Nothing here returns a
 * secret, and that is now structural rather than a promise — cookie values
 * never leave the sandbox at all.
 */
export class WebLoginsNamespace {
  constructor(private readonly http: HttpClient) {}

  /**
   * Every site the browser is signed in to.
   *
   * Not paged: this is what one browser is holding, not a table that grows.
   * `wake` is off by default so that rendering the list is never what starts
   * somebody's computer — a paused one answers `sleeping`.
   */
  list(
    options: { wake?: boolean } = {},
  ): Promise<{ items: WebLogin[]; sleeping: boolean }> {
    const params: Record<string, string | number> = {};
    if (options.wake) params.wake = "true";
    return this.http.request("GET", "/web-logins", { params });
  }

  /**
   * Sign the browser out of a site.
   *
   * Really signs it out, which its predecessor did not: that removed Lemma's
   * encrypted copy and left the browser as it was. Needs the computer
   * running, and says so rather than reporting a success it did not achieve.
   */
  remove(origin: string): Promise<{ site: string; forgotten: boolean }> {
    return this.http.request("DELETE", "/web-logins", {
      params: { origin },
    });
  }

  /** What a sign-in link is asking for, addressed by the pause it is for.
   *
   * The conversation and tool call are a lookup, not a credential: the server
   * resolves both against the caller's own session, so a forwarded link answers
   * exactly as an invented one does.
   */
  pendingSignIn(conversationId: string, toolCallId: string): Promise<PendingSignIn> {
    return this.http.request<PendingSignIn>(
      "GET",
      `/web-logins/sign-ins/${encodeURIComponent(conversationId)}/${encodeURIComponent(toolCallId)}`,
    );
  }

  /**
   * Say whether you signed in, so the waiting run can carry on.
   *
   * One call for both answers because it is one answer. Nothing is stored:
   * the browser holds the session, so finishing is the person finishing. The
   * reply says whether the site stopped asking, which the agent is told.
   */
  answerSignIn(
    conversationId: string,
    toolCallId: string,
    options: { signedIn: boolean },
  ): Promise<SignInOutcome> {
    return this.http.request<SignInOutcome>(
      "POST",
      `/web-logins/sign-ins/${encodeURIComponent(conversationId)}/${encodeURIComponent(toolCallId)}/answer`,
      { body: { signed_in: options.signedIn } },
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
   * What page the browser signing in to `origin` is actually showing.
   *
   * Polled by the sign-in page's anti-phishing host display while its VNC
   * pane is open: VNC is pixels, not events, so there is nothing on the wire
   * to react to the way the JSON stream this replaced had with its `url`
   * message on every navigation. `null` when nothing can be read.
   */
  browserCurrentPageUrl(origin: string): Promise<{ url: string | null }> {
    return this.http.request("GET", "/workspace/browser/current-page-url", {
      params: { origin },
    });
  }

  /**
   * Fit the workspace display to the pane showing it.
   *
   * The pane is a box of an arbitrary shape and the display is a real screen
   * with a fixed size, so one of them has to move. Scaling the picture is
   * what made the browser a small letterboxed rectangle; resizing the display
   * means the pixels sent are the pixels shown, and a narrow pane gets a
   * narrow *viewport* — so sites serve their mobile layout on a phone.
   *
   * `size` is what the display actually became, which may be smaller than
   * asked for: the sandbox's framebuffer is a ceiling. `null` when nothing
   * could be resized (a sleeping computer, an older image), which is not an
   * error — the pane keeps the picture it had.
   */
  browserResizeDisplay(
    width: number,
    height: number,
  ): Promise<{ size: string | null }> {
    return this.http.request("POST", "/workspace/browser/display-size", {
      body: { width, height },
    });
  }

  /**
   * Raw bytes of one file, from `offset`, at most `length` bytes.
   *
   * The query is built into the path because `requestBytes` takes no options —
   * it is the byte-returning sibling of `request`, not a full request builder.
   */
  /**
   * A file, or a slice of one.
   *
   * `range` sends an HTTP `Range` header and gets a 206 back. That is how a
   * file larger than the server's single-read ceiling is reachable at all:
   * ask for it a piece at a time. See `readWholeFile`, which does that for
   * you.
   */
  readFile(
    path: string,
    options: { offset?: number; length?: number; range?: { start: number; end: number } } = {},
  ): Promise<Blob> {
    const query = new URLSearchParams({ path });
    if (options.offset) query.set("offset", String(options.offset));
    if (options.length) query.set("length", String(options.length));
    return this.http.requestBytes("GET", `/workspace/files:content?${query.toString()}`, {
      headers: options.range
        ? { Range: `bytes=${options.range.start}-${options.range.end}` }
        : undefined,
    });
  }

  /**
   * A whole file, however big, in as many requests as that takes.
   *
   * The server caps one read at 8 MiB, which used to mean a larger file
   * could be listed and never opened — the pane offered a download that
   * silently returned the first 8 MiB under the full name. Ranges are
   * requested in order and stitched, so what a person saves is the file.
   *
   * **The size is discovered, not trusted.** This took a `sizeBytes` and
   * stopped there, which made the caller's bookkeeping load-bearing for
   * whether a download was complete. The explorer's was wrong on the case
   * that matters: the open file lives in the URL and its size lived in React
   * state, so a reload restored the path with a size of 0 and every download
   * after it truncated at 8 MiB, under the whole file's name. Reading until
   * the server returns a short slice needs nobody to have remembered
   * anything. `sizeBytes` survives only as a hint that lets a small file skip
   * straight to a single unranged read; passing 0 or nothing is correct.
   *
   * `chunk` is clamped to the server's ceiling rather than trusted either.
   * Asking for 64 MiB got 8 MiB back and advanced the cursor by 64, so seven
   * eighths of the file was skipped and the result was a corrupt download of
   * roughly the right length — the same failure, reintroduced by the
   * parameter meant to tune it.
   */
  async readWholeFile(
    path: string,
    sizeBytes = 0,
    chunk = MAX_READ_BYTES,
  ): Promise<Blob> {
    const step = Math.min(Math.max(Math.floor(chunk), 1), MAX_READ_BYTES);
    const parts: Blob[] = [];
    let start = 0;
    if (sizeBytes > 0 && sizeBytes <= step) {
      const only = await this.readFile(path);
      // Short of the server's ceiling means that was the whole file. Exactly
      // the ceiling means the hint was stale -- the file grew after the
      // listing that measured it -- and returning here would truncate at
      // 8 MiB, which is the failure this method exists to prevent. So keep
      // what arrived and carry on reading from where it stopped.
      if (only.size < MAX_READ_BYTES) return only;
      parts.push(only);
      start = only.size;
    }
    for (;;) {
      let part: Blob;
      try {
        part = await this.readFile(path, {
          range: { start, end: start + step - 1 },
        });
      } catch (error) {
        // 416. The file ended exactly on a chunk boundary, or it is empty:
        // both are "there is nothing at this offset", and neither is a
        // failure. Any other status is.
        if (error instanceof ApiError && error.statusCode === 416) break;
        throw error;
      }
      if (part.size === 0) break;
      parts.push(part);
      start += part.size;
      // Short of what was asked for means the server ran out of file, which
      // is the ordinary way this ends — one request more than the file
      // needs, rather than a 416 every time.
      if (part.size < step) break;
    }
    return new Blob(parts);
  }
}
