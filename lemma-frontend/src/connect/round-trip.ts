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
 *  `return_to` points at `/oauth/complete`, which already exists to hand the
 *  outcome to the tab that opened it and close itself. `from` is where that page
 *  sends the browser instead when there is no opener to hand it to.
 */

export const COMPLETE_PATH = "/oauth/complete";

/** What the round trip reports: connected, install_required, pending_approval,
 *  install_received or error. */
export interface ConnectOutcome {
    connect: string;
    connector: string | null;
    account: string | null;
    reason: string | null;
}

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

/** Open the provider in a new tab the completion page can report back to.
 *
 *  Not `noopener` — that nulls `window.opener`, and the outcome can then only
 *  arrive by the completion page navigating its own tab back into the app,
 *  leaving two copies of it open. The opened page is the provider's, at a URL
 *  the API just minted; that is the trade every OAuth tab makes.
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
    return { connect, connector: source.get("connector"), account: source.get("account"), reason: source.get("reason") };
}

/** Hear how a round trip ended, however it came back.
 *
 *  Three ways in, one handler. The opened tab posts its outcome here and
 *  closes. A full-page return — a blocked tab, a link followed by hand — lands
 *  with the outcome in the query string, which is read once and stripped so a
 *  reload is not a second connection. And returning to this tab while a round
 *  trip is out is worth a refetch: a completion page on another origin, which
 *  is every local build pointed at a deployed API, can reach neither of the
 *  other two. Once an outcome has been heard, or none was ever sent, a focus
 *  is just a focus.
 */
export function useConnectOutcome(onOutcome: (outcome: ConnectOutcome) => void, onReturn: () => void) {
    const latest = useRef({ onOutcome, onReturn });
    useEffect(() => { latest.current = { onOutcome, onReturn }; });

    useEffect(() => {
        const fromQuery = readOutcome(new URLSearchParams(window.location.search));
        if (fromQuery) {
            const url = new URL(window.location.href);
            for (const key of OUTCOME_KEYS) url.searchParams.delete(key);
            window.history.replaceState(window.history.state, "", url.pathname + url.search + url.hash);
            sentAt = 0;
            latest.current.onOutcome(fromQuery);
        }

        const onMessage = (event: MessageEvent) => {
            /* Origin first: any window holding a handle to this one can post. */
            if (event.origin !== window.location.origin) return;
            const data = event.data as Record<string, string | null> | null;
            if (!data || typeof data !== "object" || data.source !== "lemma-connect") return;
            const outcome = readOutcome({ get: (key) => data[key] ?? null });
            if (!outcome) return;
            sentAt = 0;
            latest.current.onOutcome(outcome);
        };
        const onFocus = () => {
            if (sentAt && Date.now() - sentAt < ROUND_TRIP_MS) latest.current.onReturn();
        };
        window.addEventListener("message", onMessage);
        window.addEventListener("focus", onFocus);
        return () => {
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
