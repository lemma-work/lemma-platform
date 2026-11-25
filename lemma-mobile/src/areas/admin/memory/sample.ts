import { sampleListing } from "@web/thread/memory-notes";
import type { MemorySource } from "./source";

const HOUR = 3_600_000;
const DAY = 24 * HOUR;

function ago(ms: number): string {
    return new Date(Date.now() - ms).toISOString();
}

/** The notes the sample Lemmas have written, where the server keeps them:
 *  shared in `/memory` and the Lemma's own folder under it, personal under
 *  `/me`. An index (`AGENTS.md`) is not a note and is never listed. Support
 *  has written six, two of them this week; Launch two; the rest nothing yet.
 *  Exported so a sample file viewer can open the same files. */
export const SAMPLE_MEMORY_FILES: Record<string, { path: string; updated: string; description: string; text: string }[]> = {
    "sample-support-1140": [
        { path: "/memory/AGENTS.md", updated: ago(2 * DAY), description: "", text: "# Notes\n\n- refund-thresholds.md\n- weekly-numbers.md\n" },
        { path: "/memory/refund-thresholds.md", updated: ago(2 * DAY), description: "Refunds over $200 wait for a person's yes", text: "# Refund thresholds\n\nRefunds over $200 wait for a person to say yes. Under that, send them.\n" },
        { path: "/memory/weekly-numbers.md", updated: ago(20 * DAY), description: "Where the Monday numbers come from", text: "# Weekly numbers\n\nAnswered and asked come from the tickets table, Monday to Sunday.\n" },
        { path: "/memory/agents/pod-default/shippingDelays.md", updated: ago(5 * HOUR), description: "Orders to the islands take two extra days", text: "# Shipping delays\n\nOrders to the islands take two extra days. Say so before they ask.\n" },
        { path: "/memory/agents/pod-default/northwind.md", updated: ago(12 * DAY), description: "Northwind wants replies before noon", text: "# Northwind\n\nThey read replies before noon, their time.\n" },
        { path: "/me/agents/pod-default/how-ada-likes-drafts.md", updated: ago(1 * DAY), description: "Short drafts, the order number first", text: "# How Ada likes drafts\n\nShort. The order number first. No greeting.\n" },
        { path: "/me/agents/pod-default/ada-on-call.md", updated: ago(30 * DAY), description: "", text: "# On call\n\nAda covers Fridays.\n" },
    ],
    "sample-launch-1440": [
        { path: "/memory/partner-quotes.md", updated: ago(3 * DAY), description: "Quotes come in as PDFs from two partners", text: "# Partner quotes\n\nTwo partners send PDFs; the third uses the form.\n" },
        { path: "/memory/launch-day.md", updated: ago(15 * DAY), description: "The page goes live at nine", text: "# Launch day\n\nThe page goes live at nine.\n" },
    ],
};

export function memorySample(): MemorySource {
    return {
        list: async (podId, directory) => ({ items: sampleListing(directory, SAMPLE_MEMORY_FILES[podId] ?? []), next: null }),
    };
}
