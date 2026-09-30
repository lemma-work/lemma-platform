/** A bot that answers when a comment names it.
 *
 *  Not a feature of its own on the server: a DATASTORE schedule on the
 *  comments table that fires on a new row whose `mentioned_agent` is this
 *  bot. The run is handed the row, reads the page, does what was asked, and
 *  answers by adding a reply row — which the page shows live. The reply names
 *  no bot, so it never wakes one: no loop.
 */

import type { StandingJob } from "@/schedule/schedules";
import { COMMENTS_TABLE } from "./model";

function record(value: unknown): Record<string, unknown> {
    return value && typeof value === "object" && !Array.isArray(value) ? (value as Record<string, unknown>) : {};
}

/** Whether a schedule is this bot's comment wake-up. */
export function isWakeFor(job: StandingJob, agentKey: string): boolean {
    if (job.kind !== "DATASTORE" || job.broken) return false;
    const config = record(job.raw.config);
    if (config.table_name !== COMMENTS_TABLE) return false;
    const condition = record(config.when).mentioned_agent;
    const shaped = record(condition);
    const values = typeof condition === "string" ? [condition]
        : Array.isArray(shaped.in) ? (shaped.in as unknown[])
        : [shaped.to, shaped.equals];
    return values.some((one) => typeof one === "string" && one.toLowerCase() === agentKey.toLowerCase());
}

export function wakeFor(jobs: StandingJob[], agentKey: string): StandingJob | null {
    return jobs.find((job) => isWakeFor(job, agentKey)) ?? null;
}

export function wakeRequest(agentKey: string, agentLabel: string): Record<string, unknown> {
    return {
        name: "comment-replies-" + agentKey.toLowerCase().replace(/[^a-z0-9]+/g, "-"),
        schedule_type: "DATASTORE",
        agent_name: agentKey,
        visibility: "POD",
        /* `to`, on inserts and updates: fires when a comment is written
           naming this bot, or when its `mentioned_agent` is set to it later —
           which is how an existing comment is asked again. Resolving a thread
           changes nothing it tests, so it wakes nobody. */
        config: {
            table_name: COMMENTS_TABLE,
            operations: ["INSERT", "UPDATE"],
            when: { mentioned_agent: { to: agentKey } },
        },
        instruction: [
            "Someone mentioned you (" + agentLabel + ") in a comment on a page. The event data is the comment row:",
            "file_path is the page, quote is the passage it is about (empty means the whole page), body is what they said, id is the comment's id, parent_id is its thread.",
            "Read the page. If they ask for a change, make it by editing that file directly, and change nothing else.",
            "Then answer in the thread: insert one row into the " + COMMENTS_TABLE + " table with file_path the same page,",
            "parent_id set to the comment's parent_id if it has one, otherwise to its id, body your reply in a sentence or two,",
            "author_agent \"" + agentLabel + "\", quote empty, and mentioned_agent left empty.",
        ].join(" "),
    };
}

/** Whether this wake-up can be fired again for a comment already written. */
export function canAskAgain(job: StandingJob): boolean {
    const operations = record(job.raw.config).operations;
    return Array.isArray(operations) && operations.includes("UPDATE");
}
