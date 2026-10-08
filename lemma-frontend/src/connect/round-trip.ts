import { useEffect, useRef } from "react";
import { isDesktop } from "@/desktop/bridge";
import { openExternal } from "@/desktop/open-external";

/** Getting somebody back from a provider's consent page, and hearing how it went.
 *
 *  The backend ends every OAuth callback with a redirect to the app, carrying
 *  the outcome as `?connect=…&connector=…&account=…&reason=…`. Where it lands is
 *  the connect request's `return_to` — and this app never sent one, so every
 *  round trip ended on the app root in the provider's tab, with the page that
 *  started it still offering a "Done?" nobody had reason to press.
 *
 *  `return_to` points at `/oauth/complete`, which exists to hand the outcome
 *  to the tab that opened it and close itself. `from` is where that page sends
 *  the browser instead when there is no tab to hand it to.
 *
 *  A tab is not always reachable through `window.opener`: a consent chain that
 *  passes through a page cutting the link leaves the pop-up with none, and a
 *  blocked pop-up becomes an ordinary tab with none either. Without a second
 *  way over, that tab navigated *itself* back into the app and the person was
 *  left in a second copy of it rather than in the one they started from. So the
 *  outcome also travels on a channel both tabs hear, which needs no opener —
 *  see `deliverOutcome`.
 */

export const COMPLETE_PATH = "/oauth/complete";

/** What the round trip reports: connected, install_required, pending_approval,
 *  install_received or error. */
export interface ConnectOutcome {
    connect: string;
    connector: string | null;
    account: string | null;
    reason: string | null;
    /** The backend's error code, on an `error` outcome. */
    code?: string | null;
}

/** What a message about an outcome says it is, so a listener on either the
 *  channel or the window can tell this app's messages from a stranger's. */
export const CONNECT_SOURCE = "lemma-connect";

/** The channel the provider's tab reports on when it cannot reach the tab that
 *  started the flow. Named once here, because both ends have to agree. */
export const CONNECT_CHANNEL = "lemma:connect";

/** The current address with `extra` merged into its query — the page to come
 *  back to, with whatever reopens the panel that started it. */
export function hereWith(extra: Record<string, string> = {}): string {
    const url = new URL(window.location.href);
    for (const [key, value] of Object.entries(extra)) url.searchParams.set(key, value);
    return url.pathname + url.search;
}

/** The `return_to` for a connect request started from `from`. A rooted path:
 *  the backend refuses anything with a host in it. */
export function completionPath(from: string = hereWith()): string {
    return COMPLETE_PATH + "?from=" + encodeURIComponent(from);
}

/** When a provider's page was last opened from here, or 0 once its outcome
 *  has come back. Returning to the tab is only worth a refetch while a round
 *  trip could still be finishing: without this every focus — every switch
 *  back from an editor, for an hour — re-read the connector lists.
 *  Module-level because the tab is one, whichever panel started it. */
let sentAt = 0;

/** How long after opening a provider's page a return is still taken as
 *  somebody coming back from it. Consent pages are quick; a person who wanders
 *  off for longer has the "Done?" button. */
const ROUND_TRIP_MS = 15 * 60_000;

/** Where a round trip in flight is recorded, so the provider's tab can tell
 *  whether the tab that started it is still there to be told.
 *
 *  `localStorage` rather than `sessionStorage`, which is per tab: the whole
 *  point is for another tab to read what this one wrote. Storage can be
 *  refused (a partitioned or private context), and then this reads as "nobody
 *  is waiting" — the provider's tab takes the person back itself, which is
 *  where it started. */
const WAITING_KEY = "lemma:connect:waiting";

/** Whether what the tab that started a round trip wrote still means it is
 *  waiting for the answer. Judged from the mark and the clock alone, so the
 *  rule can be checked without a browser. */
export function isWaiting(mark: string | null, now: number = Date.now()): boolean {
    const at = Number(mark);
    return Number.isFinite(at) && at > 0 && now - at < ROUND_TRIP_MS;
}

/** Whether a tab is still waiting, as this tab can see. */
export function waitingForOutcome(): boolean {
    try {
        return isWaiting(window.localStorage.getItem(WAITING_KEY));
    } catch {
        return false;
    }
}

function rememberWaiting(at: number): void {
    try {
        window.localStorage.setItem(WAITING_KEY, String(at));
    } catch {
        /* Nothing to do about it: the provider's tab then comes home itself. */
    }
}

function forgetWaiting(): void {
    try {
        window.localStorage.removeItem(WAITING_KEY);
    } catch {
        /* Nothing was stored. */
    }
}

/** The channel both tabs hear on, held open so a message posted just before
 *  this page unloads is not dropped with a channel that was only borrowed. */
let announcements: BroadcastChannel | null = null;

/** Post an outcome on the channel. `deliverOutcome` is what decides to. */
export function announceOutcome(outcome: ConnectOutcome): void {
    if (typeof BroadcastChannel !== "function") return;
    announcements ??= new BroadcastChannel(CONNECT_CHANNEL);
    announcements.postMessage({ source: CONNECT_SOURCE, ...outcome });
}

/** Hand an outcome to the tab that started the round trip.
 *
 *  The opener first: it is direct, and a tab that can be reached that way is
 *  reached for certain. With none, the channel — and only while a tab is
 *  still waiting for an answer, so a tab somebody opened by hand (where there
 *  is no tab to report to, and `window.close` would be refused) still becomes
 *  the app rather than closing on a message nobody asked for. `"nowhere"`
 *  means neither way worked, and the caller takes the person back itself.
 */
export function deliverOutcome(
    outcome: ConnectOutcome,
    to: {
        opener: { closed: boolean; postMessage(message: unknown, targetOrigin: string): void } | null;
        origin: string;
        waiting: boolean;
        broadcast: (outcome: ConnectOutcome) => void;
    },
): "opener" | "channel" | "nowhere" {
    if (to.opener && !to.opener.closed) {
        to.opener.postMessage({ source: CONNECT_SOURCE, ...outcome }, to.origin);
        return "opener";
    }
    if (to.waiting) {
        to.broadcast(outcome);
        return "channel";
    }
    return "nowhere";
}

/** Open the provider in a new tab the completion page can report back to.
 *
 *  Not `noopener` — that nulls `window.opener`, and the outcome can then only
 *  arrive by the completion page navigating its own tab back into the app,
 *  leaving two copies of it open. The opened page is the provider's, at a URL
 *  the API just minted; that is the trade every OAuth tab makes. The tab is
 *  also recorded as waiting, so a completion page that has *lost* its opener
 *  knows to report on the channel instead of coming home in a tab of its own.
 *
 *  In the desktop app the webview opens no tab at all: the shell routes the
 *  request to the system browser, `window.open` answers `null` either way, and
 *  a browser tab there has no opener to report to. So it goes through
 *  `openExternal` like every other outside URL, and the outcome arrives the
 *  one way left — the refetch when the app regains focus.
 *
 *  False when the browser refused, which it may for a window opened after an
 *  await rather than inside the click — the caller then offers a link. */
export function openAuthorization(url: string): boolean {
    /* Set whether or not the tab opens: a refused one is followed by a link
       the person opens by hand, and that is still a round trip. */
    sentAt = Date.now();
    rememberWaiting(sentAt);
    if (isDesktop()) {
        openExternal(url);
        return true;
    }
    const opened = window.open(url, "_blank");
    return opened !== null;
}

const OUTCOME_KEYS = ["connect", "connector", "account", "code", "reason"];

function readOutcome(source: { get(key: string): string | null }): ConnectOutcome | null {
    const connect = source.get("connect");
    if (!connect) return null;
    return {
        connect,
        connector: source.get("connector"),
        account: source.get("account"),
        code: source.get("code"),
        reason: source.get("reason"),
    };
}

/** Hear how a round trip ended, however it came back.
 *
 *  Four ways in, one handler. The opened tab posts its outcome to this window
 *  and closes, or — when it holds no handle to it — posts on the channel both
 *  tabs hear. A full-page return — a blocked tab, a link followed by hand —
 *  lands with the outcome in the query string, which is read once and stripped
 *  so a reload is not a second connection. And returning to this tab while a
 *  round trip is out is worth a refetch: a completion page on another origin,
 *  which is every local build pointed at a deployed API, can reach none of the
 *  other three. Once an outcome has been heard, or none was ever sent, a focus
 *  is just a focus.
 */
export function useConnectOutcome(onOutcome: (outcome: ConnectOutcome) => void, onReturn: () => void) {
    const latest = useRef({ onOutcome, onReturn });
    useEffect(() => { latest.current = { onOutcome, onReturn }; });

    useEffect(() => {
        /* One handler for all four ways in, so none of them can leave the
           round trip marked as still out — which is what would turn every
           later focus into a refetch. */
        const heard = (outcome: ConnectOutcome) => {
            sentAt = 0;
            forgetWaiting();
            latest.current.onOutcome(outcome);
        };
        const asOutcome = (data: unknown): ConnectOutcome | null => {
            const message = data as Record<string, string | null> | null;
            if (!message || typeof message !== "object" || message.source !== CONNECT_SOURCE) return null;
            return readOutcome({ get: (key) => message[key] ?? null });
        };

        const fromQuery = readOutcome(new URLSearchParams(window.location.search));
        if (fromQuery) {
            const url = new URL(window.location.href);
            for (const key of OUTCOME_KEYS) url.searchParams.delete(key);
            window.history.replaceState(window.history.state, "", url.pathname + url.search + url.hash);
            heard(fromQuery);
        }

        const onMessage = (event: MessageEvent) => {
            /* Origin first: any window holding a handle to this one can post. */
            if (event.origin !== window.location.origin) return;
            const outcome = asOutcome(event.data);
            if (outcome) heard(outcome);
        };
        /* A channel, not just the window: the tab the provider opened has no
           handle to this one whenever the consent chain cut it, and a message
           it cannot deliver is a person left in the wrong tab. */
        const channel = typeof BroadcastChannel === "function" ? new BroadcastChannel(CONNECT_CHANNEL) : null;
        const onChannel = (event: MessageEvent) => {
            const outcome = asOutcome(event.data);
            if (outcome) heard(outcome);
        };
        const onFocus = () => {
            if (sentAt && Date.now() - sentAt < ROUND_TRIP_MS) latest.current.onReturn();
        };
        channel?.addEventListener("message", onChannel);
        window.addEventListener("message", onMessage);
        window.addEventListener("focus", onFocus);
        return () => {
            channel?.removeEventListener("message", onChannel);
            channel?.close();
            window.removeEventListener("message", onMessage);
            window.removeEventListener("focus", onFocus);
        };
    }, []);
}

/** The outcome, said to a person. Only `error` is a failure: the two install
 *  states are a working credential that cannot reach anything yet. */
export function outcomeNote(outcome: ConnectOutcome): { text: string; bad: boolean } {
    switch (outcome.connect) {
        case "connected": return { text: "Connected.", bad: false };
        case "install_required":
            return { text: "Almost there — the app still needs installing before it can reach anything.", bad: false };
        case "pending_approval":
            return { text: "Requested. An owner has to approve the app before it can be installed.", bad: false };
        case "install_received": return { text: "Installation received.", bad: false };
        case "error": return { text: outcome.reason || "The account was not connected.", bad: true };
        default: return { text: "Back from signing in.", bad: false };
    }
}
