/** Reading a closed VNC socket, and deciding what to do about it.
 *
 *  Split from the pane for the reason everything else in here is: a close code
 *  is a rule, the pane is a canvas, and only one of those can be tested.
 */

/** Why the socket closed, in numbers. These match the `CLOSE_*` constants in
 *  `browser_view_controller.py`, and they are read off the WebSocket's own
 *  close event rather than RFB's `disconnect`, which reports only whether the
 *  close was clean — not enough to tell "no browser is running yet" (worth
 *  retrying, the agent may start one) from "your session ended" (retrying with
 *  the same expired cookie can never succeed). */
const CLOSE_UNAUTHENTICATED = 4401;
const CLOSE_ORIGIN_REFUSED = 4403;
const CLOSE_NO_BROWSER = 4409;
const CLOSE_UNSUPPORTED = 4422;
const CLOSE_STALE_IMAGE = 4426;

export type LiveState =
    | "connecting"
    | "live"
    | "lost"
    | "refused"
    | "signed-out"
    | "no-browser"
    | "unsupported"
    | "stale-image";

export function stateFromClose(code: number): LiveState {
    if (code === CLOSE_UNAUTHENTICATED) return "signed-out";
    if (code === CLOSE_ORIGIN_REFUSED) return "refused";
    if (code === CLOSE_NO_BROWSER) return "no-browser";
    if (code === CLOSE_UNSUPPORTED) return "unsupported";
    if (code === CLOSE_STALE_IMAGE) return "stale-image";
    return "lost";
}

/** States a fresh attempt cannot fix.
 *
 *  Retrying any of these just repeats the same refusal while telling somebody
 *  otherwise: an allowlist that does not name this app, an ended session, a
 *  fabric that cannot do this, an image without the relay. Each needs
 *  something outside this pane to change first.
 */
const SETTLED = new Set<LiveState>(["refused", "signed-out", "unsupported", "stale-image"]);

export function isSettled(state: LiveState): boolean {
    return SETTLED.has(state);
}

/** Backoff with jitter, so a sandbox restart does not have every open pane
 *  retrying in lockstep. */
export function retryDelay(attempt: number): number {
    return Math.min(30_000, 500 * 2 ** attempt) * (0.5 + Math.random() / 2);
}

export function socketUrl(
    api: string,
    options: {
        mode: "view" | "control";
        origin?: string | null;
        conversationId?: string | null;
        /** The bearer token, where this browser has one. Absent on a cookie
         *  session, which is the case that needs no credential in a URL. */
        accessToken?: string | null;
    },
): string {
    const base = api.replace(/^http/, "ws").replace(/\/$/, "");
    const query = new URLSearchParams({ mode: options.mode });
    /* One or the other, never both: a sign-in names the site it is for, and
       the server resolves the session from that. Sending a conversation beside
       it would be a second answer to a question with one. */
    if (options.origin) query.set("origin", options.origin);
    else if (options.conversationId) query.set("conversation", options.conversationId);
    if (options.accessToken) query.set("access_token", options.accessToken);
    return `${base}/workspace/browser/view?${query.toString()}`;
}
