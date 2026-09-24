import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ApiError } from "lemma-sdk";
import { lemma } from "@/session/client";
import { live } from "@/usage/queries";
import { useEffect, useRef } from "react";
import { startupProgress } from "./startup";
import { MAX_READ_BYTES, MAX_TEXT_BYTES, viewerFor, type BrowserState, type Listing } from "./machine";

export function useWorkspaceStatus(enabled: boolean) {
    const cache = useQueryClient();
    const result = useQuery({
        queryKey: ["computer", "status"],
        queryFn: () => lemma().workspace.status(),
        enabled: live() && enabled,
        refetchInterval: (query) => startupProgress(query.state.data) ? 3_000 : 15_000,
        refetchOnWindowFocus: true,
        retry: false,
    });
    const previous = useRef<string | undefined>(undefined);
    const state = result.data?.state;
    useEffect(() => {
        if (state === "ready" && previous.current && previous.current !== "ready") {
            void cache.invalidateQueries({ queryKey: ["computer", "files"] });
            void cache.invalidateQueries({ queryKey: ["computer", "browser"] });
        }
        previous.current = state;
    }, [state, cache]);
    return result;
}

/** Reading the computer without starting it.
 *
 *  The installed SDK has no namespace for these, so the paths are written out
 *  once here rather than at each call site — the same arrangement the usage
 *  calls are in, for the same reason.
 *
 *  One rule runs through the whole file and it is a cost rule, not a
 *  correctness one: **looking must not start a machine.** A paused sandbox is
 *  released after fifteen idle minutes, so a pane that woke one on render
 *  would hold compute for as long as somebody left the tab open. The listing
 *  route takes `wake` and defaults it off, answering `sleeping: true` instead;
 *  the browser status route never provisions at all. Everything that does cost
 *  something — reading a file, opening the browser — is behind a click.
 */

/** One directory of your own sandbox, ambient by default.
 *
 *  `path` is nullable, and null is the important case: it means *the server's
 *  own default*, which is the only way to open this pane without first
 *  guessing a root. The reply carries `home_root` and `workspace_root`, so one
 *  request both lists a directory and says where the machine keeps things —
 *  and a client that asks for nothing cannot ask for the wrong thing.
 *
 *  `wake` is a one-way door within a session: once somebody has asked for the
 *  machine to start, asking again with it off would answer `sleeping` about a
 *  machine that is plainly running, so the caller keeps it on.
 *
 *  Paged, because a workspace holding a `node_modules` is the ordinary case
 *  and the route stops at a thousand entries. Unpaged, a directory with more
 *  than that is a dead end: the rest can be counted and never reached.
 */
export function useFiles(path: string | null, wake: boolean, enabled = true) {
    return useInfiniteQuery({
        queryKey: ["computer", "files", path ?? "", wake],
        initialPageParam: undefined as string | undefined,
        queryFn: ({ pageParam }) => lemma().request<Listing>("GET", "/workspace/files", {
            params: { path: path ?? undefined, wake: wake || undefined, after: pageParam },
        }),
        /* The server's cursor, in the server's order. The view sorts folders
           to the top for reading and must not sort this. */
        getNextPageParam: (page) => page.next_after || undefined,
        enabled: live() && enabled,
        /* An agent writing into this directory changes it under the reader,
           but a listing costs the server a round trip into the sandbox, so
           this refreshes when somebody comes back to it rather than on a
           timer nobody is watching. */
        staleTime: 5_000,
        refetchOnWindowFocus: true,
        retry: false,
    });
}

/** Whether there is a browser to watch. Never starts anything. */
export function useBrowser(enabled: boolean) {
    return useQuery({
        queryKey: ["computer", "browser"],
        queryFn: async () => {
            const found = await lemma().request<{ state?: string; detail?: string | null }>(
                "GET", "/workspace/browser/status",
            );
            return (found?.state ?? "unavailable") as BrowserState;
        },
        enabled: live() && enabled,
        staleTime: 15_000,
        refetchOnWindowFocus: true,
        retry: false,
    });
}

export interface FileBody {
    blob: Blob;
    /** Decoded, or null when this is an image or past the reading ceiling. */
    text: string | null;
    tooLarge: boolean;
    sizeBytes: number;
}

/** One slice of a file, or all of it the server will give at once.
 *
 *  Through `stream` rather than `request`, and that is not a style choice: the
 *  route answers `application/octet-stream`, which `request` hands back as
 *  `response.text()` — a UTF-8 decode that silently replaces every byte a PNG
 *  is made of. The stream gives a real body, so one path serves text, images
 *  and the download alike, and no part of this has to touch a credential to do
 *  it.
 */
async function slice(path: string, options: { length?: number; range?: { start: number; end: number } }): Promise<Blob> {
    const body = await lemma().stream("/workspace/files:content", {
        method: "GET",
        params: { path, length: options.length },
        headers: options.range ? { Range: `bytes=${options.range.start}-${options.range.end}` } : undefined,
    });
    return await new Response(body).blob();
}

export function useFileBody(path: string | null) {
    const viewer = viewerFor(path?.split("/").pop() ?? "");
    return useQuery({
        queryKey: ["computer", "file", path ?? ""],
        queryFn: async (): Promise<FileBody> => {
            if (viewer === "image") {
                /* Exactly the ceiling, not one past it: the route validates
                   `length` against its own maximum and answers 422 for asking
                   over. So a full slice is the signal there may be more, and
                   an image that lands exactly on it is offered rather than
                   drawn — a rare file shown as a download beats a common one
                   shown torn. */
                const whole = await slice(path!, { length: MAX_READ_BYTES });
                const cut = whole.size >= MAX_READ_BYTES;
                return { blob: whole, text: null, tooLarge: cut, sizeBytes: whole.size };
            }
            const blob = await slice(path!, { length: MAX_TEXT_BYTES + 1 });
            if (blob.size > MAX_TEXT_BYTES) return { blob, text: null, tooLarge: true, sizeBytes: blob.size };
            return { blob, text: await blob.text(), tooLarge: false, sizeBytes: blob.size };
        },
        enabled: live() && Boolean(path),
        staleTime: 30_000,
        retry: false,
    });
}

/** One file's own line from the listing, asked for directly.
 *
 *  What this is for is the *size*, and the size matters because the body this
 *  pane holds is capped: a 200 MB log and a 1 MB one both come back as a
 *  megabyte of blob, so the blob cannot say how big the file is. It said so
 *  anyway, and reported every large file as exactly the size of the ceiling.
 *
 *  A stat rather than the listing entry that led here, because the listing is
 *  a snapshot and the interesting files are the ones an agent is still
 *  writing.
 */
export function useFileStat(path: string | null) {
    return useQuery({
        queryKey: ["computer", "stat", path ?? ""],
        queryFn: () => lemma().request<{ size_bytes: number }>(
            "GET", "/workspace/files:stat", { params: { path: path! } },
        ),
        enabled: live() && Boolean(path),
        staleTime: 30_000,
        retry: false,
    });
}

export async function wholeFile(path: string, sizeBytes = 0): Promise<Blob> {
    const parts: Blob[] = [];
    let start = 0;
    if (sizeBytes > 0 && sizeBytes <= MAX_READ_BYTES) {
        const only = await slice(path, {});
        /* Short of the ceiling means that was the whole file. Exactly the
           ceiling means the hint was stale — the file grew after the listing
           that measured it — and returning here would truncate, which is the
           failure this function exists to prevent. */
        if (only.size < MAX_READ_BYTES) return only;
        parts.push(only);
        start = only.size;
    }
    for (;;) {
        let part: Blob;
        try {
            part = await slice(path, { range: { start, end: start + MAX_READ_BYTES - 1 } });
        } catch (error) {
            /* 416. The file ended exactly on a chunk boundary, or it is empty:
               both are "there is nothing at this offset", and neither is a
               failure. Any other status is. */
            if (error instanceof ApiError && error.statusCode === 416) break;
            throw error;
        }
        if (part.size === 0) break;
        parts.push(part);
        start += part.size;
        /* Short of what was asked for means the server ran out of file, which
           is the ordinary way this ends — one request more than the file
           needs, rather than a 416 every time. */
        if (part.size < MAX_READ_BYTES) break;
    }
    return new Blob(parts);
}

/** A signed, expiring URL onto the sandbox's own browser.
 *
 *  It opens in a tab rather than a frame because the proxy sends
 *  `frame-ancestors` naming the platform's own origins and not this app's — a
 *  leaked signed link must not be drivable from somebody else's page, and this
 *  app is on the wrong side of that line. A refused frame would have looked
 *  like a broken pane.
 *
 *  Unlike everything else here it provisions: asking for the browser is asking
 *  for the machine.
 */
export function useBrowserAccess() {
    return useMutation({
        mutationFn: () => lemma().request<{ url: string; expires_at: string }>(
            "POST", "/workspace/apps/browser/access", { body: { ttl_seconds: 1800 } },
        ),
    });
}

/** The same browser, in a tab, for where this pane cannot carry it.
 *
 *  A top-level page is not a framed one, so the proxy's `frame-ancestors` has
 *  nothing to say about it — which makes this the one route to the display
 *  that still works when the socket is refused.
 *
 *  The tab is opened empty inside the click's own turn and pointed at the
 *  grant when it arrives. Opening it in the callback instead is what a browser
 *  calls a popup: the grant takes a round trip and a provision to come back,
 *  by which time the gesture is long over and the tab is blocked. `noopener`
 *  cannot do the severing here because it makes `window.open` hand back
 *  nothing to point, so the reference is cut by hand, which is the same
 *  protection.
 */
export function useOpenBrowserTab() {
    const access = useBrowserAccess();
    const open = () => {
        const opened = window.open("", "_blank");
        access.mutate(undefined, {
            onSuccess: (grant) => {
                if (!opened || opened.closed) { window.open(grant.url, "_blank", "noopener,noreferrer"); return; }
                opened.opener = null;
                opened.location.replace(grant.url);
            },
            onError: () => { if (opened && !opened.closed) opened.close(); },
        });
    };
    return { open, busy: access.isPending, failed: access.isError };
}

/** Fit the sandbox display to the pane showing it.
 *
 *  The pane is a box of an arbitrary shape and the display is a real screen
 *  with a fixed size, so one of them has to move. Scaling the picture is what
 *  made the browser a small letterboxed rectangle ringed with dead space;
 *  resizing the display means the pixels sent are the pixels shown — and a
 *  narrow pane gets a narrow *viewport*, so a site serves its mobile layout to
 *  somebody signing in on a phone.
 *
 *  A failure here is not a failure for the person: they keep the picture they
 *  had. The route says so itself by answering `{size: null}` rather than a
 *  status code, so there is nothing to catch and nothing to show.
 */
export function useBrowserResize() {
    return useMutation({
        mutationFn: (size: { width: number; height: number }) =>
            lemma().request<{ size: string | null }>("POST", "/workspace/browser/display-size", { body: size }),
    });
}

/** Where the browser signing in to `origin` actually is.
 *
 *  Polled, because VNC is pixels rather than events: there is nothing on the
 *  wire to react to the way a navigation message would be. It is the only
 *  thing that can answer "which site am I about to type my password into"
 *  while a sign-in redirects through an identity provider and back.
 */
export function useCurrentPage(origin: string | null, enabled: boolean) {
    return useQuery({
        queryKey: ["computer", "page", origin ?? ""],
        queryFn: async () => {
            const found = await lemma().request<{ url: string | null }>(
                "GET", "/workspace/browser/current-page-url", { params: { origin: origin! } },
            );
            return found?.url ?? null;
        },
        enabled: live() && enabled && Boolean(origin),
        refetchInterval: 1_500,
        staleTime: 0,
        retry: false,
    });
}

/** What a sign-in link is asking for, addressed by the pause it is for.
 *
 *  The conversation and tool call are a lookup, not a credential: the server
 *  resolves both against the caller's own session, so a forwarded link answers
 *  exactly as an invented one does.
 */
export interface PendingSignIn {
    tool_call_id: string;
    origin: string;
    /** What the agent is doing, in its own words, to show the person. */
    reason: string;
}

export function usePendingSignIn(conversationId: string | null, toolCallId: string | null) {
    return useQuery({
        queryKey: ["computer", "sign-in", conversationId ?? "", toolCallId ?? ""],
        queryFn: () => lemma().request<PendingSignIn>(
            "GET",
            "/web-logins/sign-ins/" + encodeURIComponent(conversationId!) + "/" + encodeURIComponent(toolCallId!),
        ),
        enabled: live() && Boolean(conversationId && toolCallId),
        retry: false,
    });
}

export interface SignInOutcome {
    origin: string;
    signed_in: boolean;
    /** Whether the site stopped asking for a login straight afterwards.
     *  Reported, not enforced — the check is a guess, and the person has
     *  already done what was asked. */
    working: boolean;
}

/** Say whether you signed in, so the waiting run can carry on.
 *
 *  **This is the only route back to the paused run.** Nothing detects a
 *  completed login on its own, so a surface that shows the browser without
 *  this lets somebody sign in and leaves the agent waiting for ever. One call
 *  for both answers because it is one answer, and nothing is stored: the
 *  browser holds the session, so finishing is the person finishing.
 */
export function useAnswerSignIn(conversationId: string | null, toolCallId: string | null) {
    const cache = useQueryClient();
    return useMutation({
        mutationFn: (signedIn: boolean) => lemma().request<SignInOutcome>(
            "POST",
            "/web-logins/sign-ins/" + encodeURIComponent(conversationId!) + "/"
                + encodeURIComponent(toolCallId!) + "/answer",
            { body: { signed_in: signedIn } },
        ),
        /* The transcript is where the answered card lives, and the list of
           sites has just gained one. Neither refetches on its own. */
        onSuccess: () => {
            void cache.invalidateQueries({ queryKey: ["computer", "logins"] });
            void cache.invalidateQueries({ queryKey: ["conversation"] });
        },
    });
}

/** One site the sandbox's browser is signed in to. */
export interface WebLogin {
    /** The site, as a person would name it — grouped by registrable domain, so
     *  a host and its API are one login rather than two. */
    site: string;
    cookie_count: number;
    /** When the soonest of them lapses. Null when they are all session
     *  cookies, which is the closest a browser gets to "no idea". */
    expires: string | null;
    /** Whether somebody answered "yes, I signed in" for this site. `false`
     *  means "nobody said so", not "no session". */
    signed_in: boolean;
}

/** The sites your sandbox's browser is signed in to.
 *
 *  Read from the browser every time rather than from a table: it keeps its own
 *  profile, so what it holds is the only true answer. Nothing here returns a
 *  secret, and that is structural rather than a promise — cookie values never
 *  leave the sandbox.
 *
 *  Not paged: this is what one browser is holding, not a table that grows.
 *  `wake` is off until somebody asks, because opening a pane should not be
 *  what starts a computer.
 */
export function useWebLogins(wake: boolean, enabled: boolean) {
    return useQuery({
        queryKey: ["computer", "logins", wake],
        queryFn: () => lemma().request<{ items?: WebLogin[]; sleeping?: boolean }>(
            "GET", "/web-logins", { params: { wake: wake || undefined } },
        ),
        enabled: live() && enabled,
        staleTime: 10_000,
        retry: false,
    });
}

/** Sign the browser out of a site.
 *
 *  Really signs it out, which is why it needs the machine running and says so
 *  rather than reporting a success it did not achieve.
 */
export function useForgetWebLogin() {
    const cache = useQueryClient();
    return useMutation({
        mutationFn: (origin: string) => lemma().request<{ site: string; forgotten: boolean }>(
            "DELETE", "/web-logins", { params: { origin } },
        ),
        /* Both keys: forgetting is done from the woken list, and the sleeping
           one is what renders on the next visit. */
        onSuccess: () => { void cache.invalidateQueries({ queryKey: ["computer", "logins"] }); },
    });
}

export function useConversationDirectory(podId: string, conversationId: string | null) {
    return useQuery({
        queryKey: ["computer", "cwd", podId, conversationId ?? ""],
        queryFn: async () => {
            const found = await lemma(podId).conversations.get(conversationId!, { pod_id: podId });
            const cwd = (found as { workspace_cwd?: string } | null)?.workspace_cwd;
            return typeof cwd === "string" && cwd.startsWith("/") ? cwd : null;
        },
        enabled: live() && Boolean(conversationId),
        staleTime: 5 * 60_000,
        retry: false,
    });
}
