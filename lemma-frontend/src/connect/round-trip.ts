import { useEffect, useRef } from "react";

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

/** Open the provider in a new tab the completion page can report back to.
 *
 *  Not `noopener` — that nulls `window.opener`, and the outcome can then only
 *  arrive by the completion page navigating its own tab back into the app,
 *  leaving two copies of it open. The opened page is the provider's, at a URL
 *  the API just minted; that is the trade every OAuth tab makes.
 *
 *  False when the browser refused, which it may for a window opened after an
 *  await rather than inside the click — the caller then offers a link. */
export function openAuthorization(url: string): boolean {
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
 *  reload is not a second connection. And returning to this tab at all is
 *  worth a refetch: a completion page on another origin, which is every local
 *  build pointed at a deployed API, can reach neither of the other two.
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
            latest.current.onOutcome(fromQuery);
        }

        const onMessage = (event: MessageEvent) => {
            /* Origin first: any window holding a handle to this one can post. */
            if (event.origin !== window.location.origin) return;
            const data = event.data as Record<string, string | null> | null;
            if (!data || typeof data !== "object" || data.source !== "lemma-connect") return;
            const outcome = readOutcome({ get: (key) => data[key] ?? null });
            if (outcome) latest.current.onOutcome(outcome);
        };
        const onFocus = () => latest.current.onReturn();
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
