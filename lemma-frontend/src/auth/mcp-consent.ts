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
    client_name: string;
    client_uri: string | null;
    logo_uri: string | null;
    redirect_host: string;
    pod_id: string;
    pod_name: string;
    scopes: string[];
}

export const WRITE_SCOPE = "pod:write";

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

/** The URL to send the browser to. Only ever the one the API returns: the
 *  API has already checked it against what the client registered. */
export async function answerConsentRequest(
    id: string,
    allow: boolean,
    fetcher: typeof fetch = fetch,
): Promise<string> {
    const response = await fetcher(onApi("/oauth/consent/" + encodeURIComponent(id)), {
        method: "POST",
        credentials: "include",
        headers: { Accept: "application/json", "Content-Type": "application/json" },
        body: JSON.stringify({ allow }),
    });
    if (!response.ok) throw await problem(response, "Your answer could not be sent (" + response.status + ").");
    const body = (await response.json()) as { redirect_to?: unknown };
    if (typeof body.redirect_to !== "string") throw new Error("The server did not say where to go next.");
    return body.redirect_to;
}
