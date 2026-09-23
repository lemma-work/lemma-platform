import { apiUrl, hasApiUrl } from "@/session/client";

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
    return (process.env.NEXT_PUBLIC_APPS_DOMAIN_SUFFIX ?? "").trim();
}

/** Where somebody lands when they signed in without saying where they were
 *  going — somebody who typed the address, rather than being sent here. */
export const DEFAULT_LANDING = "/t";
