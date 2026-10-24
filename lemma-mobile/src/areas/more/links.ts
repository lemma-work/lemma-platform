/** Links from outside the app: an invitation in an email, an install or a
 *  remix button on a page, a Lemma's address somebody sent, a sign-in a
 *  Lemma asked for. Pure, so `tests/more-links.test.ts` holds it.
 *
 *  The phone's screens sit at the web's own paths (`moreRoutes`), so a link
 *  in the web's grammar needs only its host dropped: `https://lemma.work/
 *  invitations/<id>/accept` and `lemma://invitations/<id>/accept` both open
 *  `/invitations/<id>/accept`. The few the phone keeps elsewhere are said
 *  here: a Lemma is `/lemma/<id>` on the phone and `/t/<id>` on the web. */

import { PRESETS } from "../../session/server";

/** The web hosts whose links the app opens: lemma.work's and those of the
 *  servers the build names (`android.intentFilters`, app.json and
 *  app.config.js). A custom server's links open in the browser. */
export const LINK_HOSTS = PRESETS.flatMap((server) => {
    const host = /^https?:\/\/([^/:]+)/i.exec(server.web)?.[1]?.toLowerCase();
    return host ? [host, "www." + host] : [];
});

const SCHEME = /^lemma:\/\//i;

/** The path and query a link names, or null for one that is not ours. */
function pathOf(url: string): string | null {
    const said = url.trim();
    if (SCHEME.test(said)) return "/" + said.replace(SCHEME, "").replace(/^\/+/, "");
    const web = /^https?:\/\/([^/?#]+)(\/[^#]*)?/i.exec(said);
    if (web) return LINK_HOSTS.includes(web[1].toLowerCase()) ? web[2] || "/" : null;
    return said.startsWith("/") ? said : null;
}

/** One id-like segment: never "." or "..", never an escaped character. */
const SAFE = /^(?!\.{1,2}$)[A-Za-z0-9._~-]+$/;

/** Where on the phone a link opens, or null when it is not one of the links
 *  this file knows (the router is then left to read it as it is). */
export function appPathOf(url: string): string | null {
    const path = pathOf(url);
    if (path === null) return null;
    const [bare, query = ""] = path.split("?", 2);
    const parts = bare.split("/").filter(Boolean);
    const [head, one, two, three] = parts;
    const search = query ? "?" + query : "";
    if (head === "invitations" && one && SAFE.test(one) && (two === "accept" || two === "reject") && parts.length === 3) return `/invitations/${one}/${two}`;
    if (head === "import" && one === "github" && two && three && SAFE.test(two) && SAFE.test(three) && parts.length === 4) return `/import/github/${two}/${three}`;
    if (head === "remix" && parts.length === 1) return "/remix" + search;
    if (head === "templates") return "/templates";
    if (head === "sign-in-to-site" && one && two && SAFE.test(one) && SAFE.test(two) && parts.length === 3) return `/sign-in-to-site/${one}/${two}`;
    if (head === "join" && one && SAFE.test(one) && parts.length === 2) return `/join/${one}`;
    /* A Lemma, and anything inside it: the phone opens the Lemma. */
    if (head === "t" && one && SAFE.test(one)) return `/lemma/${one}`;
    if (head === "t" || head === "o") return "/";
    return null;
}

/** Whether a path is one a person reaches by a link, and so one worth
 *  keeping across a sign-in. */
export function isLinkPath(path: string): boolean {
    return /^\/(invitations|import|remix|templates|sign-in-to-site|lemma|join)(\/|\?|$)/.test(path);
}

/* ── a link that arrived while signed out ─────────────────────────────── */

/** How long a link waits for somebody to sign in. */
export const LINK_WAIT_MS = 15 * 60_000;

let waiting: { path: string; at: number } | null = null;

/** Hold a link until its screen is open. Signed out, the router shows the
 *  sign-in screen instead, and the link would be gone once they are in. */
export function rememberLink(path: string, now = Date.now()): void {
    if (isLinkPath(path)) waiting = { path, at: now };
}

/** The link's screen opened: nothing is waiting any more. */
export function linkArrived(pathname: string): void {
    if (waiting && waiting.path.split("?")[0] === pathname.split("?")[0]) waiting = null;
}

/** The link still waiting, if it is recent, taken so it opens once. */
export function takeWaitingLink(now = Date.now()): string | null {
    const held = waiting;
    waiting = null;
    return held && now - held.at < LINK_WAIT_MS ? held.path : null;
}
