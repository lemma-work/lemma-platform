import * as Crypto from "expo-crypto";
import { fetch as expoFetch } from "expo/fetch";
import { base64Url, browserUrl, codeFrom, codeInput, REQUEST_ID } from "./handoff-link";
import type { Server } from "./server";
import { store } from "./store";

/** Signing in in the phone's own browser, for the ways a WebView cannot
 *  offer: Google refuses to sign anyone in inside an app's WebView.
 *
 *  This is the web's desktop handoff, used as it is
 *  (`lemma-frontend/src/desktop/auth-handoff.ts`, and on the backend
 *  `identity/services/desktop_auth_handoff.py`):
 *
 *   1. App: `POST /auth/desktop/requests` with a PKCE challenge, then open
 *      `{web}/auth?desktop_browser=1&desktop_request=<id>` in a Custom Tab.
 *   2. Browser: sign in any way the web offers. The page shows a short code
 *      derived from the request id. The app shows the same code, and the
 *      person confirms the two match. The page then completes the request and
 *      opens `lemma://auth/complete?request_id=<id>`, which is this app's
 *      scheme, so the app comes back to the front.
 *   3. App: a hidden WebView trades the verifier at `POST /auth/desktop/session`
 *      for a cookie session of its own. The usual mint then gives the app its
 *      header session (`web-session.tsx`). The verifier never leaves the
 *      phone, so a request id seen in a URL cannot be exchanged by anyone else.
 *
 *  It is not the CLI's loopback (`/auth/cli/login` posting to 127.0.0.1).
 *  Before a public page may reach loopback, Chrome on Android asks whether
 *  the site "wants to access other apps and services on this device". A
 *  "Block" there leaves sign-in broken until it is undone in site settings,
 *  and the page has no way to bring the app back. The handoff needs no port
 *  and returns to the app by itself. */

export interface Handoff {
    requestId: string;
    /** PKCE verifier. Stays on the phone. */
    verifier: string;
    /** What the browser shows, e.g. `KX7M-Q2PA`. */
    code: string;
    /** Where the browser is sent. */
    url: string;
    /** Epoch ms. */
    expiresAt: number;
    /** The server it was started against. */
    serverId: string;
}

async function sha256(text: string): Promise<Uint8Array> {
    return new Uint8Array(await Crypto.digest(Crypto.CryptoDigestAlgorithm.SHA256, new TextEncoder().encode(text)));
}

/** Open a request against `server`. Nothing is signed in until the browser
 *  completes it and the app's WebView trades the verifier. */
export async function startHandoff(server: Server): Promise<Handoff> {
    const verifier = base64Url(Crypto.getRandomBytes(32));
    const challenge = base64Url(await sha256(verifier));
    let response: Response;
    try {
        response = await expoFetch(`${server.api}/auth/desktop/requests`, {
            method: "POST",
            headers: { "Content-Type": "application/json", Accept: "application/json" },
            body: JSON.stringify({ code_challenge: challenge }),
        }) as unknown as Response;
    } catch {
        throw new Error(`${server.label} did not answer. Check the connection and try again.`);
    }
    if (response.status === 429) throw new Error("Too many sign-ins were started from here. Wait a minute and try again.");
    if (!response.ok) throw new Error(`Signing in in the browser could not start (${response.status}).`);
    const created = (await response.json()) as { request_id?: unknown; expires_in_seconds?: unknown };
    const requestId = typeof created.request_id === "string" && REQUEST_ID.test(created.request_id) ? created.request_id : null;
    if (!requestId) throw new Error("Signing in in the browser could not start.");
    const seconds = Number(created.expires_in_seconds);
    const handoff: Handoff = {
        requestId,
        verifier,
        code: codeFrom(await sha256(codeInput(requestId))),
        url: browserUrl(server.web, requestId),
        expiresAt: Date.now() + (Number.isFinite(seconds) && seconds > 0 ? seconds : 300) * 1000,
        serverId: server.id,
    };
    await store.setHandoff(JSON.stringify(handoff));
    return handoff;
}

/** A handoff still open for `server`, kept in the keystore so it survives
 *  Android stopping the app while the browser is in front. */
export async function pendingHandoff(server: Server, now = Date.now()): Promise<Handoff | null> {
    const held = await store.handoff();
    if (!held) return null;
    try {
        const handoff = JSON.parse(held) as Partial<Handoff>;
        if (
            typeof handoff.requestId === "string" && typeof handoff.verifier === "string"
            && typeof handoff.code === "string" && typeof handoff.url === "string"
            && typeof handoff.expiresAt === "number" && handoff.expiresAt > now
            && handoff.serverId === server.id
        ) return handoff as Handoff;
    } catch {
        /* unreadable is the same as absent */
    }
    await dropHandoff();
    return null;
}

export function dropHandoff(): Promise<void> {
    return store.setHandoff(null);
}

/** Whether `server` offers Google, from SuperTokens' own `GET /loginmethods`,
 *  the same answer the web's buttons come from. */
export async function offersGoogle(server: Server): Promise<boolean> {
    try {
        const response = await expoFetch(`${server.api}/st/auth/loginmethods`, { headers: { Accept: "application/json" } });
        if (!response.ok) return false;
        const body = (await response.json()) as { status?: string; thirdParty?: { enabled?: boolean; providers?: { id?: string }[] } };
        if (body.status !== "OK" || body.thirdParty?.enabled === false) return false;
        return (body.thirdParty?.providers ?? []).some((provider) => provider?.id === "google");
    } catch {
        return false;
    }
}
