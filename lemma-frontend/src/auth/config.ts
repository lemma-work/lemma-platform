import { apiUrl, hasApiUrl } from "@/session/client";
import { siteRuntime } from "@/site/runtime";

/** SuperTokens' API base, under the gateway prefix. The SDK spells this the
 *  same way for the session it manages; if one moves, both move. */
export const ST_BASE = "/st/auth";

/** Where the portal itself answers, in this app. */
export const PORTAL_PATH = "/auth";

/** An absolute URL on the API, or "" when no API is configured.
 *
 *  Empty rather than a throw, for the reason `upstreamUrl` is: every caller is
 *  building a URL during a render, and the one screen somebody reaches when
 *  nothing is configured must not be the screen that crashes. */
export function onApi(path: string): string {
    if (!hasApiUrl()) return "";
    const base = apiUrl().replace(/\/$/, "");
    return base + (path.startsWith("/") ? path : "/" + path);
}

/** A SuperTokens endpoint, absolute. */
export function stUrl(path: string): string {
    return onApi(ST_BASE + (path.startsWith("/") ? path : "/" + path));
}

/** This origin, which is the only site origin the portal has.
 *
 *  Read from the browser rather than from an environment variable. The portal
 *  runs in exactly one place — the page it is on — and a configured value that
 *  disagreed with it would send somebody to the other deployment mid-sign-in,
 *  which is the failure this whole move exists to end. */
export function siteOrigin(): string {
    return typeof window === "undefined" ? "" : window.location.origin;
}

export function appsDomainSuffix(): string {
    return siteRuntime().appsDomainSuffix.trim();
}

/** Where somebody lands when they signed in without saying where they were
 *  going — somebody who typed the address, rather than being sent here. */
export const DEFAULT_LANDING = "/t";

declare global {
    interface Window {
        /** Injected by the desktop shell before any page script runs: its own,
         *  trusted statement of the local installation's auth policy. */
        __LEMMA_AUTH_CONFIG__?: Record<string, string | undefined>;
    }
}

/** Auth gates the browser has to agree with the backend about, and where the
 *  deployment's own value for each is read from.
 *
 *  `scripts/check_local_auth_gates.py` reads the keys of this table: a gate
 *  named here must also be rendered into the frontend's environment wherever
 *  the backend is told to relax it, or the two disagree and somebody signs up
 *  into a screen asking them to verify an address no email will reach. */
const MIRRORED_GATES = {
    "AUTH_EMAIL_VERIFICATION_REQUIRED": () => siteRuntime().authEmailVerificationRequired,
} as const;

function gate(name: keyof typeof MIRRORED_GATES): boolean | null {
    /* The desktop shell's word first. A frontend that outlived the pack that
       started it -- or `/site-config.js` failing to load -- must not send a
       local account into a hosted verification flow while offline. */
    const shell = typeof window === "undefined" ? undefined : window.__LEMMA_AUTH_CONFIG__?.[name];
    const raw = (shell?.trim() || MIRRORED_GATES[name]().trim()).toLowerCase();
    if (!raw) return null;
    return !["0", "false", "no", "off"].includes(raw);
}

/** Whether a new account has to prove its address before it may start.
 *
 *  Only a deployment that says so gets the extra step. Unset keeps what this
 *  app has always done -- straight into the workspace -- because the backend
 *  is the authority on the gate and a hosted deployment that never set this
 *  must not start mailing everybody who signs up. */
export function emailVerificationRequired(): boolean {
    return gate("AUTH_EMAIL_VERIFICATION_REQUIRED") ?? false;
}

/** Whether the URL asked for the sign-up form rather than sign-in.
 *
 *  `show=signup` is how the desktop shell opens a fresh installation, which
 *  has no account to sign in to yet, and how older links spell it. The hash
 *  is read too, because a router that pushed the parameter there is still
 *  somebody asking for sign-up. */
export function asksForSignUp(search: string, hash = ""): boolean {
    const fromSearch = new URLSearchParams(search).get("show");
    const fromHash = new URLSearchParams(hash.replace(/^#/, "")).get("show");
    return (fromSearch ?? fromHash) === "signup";
}
