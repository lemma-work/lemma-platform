import { resolveSafeRedirectUri } from "lemma-sdk";
import { appsDomainSuffix, DEFAULT_LANDING, PORTAL_PATH, siteOrigin, onApi } from "./config";
import { heldRequestId } from "@/desktop/auth-handoff";

/** Held across the round trip to Google or Microsoft, which leaves and
 *  re-enters this app with a URL we did not write. `sessionStorage` rather
 *  than `localStorage`: it belongs to this tab and this sign-in, and a
 *  destination left behind in a shared store is one a later, different sign-in
 *  would silently obey. */
const KEY = "lemma-app:auth:after";

/** A sentinel the resolver hands back when it refuses. Never navigated to —
 *  its only job is to be recognisable, so a refusal can be told apart from a
 *  destination that merely happens to be the default. */
const REFUSED = "/__refused";

/** The parameter names a caller might use. `redirect_uri` is what this app and
 *  the SDK send; the other two are what older links and the platform's own
 *  pages have used, and a link already in somebody's inbox does not get to
 *  stop working because the spelling was tidied. */
const PARAMS = ["redirect_uri", "redirectTo", "redirectBack", "next"] as const;

export function rawDestination(search: string): string | null {
    const params = new URLSearchParams(search);
    for (const name of PARAMS) {
        const value = params.get(name);
        if (value) return value;
    }
    return null;
}

/** What this app will actually honour, or null.
 *
 *  Null means refused, and the caller is expected to say so rather than
 *  quietly substituting the default — that substitution is the entire bug this
 *  replaces.
 */
export interface Where {
    /** The origin this portal is served from. */
    origin: string;
    /** Hostname suffix for deployed pod apps, or "" for none. */
    appsSuffix: string;
    apiOrigin?: string;
}

/** The decision, with nowhere read from the browser.
 *
 *  Separated so it can be tested at all: the runner has no `window`, and a
 *  security rule that is only exercised by clicking through a browser is one
 *  nobody exercises. `safeDestination` below is the same call with this app's
 *  own origin filled in.
 */
export function safeDestinationIn(raw: string | null, where: Where): string | null {
    if (!raw) return null;
    const { origin, appsSuffix: suffix } = where;
    if (!origin) return null;

    const resolved = resolveSafeRedirectUri(raw, {
        siteOrigin: origin,
        fallback: REFUSED,
        /* The portal itself, so a redirect back into sign-in cannot make a
           loop that looks like a broken password. */
        blockedPaths: [PORTAL_PATH, "/login", "/signup", "/verify-email", "/reset-password"],
        allowedOrigins: where.apiOrigin ? [where.apiOrigin] : undefined,
        /* A deployed pod app is first-party even though it is not this origin,
           and is where somebody signing in from an app expects to return. */
        allowedOriginSuffixes: suffix ? [suffix] : undefined,
    });

    if (!resolved) return null;
    try {
        return new URL(resolved, origin).pathname === REFUSED ? null : resolved;
    } catch {
        return null;
    }
}

/** What this app will honour, asked of this app. */
export function safeDestination(raw: string | null): string | null {
    return safeDestinationIn(raw, { origin: siteOrigin(), appsSuffix: appsDomainSuffix(), apiOrigin: onApi("/") });
}

/** The destination a URL asks for, if this app will honour it. */
export function destinationFrom(search: string): string | null {
    return safeDestination(rawDestination(search));
}

/** Whether a URL asked for one at all, which is a different question from
 *  whether it may have it: a link with no `redirect_uri` is somebody who typed
 *  the address, and gets the default without being told anything was refused. */
export function asksForDestination(search: string): boolean {
    return rawDestination(search) !== null;
}

export function rememberDestination(destination: string | null): void {
    try {
        if (destination) window.sessionStorage.setItem(KEY, destination);
        else window.sessionStorage.removeItem(KEY);
    } catch {
        /* A browser refusing session storage still signs people in; they land
           on the default instead of where they were going. */
    }
}

/** Take it back out, once. Read-and-clear rather than read, because a
 *  destination that outlived its sign-in would be obeyed by the next one. */
export function takeDestination(): string | null {
    const destination = storedDestination();
    forgetDestination();
    return destination;
}

export function storedDestination(): string | null {
    try {
        const held = window.sessionStorage.getItem(KEY);
        return safeDestination(held);
    } catch {
        return null;
    }
}

export function forgetDestination(): void {
    try {
        window.sessionStorage.removeItem(KEY);
    } catch {
        /* nothing held, nothing to drop */
    }
}

/** Where to go now: back to the desktop app if this browser is signing in for
 *  it, else what they asked for, what they asked for before the round trip, or
 *  the workspace. */
export function landing(search: string): string {
    if (heldRequestId()) return PORTAL_PATH + "/desktop";
    const held = takeDestination();
    return asksForDestination(search)
        ? destinationFrom(search) ?? DEFAULT_LANDING
        : held ?? DEFAULT_LANDING;
}

export function pendingDestination(search: string): string | null {
    return asksForDestination(search) ? destinationFrom(search) : storedDestination();
}

/** Carry only the destination, never a password-reset token or OAuth code. */
export function authLink(path: string, search = window.location.search): string {
    const destination = pendingDestination(search);
    return destination ? path + "?redirect_uri=" + encodeURIComponent(destination) : path;
}
