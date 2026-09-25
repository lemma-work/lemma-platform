import { onApi } from "@/auth/config";
import { digestSha256 } from "@/auth/sha256";
import { desktopInfo } from "./bridge";

/** Signing in to a hosted workspace from the desktop app, in the system browser.
 *
 *  The app's webview is not where a password manager, a passkey or an
 *  existing Google session lives, and an OAuth provider may refuse to be
 *  embedded at all. So in hosted mode the app does not show the sign-in form.
 *  It opens a sign-in request, sends the person to this same portal in their
 *  own browser, and waits:
 *
 *   1. App: `POST /auth/desktop/requests` with a PKCE challenge, then navigate
 *      to `/auth?desktop_browser=1&desktop_request=<id>`. The shell recognises
 *      that marker (`is_desktop_browser_auth_url` in `desktop/src/navigation.rs`),
 *      cancels the navigation and opens the URL in the system browser.
 *   2. Browser: sign in as usual. The request id is held in `sessionStorage`
 *      across the provider round trip. On landing the portal shows the
 *      request's short code — the app is showing the same one — and asks the
 *      person to confirm. Only on that click does it call
 *      `POST /auth/desktop/requests/<id>/complete` with the browser's session,
 *      then open `lemma://auth/complete?request_id=<id>`, which brings the app
 *      back to the front.
 *   3. App: meanwhile polling `POST /auth/desktop/session` with the verifier.
 *      409 is "not yet"; success sets the app's own session cookies.
 *
 *  The verifier never leaves the app's webview, so a request id alone — which
 *  is in a URL, in a browser — cannot be exchanged for a session. But whoever
 *  holds the verifier is whoever *started* the request, and that need not be
 *  the person whose browser finishes it: a link to `/auth?desktop_request=<id>`
 *  sent to somebody already signed in used to complete on its own and hand the
 *  sender that person's session. Hence the code, and the click: the browser
 *  completes only a request its own person confirms, having seen that the
 *  Lemma app in front of them is showing the same code. The backend half is
 *  `app/modules/identity/services/desktop_auth_handoff.py`.
 */

const REQUEST_KEY = "lemma.desktop-auth.request-id";
const PENDING_KEY = "lemma.desktop-auth.pending";
const REQUEST_ID = /^[A-Za-z0-9_-]{20,128}$/;

/** Only when the shell says it is a hosted workspace. A local one signs in
 *  inside the app: its API is on this machine, and the browser has no route to
 *  hand a session back through. */
export function shouldUseBrowserHandoff(): boolean {
    return desktopInfo()?.mode === "hosted";
}

/* ── the browser half ──────────────────────────────────────────────── */

export function requestIdFromSearch(search: string): string | null {
    const value = new URLSearchParams(search).get("desktop_request")?.trim();
    return value && REQUEST_ID.test(value) ? value : null;
}

function session(): Storage | null {
    try {
        return typeof window === "undefined" ? null : window.sessionStorage;
    } catch {
        return null;
    }
}

export function holdRequestId(requestId: string | null): void {
    if (requestId) session()?.setItem(REQUEST_KEY, requestId);
}

/** The request this browser tab is signing in for, if any. */
export function heldRequestId(): string | null {
    const value = session()?.getItem(REQUEST_KEY) ?? null;
    return value && REQUEST_ID.test(value) ? value : null;
}

export function dropRequestId(): void {
    session()?.removeItem(REQUEST_KEY);
}

/** Letters and digits nobody misreads for one another: no 0/O, 1/I/L, 5/S, 8/B. */
const CODE_ALPHABET = "ACDEFGHJKMNPQRTUVWXY2345679";

/** The short code both halves show for one request, e.g. `KX7M-Q2PA`.
 *
 *  Derived from the request id, so the app and the browser compute it
 *  separately and agree without either telling the other. It is not a secret —
 *  whoever started the request sees it — it is a comparison: the person
 *  confirming checks it against the app they are actually looking at. */
export async function handoffCode(requestId: string): Promise<string> {
    const digest = await digestSha256(new TextEncoder().encode("lemma-desktop-handoff:" + requestId));
    const letters = Array.from(digest.slice(0, 8), (byte) => CODE_ALPHABET[byte % CODE_ALPHABET.length]).join("");
    return letters.slice(0, 4) + "-" + letters.slice(4);
}

/** Where the app is woken with the result. */
export function appReturnUrl(requestId: string): string {
    return `lemma://auth/complete?request_id=${encodeURIComponent(requestId)}`;
}

/** Tell the backend this browser's session is the one the app asked for. */
export async function completeRequest(requestId: string, fetcher: typeof fetch = fetch): Promise<void> {
    const response = await fetcher(onApi(`/auth/desktop/requests/${encodeURIComponent(requestId)}/complete`), {
        method: "POST",
        credentials: "include",
    });
    if (!response.ok) throw new Error(`Lemma Desktop could not be signed in (${response.status}).`);
}

/* ── the app half ──────────────────────────────────────────────────── */

export interface PendingHandoff {
    requestId: string;
    verifier: string;
    browserUrl: string;
    expiresAt: number;
}

/** A request this webview already started and has not finished, so a reload
 *  resumes it instead of opening a second browser tab. */
export function pendingHandoff(now = Date.now()): PendingHandoff | null {
    const raw = session()?.getItem(PENDING_KEY);
    if (!raw) return null;
    try {
        const pending = JSON.parse(raw) as Partial<PendingHandoff>;
        if (
            typeof pending.requestId === "string"
            && typeof pending.verifier === "string"
            && typeof pending.browserUrl === "string"
            && typeof pending.expiresAt === "number"
            && pending.expiresAt > now
        ) return pending as PendingHandoff;
    } catch {
        /* unreadable is the same as absent */
    }
    dropPending();
    return null;
}

export function dropPending(): void {
    session()?.removeItem(PENDING_KEY);
}

function base64Url(bytes: Uint8Array): string {
    let binary = "";
    for (const byte of bytes) binary += String.fromCharCode(byte);
    return btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

export function createVerifier(): string {
    const bytes = new Uint8Array(32);
    crypto.getRandomValues(bytes);
    return base64Url(bytes);
}

/** S256 of the verifier. */
export async function challengeFor(verifier: string): Promise<string> {
    return base64Url(await digestSha256(new TextEncoder().encode(verifier)));
}

/** Where the system browser is sent: this portal, marked so the shell hands it
 *  out rather than navigating the app. */
export function browserSignInUrl(origin: string, portalPath: string, requestId: string, signUp: boolean): string {
    const url = new URL(portalPath + (signUp ? "/signup" : ""), origin);
    url.searchParams.set("desktop_browser", "1");
    url.searchParams.set("desktop_request", requestId);
    return url.toString();
}

/** Open a request, or resume the one already open. */
export async function startHandoff(
    origin: string,
    portalPath: string,
    signUp: boolean,
    fetcher: typeof fetch = fetch,
): Promise<PendingHandoff> {
    const existing = pendingHandoff();
    if (existing) return existing;
    const verifier = createVerifier();
    const response = await fetcher(onApi("/auth/desktop/requests"), {
        method: "POST",
        credentials: "include",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ code_challenge: await challengeFor(verifier) }),
    });
    if (!response.ok) throw new Error(`Sign-in could not be started (${response.status}).`);
    const created = (await response.json()) as { request_id: string; expires_in_seconds: number };
    const pending: PendingHandoff = {
        requestId: created.request_id,
        verifier,
        browserUrl: browserSignInUrl(origin, portalPath, created.request_id, signUp),
        expiresAt: Date.now() + created.expires_in_seconds * 1000,
    };
    session()?.setItem(PENDING_KEY, JSON.stringify(pending));
    return pending;
}

const EXCHANGE_INTERVAL_MS = 1_200;

/** Wait for the browser to finish, then take the session into this webview.
 *  Stops when `cancelled()` says so, and rejects once the request expires. */
export async function awaitSession(
    pending: PendingHandoff,
    cancelled: () => boolean,
    fetcher: typeof fetch = fetch,
    wait: (ms: number) => Promise<void> = (ms) => new Promise((resolve) => setTimeout(resolve, ms)),
): Promise<"signed-in" | "cancelled"> {
    while (Date.now() < pending.expiresAt) {
        if (cancelled()) return "cancelled";
        const response = await fetcher(onApi("/auth/desktop/session"), {
            method: "POST",
            credentials: "include",
            headers: { "content-type": "application/json", "st-auth-mode": "cookie" },
            body: JSON.stringify({ request_id: pending.requestId, code_verifier: pending.verifier }),
        });
        if (response.status === 409) {
            await wait(EXCHANGE_INTERVAL_MS);
            continue;
        }
        if (!response.ok) {
            throw new Error(response.status === 404
                ? "This sign-in request expired. Start again."
                : `Sign-in could not be finished (${response.status}).`);
        }
        dropPending();
        return "signed-in";
    }
    throw new Error("This sign-in request expired. Start again.");
}
