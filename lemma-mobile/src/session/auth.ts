import { fetch as expoFetch } from "expo/fetch";
import type { Server } from "./server";
import { store } from "./store";

/** The phone's own session: header tokens, never cookies.
 *
 *  It is minted for this app alone (`POST /auth/cli/session-tokens`, from the
 *  sign-in WebView) and refreshed through `POST /auth/cli/refresh`, so its
 *  refresh token is held nowhere else. That is the rule this file exists to
 *  keep: a refresh token two holders share trips SuperTokens' theft detection
 *  and signs both out. So these tokens never go into a cookie, and the
 *  WebViews keep their own cookie session from signing in.
 *
 *  Every request the SDK makes goes through `fetch` here: it carries the
 *  current access token, refreshes it shortly before it runs out, and on a
 *  401 refreshes once and asks again. `expo/fetch` underneath, because the
 *  agent stream needs a response body it can read as it arrives, which React
 *  Native's own `fetch` does not give. */

export interface Tokens {
    access: string;
    refresh: string;
    /** Epoch ms. */
    expiresAt: number;
    userId: string | null;
    email: string | null;
}

export type SessionEvent = "signed-in" | "signed-out";

let server: Server | null = null;
let tokens: Tokens | null = null;
let refreshing: Promise<Tokens | null> | null = null;
const listeners = new Set<(event: SessionEvent) => void>();

/** Refresh this long before the access token runs out. */
const EARLY_MS = 60_000;

export function onSession(listener: (event: SessionEvent) => void): () => void {
    listeners.add(listener);
    return () => listeners.delete(listener);
}

function emit(event: SessionEvent): void {
    for (const listener of listeners) listener(event);
}

/** What `/auth/cli/session-tokens` and `/auth/cli/refresh` answer. */
export function tokensFrom(raw: unknown): Tokens | null {
    if (!raw || typeof raw !== "object") return null;
    const body = raw as Record<string, unknown>;
    const access = typeof body.access_token === "string" ? body.access_token : null;
    const refresh = typeof body.refresh_token === "string" ? body.refresh_token : null;
    if (!access || !refresh) return null;
    const expires = Number(body.access_token_expires_at);
    return {
        access,
        refresh,
        expiresAt: Number.isFinite(expires) && expires > 0 ? expires : Date.now() + 15 * 60_000,
        userId: typeof body.user_id === "string" ? body.user_id : null,
        email: typeof body.email === "string" ? body.email : null,
    };
}

export async function loadSession(next: Server): Promise<Tokens | null> {
    server = next;
    const held = await store.session(next.id);
    try {
        tokens = held ? tokensFrom(JSON.parse(held)) ?? (JSON.parse(held) as Tokens) : null;
    } catch {
        tokens = null;
    }
    if (tokens && (!tokens.access || !tokens.refresh)) tokens = null;
    return tokens;
}

async function keep(next: Tokens | null): Promise<void> {
    tokens = next;
    if (server) await store.setSession(server.id, next ? JSON.stringify(next) : null);
}

export async function signedIn(next: Server, minted: Tokens): Promise<void> {
    server = next;
    await keep(minted);
    emit("signed-in");
}

export function currentTokens(): Tokens | null {
    return tokens;
}

/** The server the session is with, or null before one is loaded. */
export function currentServer(): Server | null {
    return server;
}

/** A refreshed access token after the server refused the one it had, or
 *  null when the session has ended. */
export async function renewAccess(): Promise<string | null> {
    return (await refreshOnce())?.access ?? null;
}

/** An access token good for the next minute, refreshed first if not, for
 *  what cannot go through `authedFetch`: an upload handed to the platform,
 *  the live socket. Null when signed out. */
export async function freshAccess(): Promise<string | null> {
    const held = tokens;
    if (!held) return null;
    if (held.expiresAt - Date.now() >= EARLY_MS) return held.access;
    return (await refreshOnce())?.access ?? null;
}

/** One refresh at a time: every request that found the token stale waits on
 *  the same one, so a refresh token is never spent twice. */
function refreshOnce(): Promise<Tokens | null> {
    refreshing ??= (async () => {
        const held = tokens;
        const at = server;
        if (!held || !at) return null;
        let response: Response;
        try {
            response = await expoFetch(`${at.api}/auth/cli/refresh`, {
                method: "POST",
                headers: { "Content-Type": "application/json", Accept: "application/json" },
                body: JSON.stringify({ refresh_token: held.refresh }),
            }) as unknown as Response;
        } catch {
            /* Offline is not signed out: keep the session and try later. */
            return held;
        }
        if (response.status === 401) {
            await keep(null);
            emit("signed-out");
            return null;
        }
        if (!response.ok) return held;
        const next = tokensFrom(await response.json().catch(() => null));
        if (!next) return held;
        await keep(next);
        return next;
    })().finally(() => {
        refreshing = null;
    });
    return refreshing;
}

function urlOf(input: unknown): string {
    if (typeof input === "string") return input;
    if (input && typeof input === "object" && "url" in input) return String((input as { url: string }).url);
    return String(input);
}

function headersOf(init: RequestInit | undefined): Record<string, string> {
    const raw = init?.headers;
    const out: Record<string, string> = {};
    if (!raw) return out;
    if (typeof Headers !== "undefined" && raw instanceof Headers) {
        raw.forEach((value, key) => { out[key] = value; });
        return out;
    }
    if (Array.isArray(raw)) {
        for (const [key, value] of raw) out[key] = value;
        return out;
    }
    return { ...(raw as Record<string, string>) };
}

function withBearer(init: RequestInit | undefined, access: string): RequestInit {
    const headers = headersOf(init);
    for (const key of Object.keys(headers)) if (key.toLowerCase() === "authorization") delete headers[key];
    headers.Authorization = `Bearer ${access}`;
    return { ...init, headers, credentials: "omit" };
}

/** The `fetch` every request in the app goes through. */
export async function authedFetch(input: unknown, init?: RequestInit): Promise<Response> {
    const url = urlOf(input);
    const at = server;
    const ours = at !== null && url.startsWith(at.api) && !url.includes("/auth/cli/refresh");
    const send = (options: RequestInit | undefined) => expoFetch(url, options as never) as unknown as Promise<Response>;
    if (!ours || !tokens) return send(init);
    let held: Tokens | null = tokens;
    if (held.expiresAt - Date.now() < EARLY_MS) held = await refreshOnce();
    if (!held) return send(init);
    const first = await send(withBearer(init, held.access));
    if (first.status !== 401) return first;
    const renewed = await refreshOnce();
    if (!renewed || renewed.access === held.access) return first;
    return send(withBearer(init, renewed.access));
}

/** Sign this app out: revoke its own session on the server, then forget it.
 *  The sign-in WebView's cookie session is signed out separately
 *  (`signOutWebView`), since it is a session of its own. */
export async function signOut(): Promise<void> {
    const held = tokens;
    const at = server;
    if (held && at) {
        try {
            await expoFetch(`${at.api}/st/auth/signout`, {
                method: "POST",
                headers: { Authorization: `Bearer ${held.access}`, "st-auth-mode": "header", rid: "session", "fdi-version": "4.2" },
            });
        } catch {
            /* Forgotten here either way; the server's copy runs out. */
        }
    }
    await keep(null);
    emit("signed-out");
}

/** Installed once, before the first request: the SDK calls the global. */
export function installFetch(): void {
    (globalThis as { fetch: unknown }).fetch = authedFetch;
}
