/** Where a sign-in goes next, in words: "Then on to Renewals".
 *
 *  The old page printed the address ("localhost:3000/apps"), which is true
 *  and says nothing a person can recognise. This names the place instead, and
 *  says nothing at all when the answer is only "the default": a sentence that
 *  always says "your workspace" is one nobody reads.
 *
 *  Pure, so it can be tested without a browser. The caller reads the held
 *  requests and the destination and passes them in.
 */

import { waitingFor } from "./waiting";

export interface Heading {
    /** The destination, already checked by `pendingDestination`. */
    destination: string | null;
    /** This origin, to tell a page of this app from an app on its own host. */
    origin: string;
    /** The hostname suffix deployed apps answer on, or "" for none. */
    appsSuffix?: string;
    /** A browser signing in for Lemma Desktop, the CLI, or an app connecting. */
    desktop?: boolean;
    cli?: boolean;
    consent?: boolean;
}

const INVITATION = /^\/invitations\/[0-9a-f-]{36}\/accept\/?$/i;
const TEMPLATE = /^\/import\/github\/[^/]+\/([^/]+)\/?$/;
const SLACK = /^\/slack\/install\/?$/;
/** Where somebody lands anyway: not worth a sentence. */
const DEFAULTS = new Set(["/", "/home", "/t", "/o"]);

/** "the Lemma CLI", "Renewals", "your workspace" -- or null for nothing worth
 *  naming. Read as "Then on to {this}". */
export function headingTo(where: Heading): string | null {
    if (where.desktop) return "Lemma Desktop";
    if (where.cli) return "the Lemma CLI";
    if (where.consent) return "connecting an app";
    if (!where.destination) return null;

    let url: URL;
    try {
        url = new URL(where.destination, where.origin || "https://lemma.invalid");
    } catch {
        return null;
    }
    const sameSite = !where.origin || url.origin === new URL(where.origin).origin;
    if (!sameSite) {
        const suffix = (where.appsSuffix ?? "").replace(/^\./, "");
        return suffix && url.hostname.endsWith(suffix) ? "the app you were opening" : null;
    }

    const path = url.pathname;
    if (INVITATION.test(path)) return "your invitation";
    const template = TEMPLATE.exec(path);
    if (template) return "adding " + decodeURIComponent(template[1]) + " from GitHub";
    if (SLACK.test(path)) return "adding Lemma to Slack";

    const waiting = waitingFor(path);
    if (waiting.id) return waiting.name ?? "that Lemma";
    if (DEFAULTS.has(path.replace(/\/$/, "") || "/")) return null;
    return "your workspace";
}
