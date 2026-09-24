/** Whether this browser has a session, and the one request that can say so.
 *
 *  Its own module for the reason `auth-state.ts` is: the test runner strips
 *  types from `.ts` and cannot load `.tsx` at all, so a decision living beside
 *  JSX is a decision nothing can test. It is also the only way the gate on the
 *  front door and the gate on the workspace can be the same decision rather
 *  than two that drift.
 */

import { lemma, apiUrl, hasApiUrl } from "./client";
import { source } from "@/data";
import { entersTheApp } from "./auth-state";

export type Person = { id: string; email: string; name?: string };

/** Ask the API directly whether this browser has a session.
 *
 *  The SDK will not, and its reason is good until it is not. `performAuthCheck`
 *  short-circuits on `doesSessionExist()`, which reads a front token out of
 *  storage on *this* origin — no network — and returns unauthenticated when it
 *  finds none. That exists to stop an app whose auth domain cannot share
 *  cookies from hammering the refresh endpoint forever, and it is the right
 *  default.
 *
 *  It is also exactly wrong for a development origin. The session cookie this
 *  deployment sets is host-only on the API and `SameSite=None; Secure`, so a
 *  browser *will* send it cross-site, and the API's CORS allowlist names the
 *  dev origin with credentials. Everything needed is in place — and the check
 *  never made the request, because signing in at the auth site stored the front
 *  token under *that* origin, leaving the dev origin blank. Sign in, come back,
 *  get the door again, forever.
 *
 *  So: one request, on the unauthenticated path only, before believing it. It
 *  cannot storm — it is a single fetch whose answer is kept, not a refresh
 *  loop — and where the front token is present the SDK answers first and this
 *  never runs.
 */
export async function askTheApi(): Promise<Person | null> {
    try {
        const response = await fetch(apiUrl() + "/users/me", {
            credentials: "include",
            headers: { accept: "application/json" },
        });
        if (!response.ok) return null;
        return (await response.json()) as Person;
    } catch {
        /* Offline, CORS, DNS — none of which is a session, and all of which the
           sign-in screen is the right answer to. */
        return null;
    }
}

/** Settle it, once, for somewhere that only needs the yes or no.
 *
 *  The front door asks this. It has to end at `askTheApi` rather than at the
 *  SDK's answer for the reason above: a signed-in person arriving from the auth
 *  site has no front token on this origin, so the SDK says `unauthenticated`
 *  without going near the network — and a door that believed it would show the
 *  marketing page to somebody who is already signed in, every time.
 */
export async function signedIn(): Promise<boolean> {
    const sample = source.label === "sample";
    const configured = hasApiUrl();
    if (sample || !configured) return entersTheApp("unauthenticated", null, sample, configured);

    const client = lemma();
    let sdk = client.auth.getState().status;
    if (sdk === "loading") {
        /* `checkAuth` publishes its own failures into the state it keeps;
           there is nothing here to do with a rejection. */
        await client.auth.checkAuth().catch(() => undefined);
        sdk = client.auth.getState().status;
    }
    if (sdk === "authenticated") return entersTheApp(sdk, null, sample, configured);

    if (client.auth.isTokenMode) return false;

    /* Only now, and only once: the SDK's "no" is not the API's. */
    return entersTheApp(sdk, Boolean(await askTheApi()), sample, configured);
}
