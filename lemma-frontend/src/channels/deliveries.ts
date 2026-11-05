import { platformName } from "./sources";
import { sourceLabel, type LineDelivery, type SourcePlace } from "./model";

/** Where the reader's own line went outside Lemma, and what became of it --
 *  said under the line: sent, failed and why, skipped and why, and a Retry
 *  for a send that can be tried again.
 *
 *  The line carries it (`delivered`, one entry per place, on the writer's own
 *  lines only), and when a send's status moves the server sends the line
 *  again as `line.edited`, so nothing here asks for it. */

export interface DeliveryNote {
    id: string;
    tone: "pending" | "sent" | "failed" | "skipped";
    /** "Sent to #launch (Slack)", "Couldn’t reach WhatsApp: …". */
    said: string;
    /** A failed or given-up send can be tried again now. */
    retry: boolean;
}

/** A reason as the server gives it -- a code or a sentence -- said plainly. */
export function sayReason(reason: string | null): string | null {
    if (!reason) return null;
    /* The executor says why in a sentence; older rows carry a code. */
    if (/\s/.test(reason)) return reason.replace(/\.$/, "");
    switch (reason) {
        case "outside_window": return "WhatsApp only allows a reply within a day of their last message";
        case "unsubscribed":
        case "opted_out": return "they asked not to be written to there";
        case "cap_reached":
        case "daily_cap": return "today's limit for writing to them is reached";
        case "no_surface":
        case "surface_missing": return "the bot that reached them is gone";
        default: return reason.replaceAll("_", " ");
    }
}

/** What each send of a line says, in the order the server lists them. */
export function deliveryNotes(delivered: readonly LineDelivery[] | null, places: readonly SourcePlace[]): DeliveryNote[] {
    return (delivered ?? []).map((one) => {
        const where = sourceLabel(one.sourceId, places) ?? (one.platform ? platformName(one.platform) : "outside");
        const why = sayReason(one.reason);
        switch (one.status) {
            case "PENDING": return { id: one.id, tone: "pending", said: "Sending to " + where + "…", retry: false };
            case "SENT": return { id: one.id, tone: "sent", said: "Sent to " + where, retry: false };
            case "FAILED": return { id: one.id, tone: "failed", said: "Failed to reach " + where + (why ? ": " + why : "") + " — trying again", retry: true };
            case "DEAD": return { id: one.id, tone: "failed", said: "Couldn’t reach " + where + (why ? ": " + why : ""), retry: true };
            case "SKIPPED": return { id: one.id, tone: "skipped", said: "Not sent to " + where + (why ? ": " + why : ""), retry: false };
        }
    });
}
