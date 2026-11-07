import { LemmaClient } from "lemma-sdk";
import { currentTokens, freshAccess, renewAccess } from "./auth";
import type { Server } from "./server";

/** The SDK client, one per server, and one per Lemma through `withPod`, so a
 *  401 anywhere is the one session ending everywhere (as on the web).
 *
 *  Constructed with the access token, which puts the SDK in token mode:
 *  Bearer headers, no cookies, no SuperTokens browser SDK. The token it was
 *  built with goes stale; `authedFetch` swaps in the current one on every
 *  request, so the client never needs rebuilding after a refresh. */

let base: { server: Server; client: LemmaClient } | null = null;
const scoped = new Map<string, LemmaClient>();

export function lemma(server: Server, podId?: string): LemmaClient {
    if (!base || base.server.id !== server.id) {
        scoped.clear();
        base = {
            server,
            client: new LemmaClient({
                apiUrl: server.api,
                authUrl: `${server.web}/auth`,
                token: currentTokens()?.access ?? "signed-out",
                /* What asks the SDK for a token rather than sending one, the
                   live changes socket, gets the session's current one. */
                tokenSource: {
                    current: async () => (await freshAccess()) ?? Promise.reject(new Error("Signed out.")),
                    renew: async () => (await renewAccess()) ?? Promise.reject(new Error("Signed out.")),
                },
                /* No mobile client is known to the backend yet, and a phone
                   is a person at the app, not a script. */
                client: "lemma-app",
            }),
        };
    }
    if (!podId) return base.client;
    const held = scoped.get(podId);
    if (held) return held;
    const made = base.client.withPod(podId);
    scoped.set(podId, made);
    return made;
}

export function forgetClients(): void {
    base = null;
    scoped.clear();
}
