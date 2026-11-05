import { onApi } from "./config";

/** Letting an outside MCP client — Claude, ChatGPT, an editor — use one space
 *  (a pod, to the API).
 *
 *  The client sends the browser to the API's `/oauth/authorize`, which holds
 *  the request and redirects here with `?request=<id>`. This page signs the
 *  person in as usual, shows what is being asked, and posts the answer; the
 *  API replies with the client's redirect URI — a code, or `access_denied` —
 *  and the browser goes there. `lemma_backend/app/modules/mcp_access` has the
 *  other half.
 *
 *  The id is all the page carries, and it is single-use on the server: nothing
 *  about the request is taken from the URL but which request it is.
 */

export interface ConsentRequest {
    client_id: string;
    /** What the app calls itself. Anyone can register an app called "Claude". */
    client_name: string;
    /** The host serving the app's metadata document — the one thing about it
     *  that is checked. Null for an app that registered itself. */
    verified_host: string | null;
    client_uri: string | null;
    /** A web redirect's host, or an app's scheme and a colon (`cursor:`):
     *  the rest of an app's URI is whatever the app wrote, so
     *  `evilapp://claude.ai/cb` must not read as going to claude.ai. */
    redirect_host: string;
    /** The redirect opens an app on this device rather than a web page. */
    redirect_to_app?: boolean;
    pod_id: string;
    pod_name: string;
    scopes: string[];
}

export const WRITE_SCOPE = "pod:write";
/** Being told about new rows: sent to the app's own server as they are added,
 *  while the person is away too. Asked for separately and off unless ticked. */
export const EVENTS_SCOPE = "pod:events";

/** Who is asking, said the way a person can check it. The host serving an
 *  app's metadata document is verified; the name an app gives itself is not,
 *  so the name is never the subject of the question. */
export function whoIsAsking(request: Pick<ConsentRequest, "verified_host" | "client_name">): {
    title: string;
    claim: string;
    verified: boolean;
} {
    const claim = "It calls itself “" + request.client_name + "”.";
    if (request.verified_host) {
        return { title: request.verified_host, claim, verified: true };
    }
    return {
        title: "An unverified app",
        claim: claim + " Lemma cannot check that: this app registered itself.",
        verified: false,
    };
}

/** Where the person is sent after answering, in words they can check. */
export function whereBack(request: Pick<ConsentRequest, "redirect_host" | "redirect_to_app">): string {
    return request.redirect_to_app
        ? "an app on this device that opens " + request.redirect_host + " links"
        : request.redirect_host;
}

/** Held across sign-in, which leaves this page and may leave the site. */
const KEY = "lemma.mcp-consent.request";

const REQUEST_ID = /^[A-Za-z0-9_-]{16,64}$/;

export function requestIdFromSearch(search: string): string | null {
    const raw = new URLSearchParams(search).get("request")?.trim();
    return raw && REQUEST_ID.test(raw) ? raw : null;
}

function session(): Storage | null {
    try {
        return typeof window === "undefined" ? null : window.sessionStorage;
    } catch {
        return null;
    }
}

export function holdConsentRequest(id: string): void {
    session()?.setItem(KEY, id);
}

export function heldConsentRequest(): string | null {
    const raw = session()?.getItem(KEY) ?? null;
    return raw && REQUEST_ID.test(raw) ? raw : null;
}

export function dropConsentRequest(): void {
    session()?.removeItem(KEY);
}

async function problem(response: Response, fallback: string): Promise<Error> {
    const body = (await response.json().catch(() => null)) as { message?: unknown } | null;
    return new Error(typeof body?.message === "string" ? body.message : fallback);
}

export async function readConsentRequest(id: string, fetcher: typeof fetch = fetch): Promise<ConsentRequest> {
    const response = await fetcher(onApi("/oauth/consent/" + encodeURIComponent(id)), {
        credentials: "include",
        cache: "no-store",
        headers: { Accept: "application/json" },
    });
    if (!response.ok) throw await problem(response, "This request could not be read (" + response.status + ").");
    return (await response.json()) as ConsentRequest;
}

/** Schemes that run something in the page that navigates to them. The
 *  browser is on the auth site with the person signed in, so a redirect to one
 *  of these would run it there. Mirrors `app/modules/mcp_access/domain/redirects.py`. */
const REFUSED_SCHEMES = new Set([
    "javascript:", "vbscript:", "data:", "blob:", "file:", "filesystem:",
    "about:", "view-source:", "jar:", "ws:", "wss:", "ftp:",
]);

/** The same loopback rule as the API's `ipaddress.is_loopback`: all of
 *  127.0.0.0/8, `::1`, and IPv4-mapped 127.x (which `URL` spells
 *  `[::ffff:7f00:1]`). Narrower here than there, a redirect the API accepted
 *  — after creating the grant — would be refused on this page, and the person
 *  would see an error beside a connection that exists. */
function isLoopback(hostname: string): boolean {
    if (hostname === "localhost" || hostname === "[::1]") return true;
    if (/^127\.\d{1,3}\.\d{1,3}\.\d{1,3}$/.test(hostname)) return true;
    return /^\[::ffff:7f[0-9a-f]{2}:[0-9a-f]{1,4}\]$/.test(hostname);
}

/** Whether the browser may be sent to `raw`. The API checks this too; this is
 *  the backstop, because this page is where a bad URI would do its harm. */
export function safeRedirect(raw: string): boolean {
    if (/[\u0000-\u0020]/.test(raw)) return false;
    let url: URL;
    try {
        url = new URL(raw);
    } catch {
        return false;
    }
    if (REFUSED_SCHEMES.has(url.protocol)) return false;
    if (url.username || url.password) return false;
    if (url.protocol === "https:") return url.hostname !== "";
    if (url.protocol === "http:") return isLoopback(url.hostname);
    return /^[a-z][a-z0-9+.-]*:$/.test(url.protocol);
}

/** The URL to send the browser to. Only ever the one the API returns, and
 *  only when it passes `safeRedirect`. `readOnly` narrows what the app asked
 *  for; it never widens it. `events` grants `pod:events` only when the app
 *  asked for it. */
export async function answerConsentRequest(
    id: string,
    answer: { allow: boolean; readOnly?: boolean; events?: boolean },
    fetcher: typeof fetch = fetch,
): Promise<string> {
    const response = await fetcher(onApi("/oauth/consent/" + encodeURIComponent(id)), {
        method: "POST",
        credentials: "include",
        headers: { Accept: "application/json", "Content-Type": "application/json" },
        body: JSON.stringify({
            allow: answer.allow,
            read_only: answer.readOnly ?? false,
            events: answer.events ?? false,
        }),
    });
    if (!response.ok) throw await problem(response, "Your answer could not be sent (" + response.status + ").");
    const body = (await response.json()) as { redirect_to?: unknown };
    if (typeof body.redirect_to !== "string") throw new Error("The server did not say where to go next.");
    if (!safeRedirect(body.redirect_to)) throw new Error("This app asked to send you somewhere unsafe, so you were not sent.");
    return body.redirect_to;
}
