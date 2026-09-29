import { onApi } from "./config";

/** Signing in the Lemma CLI from this browser.
 *
 *  `lemma auth login` (`lemma_sdk/auth.py: run_login_flow`) listens on a
 *  loopback port, then opens `/auth/cli/login?callback=<loopback>&state=<nonce>`
 *  here. The browser signs in as usual, mints a dedicated CLI session with
 *  `POST /auth/cli/session-tokens`, and posts `{ state, session }` to the
 *  callback. The CLI checks the state against the nonce it made and keeps the
 *  tokens. The URL is a contract with every CLI already installed.
 *
 *  The callback must be loopback. Anything else would post this person's
 *  tokens to whichever host a link named. Loopback is not the whole answer,
 *  since on a shared machine another user's process can hold the port, so
 *  nothing is minted until the person confirms which account they are handing
 *  over.
 */

export interface CliRequest {
    callback: string;
    state: string;
}

/** Held across sign-in, which leaves this page and may leave the site for a
 *  provider. `sessionStorage`, so it belongs to this tab and this sign-in. */
const KEY = "lemma.cli-auth.request";

const LOOPBACK = new Set(["127.0.0.1", "localhost", "[::1]"]);

/** The callback, if it points at this machine; null for anything else. */
export function loopbackCallback(raw: string | null | undefined): string | null {
    if (!raw) return null;
    try {
        const url = new URL(raw);
        if (url.protocol !== "http:" || !LOOPBACK.has(url.hostname.toLowerCase())) return null;
        if (url.username || url.password) return null;
        return url.toString();
    } catch {
        return null;
    }
}

function parse(callback: string | null | undefined, state: string | null | undefined): CliRequest | null {
    const safe = loopbackCallback(callback);
    const nonce = state?.trim();
    return safe && nonce ? { callback: safe, state: nonce } : null;
}

export function cliRequestFromSearch(search: string): CliRequest | null {
    const params = new URLSearchParams(search);
    return parse(params.get("callback"), params.get("state"));
}

/** Whether the URL tried to carry a request at all, valid or not. */
export function asksForCli(search: string): boolean {
    const params = new URLSearchParams(search);
    return params.has("callback") || params.has("state");
}

function session(): Storage | null {
    try {
        return typeof window === "undefined" ? null : window.sessionStorage;
    } catch {
        return null;
    }
}

export function holdCliRequest(request: CliRequest): void {
    session()?.setItem(KEY, JSON.stringify(request));
}

/** The CLI request this tab is signing in for, if any. Re-validated on the
 *  way out: storage is not a place a callback gets to skip the loopback check. */
export function heldCliRequest(): CliRequest | null {
    const raw = session()?.getItem(KEY);
    if (!raw) return null;
    try {
        const held = JSON.parse(raw) as Partial<CliRequest>;
        return parse(held.callback, held.state);
    } catch {
        return null;
    }
}

export function dropCliRequest(): void {
    session()?.removeItem(KEY);
}

/** Mint a CLI session from this browser's session. */
export async function mintCliSession(fetcher: typeof fetch = fetch): Promise<Record<string, unknown>> {
    const response = await fetcher(onApi("/auth/cli/session-tokens"), {
        method: "POST",
        credentials: "include",
        headers: { Accept: "application/json" },
    });
    if (!response.ok) throw new Error(`A CLI session could not be created (${response.status}).`);
    return (await response.json()) as Record<string, unknown>;
}

/** Hand the session to the waiting CLI.
 *
 *  No `base_url`: the CLI already knows which API it asked, and may have been
 *  told a different address for itself than the browser uses. */
export async function deliverToCli(
    request: CliRequest,
    minted: Record<string, unknown>,
    fetcher: typeof fetch = fetch,
): Promise<void> {
    let response: Response;
    try {
        response = await fetcher(request.callback, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ state: request.state, session: minted }),
        });
    } catch {
        throw new Error("Your terminal did not answer. If lemma auth login has stopped waiting, run it again.");
    }
    if (!response.ok) throw new Error(`Your terminal refused the sign-in (${response.status}). Run lemma auth login again.`);
}
