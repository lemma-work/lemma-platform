import { noteDeprecation, type HttpClient } from "../http.js";
import { MachinesNamespace } from "./machines.js";

export { MAX_READ_BYTES } from "./machines.js";

/** The ref every `workspace` call is for: the person's own default machine. */
const ME = "me";

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

export interface WebLogin {
    /**
     * The site, as a person would name it.
     *
     * Grouped by registrable domain, so `lemma.work` and `api.lemma.work` are one
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
    pendingSignIn(
        conversationId: string,
        toolCallId: string,
    ): Promise<PendingSignIn> {
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

/**
 * The caller's own computer, as it was addressed before machines.
 *
 * @deprecated Use `client.machines` with the ref `"me"`. Every method here
 * keeps its old signature and answer but calls the `/machines/me` route, and
 * the alias goes in the next minor release.
 */
/** Warn once per method, the way a deprecated route's header does. */
function deprecated(use: string, replacement: string): void {
    noteDeprecation(
        new Headers({
            "x-lemma-deprecation": `${use} is deprecated: use ${replacement}.`,
        }),
    );
}

export class WorkspaceNamespace {
    private readonly machines: MachinesNamespace;

    constructor(http: HttpClient, machines?: MachinesNamespace) {
        this.machines = machines ?? new MachinesNamespace(http);
    }

    /** @deprecated Use `machines.files.list("me", options)`. */
    listFiles(
        options: { path?: string; wake?: boolean; after?: string } = {},
    ): Promise<WorkspaceFileListResponse> {
        deprecated("workspace.listFiles", 'machines.files.list("me")');
        return this.machines.files.list(
            ME,
            options,
        ) as unknown as Promise<WorkspaceFileListResponse>;
    }

    /** @deprecated Use `machines.files.stat("me", path)`. */
    statFile(path: string): Promise<WorkspaceFileEntry> {
        deprecated("workspace.statFile", 'machines.files.stat("me", path)');
        return this.machines.files.stat(
            ME,
            path,
        ) as unknown as Promise<WorkspaceFileEntry>;
    }

    /**
     * A signed, short-lived URL for the live browser view.
     *
     * @deprecated Use `machines.browser.access("me", { ttlSeconds })`, which
     * answers without the `app` field: there is only ever the browser.
     */
    async browserAccess(ttlSeconds = 1800): Promise<{
        app: string;
        url: string;
        expires_at: string;
    }> {
        deprecated("workspace.browserAccess", 'machines.browser.access("me")');
        const access = await this.machines.browser.access(ME, { ttlSeconds });
        return {
            app: "browser",
            url: access.url,
            expires_at: access.expires_at,
        };
    }

    /**
     * Whether this person's computer is ready, without starting anything.
     *
     * @deprecated Use `machines.startup("me")`.
     */
    async status(): Promise<{
        state: "ready" | "downloading" | "starting" | "asleep" | "unavailable";
        detail: string | null;
        done_mb?: number | null;
        total_mb?: number | null;
    }> {
        deprecated("workspace.status", 'machines.startup("me")');
        const startup = await this.machines.startup(ME);
        return {
            state: startup.state as
                "ready" | "downloading" | "starting" | "asleep" | "unavailable",
            detail: startup.detail ?? null,
            done_mb: startup.done_mb,
            total_mb: startup.total_mb,
        };
    }

    /** @deprecated Use `machines.browser.status("me")`. */
    async browserStatus(): Promise<{ state: string; detail: string | null }> {
        deprecated("workspace.browserStatus", 'machines.browser.status("me")');
        const status = await this.machines.browser.status(ME);
        return { state: status.state, detail: status.detail ?? null };
    }

    /** @deprecated Use `machines.browser.currentPageUrl("me", origin)`. */
    browserCurrentPageUrl(origin: string): Promise<{ url: string | null }> {
        deprecated(
            "workspace.browserCurrentPageUrl",
            'machines.browser.currentPageUrl("me", origin)',
        );
        return this.machines.browser.currentPageUrl(ME, origin);
    }

    /** @deprecated Use `machines.browser.display("me", { width, height })`. */
    browserResizeDisplay(
        width: number,
        height: number,
    ): Promise<{ size: string | null }> {
        deprecated(
            "workspace.browserResizeDisplay",
            'machines.browser.display("me", size)',
        );
        return this.machines.browser.display(ME, { width, height });
    }

    /** @deprecated Use `machines.files.read("me", path, options)`. */
    readFile(
        path: string,
        options: {
            offset?: number;
            length?: number;
            range?: { start: number; end: number };
        } = {},
    ): Promise<Blob> {
        deprecated("workspace.readFile", 'machines.files.read("me", path)');
        return this.machines.files.read(ME, path, options);
    }

    /** @deprecated Use `machines.files.readWhole("me", path, sizeBytes, chunk)`. */
    readWholeFile(path: string, sizeBytes = 0, chunk?: number): Promise<Blob> {
        deprecated(
            "workspace.readWholeFile",
            'machines.files.readWhole("me", path)',
        );
        return this.machines.files.readWhole(ME, path, sizeBytes, chunk);
    }
}
