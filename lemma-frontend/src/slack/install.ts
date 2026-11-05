/** "Add to Slack" from outside the app: what the page shows, and what the
 *  page Slack sends the browser back to says.
 *
 *  Kept apart from the screens so the decisions -- which step, and what a
 *  callback's outcome means in words -- are tested without a browser. */

import type { ConnectOutcome } from "@/connect/round-trip";
import type { Org } from "@/data/types";

export type InstallStep =
    | { kind: "loading" }
    /** Lemma's own Slack app is not set up on this server: nothing is made. */
    | { kind: "unavailable" }
    /** No organization yet: one is made first, on the click. */
    | { kind: "first" }
    /** Which organization Lemma joins the workspace for; a picker only when
     *  there is a choice to make. */
    | { kind: "choose"; orgs: Org[]; picker: boolean };

export function installStep(available: boolean | undefined, orgs: Org[] | undefined): InstallStep {
    if (available === undefined || orgs === undefined) return { kind: "loading" };
    if (!available) return { kind: "unavailable" };
    if (orgs.length === 0) return { kind: "first" };
    return { kind: "choose", orgs, picker: orgs.length > 1 };
}

/** A start that runs once however often it is asked: a double click, or React
 *  running an effect twice. The page only ever starts on a click; this is the
 *  second line, because a second start would make a second connect request. */
export function once<A extends unknown[], T>(start: (...args: A) => Promise<T>): (...args: A) => Promise<T> {
    let started: Promise<T> | null = null;
    return (...args: A) => {
        started ??= start(...args).catch((error: unknown) => {
            started = null; // A failure may be tried again.
            throw error;
        });
        return started;
    };
}

export interface InstalledNote {
    title: string;
    lead: string;
    ok: boolean;
}

/** What each refusal the callback names means, in our words. The callback's
 *  `reason` is never shown: anyone can put words in a link. */
const REFUSALS: Record<string, string> = {
    OAUTH_PROVIDER_ERROR: "Slack didn't add Lemma, so nothing changed. Try “Add to Slack” again when you're ready.",
    CONNECT_REQUEST_NOT_FOUND: "That approval took too long, or was already used. Start “Add to Slack” again.",
    CONNECT_REQUEST_STATE_REQUIRED: "Slack came back without saying which install it was. Start “Add to Slack” again.",
};
const REFUSED = "Lemma couldn't finish joining Slack just now. Start “Add to Slack” again.";

export function installedNote(outcome: ConnectOutcome | null): InstalledNote {
    if (outcome?.connect === "connected") {
        return {
            title: "Lemma is joining your workspace",
            // Only what every path does: a re-add, or a grant finished in another
            // browser, sends no message, but the Home tab is always there.
            lead: "Open Lemma's Home tab in Slack to add your first teammate. If the workspace already belongs to another Lemma organization, Lemma says so in Slack when it can.",
            ok: true,
        };
    }
    if (outcome?.connect === "error") {
        return {
            title: "Lemma isn't in Slack yet",
            lead: (outcome.code && REFUSALS[outcome.code]) || REFUSED,
            ok: false,
        };
    }
    return {
        title: "Nothing to finish here",
        lead: "This page is where Slack sends you back after “Add to Slack”.",
        ok: false,
    };
}
