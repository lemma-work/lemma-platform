/** A provider sign-in's outcome, read and said. Pure, so `tests/` can hold
 *  it and `+native-intent.tsx` can import it without the browser module.
 *  The round trip itself is `round-trip.ts`. */

export const CONNECT_RETURN = "lemma://connect/done";

/** The `return_to` a phone's provider sign-in asks for: the web's completion
 *  page, told it is a phone's round trip. Today that page takes the browser
 *  to the web's home and the person closes it; once it sends a phone's round
 *  trip to `CONNECT_RETURN` (as the sign-in page does for `client=mobile`),
 *  the session closes by itself, with the outcome. A rooted path: the backend
 *  refuses anything with a host in it. */
export const PHONE_RETURN = "/oauth/complete?client=mobile";

/** Whether a link that opened the app is a connect round trip coming back:
 *  the session reads it, the router is given nothing to do. */
export function isConnectReturn(path: string): boolean {
    return /^(lemma:\/\/|\/)?\/?connect\/done\b/.test(path);
}

/** What the round trip reports: connected, install_required,
 *  pending_approval, install_received or error. */
export interface ConnectOutcome {
    connect: string;
    connector: string | null;
    account: string | null;
    reason: string | null;
    code?: string | null;
}

/** The outcome in a return address's query, when the session ended on one. */
export function outcomeOf(url: string): ConnectOutcome | null {
    const query = url.split("?")[1];
    if (!query) return null;
    const params = new URLSearchParams(query.split("#")[0]);
    const connect = params.get("connect");
    if (!connect) return null;
    return { connect, connector: params.get("connector"), account: params.get("account"), reason: params.get("reason"), code: params.get("code") };
}

/** The outcome, said to a person (the web's `outcomeNote`). Only `error` is
 *  a failure: the install states are a working credential that cannot reach
 *  anything yet. */
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
