import type { LemmaClient } from "lemma-sdk";
import { needItems, readableName, type PendingAsk, type WaitRow } from "./model";
import { conversationRefOf, items, readChannel, record, words } from "./wire";
import type { Channel, ConversationRef, Lemma, NeedItem } from "./types";

/** The reads from before the organization had its own (#1045), one Lemma
 *  at a time, for a server that does not answer those yet. A phone can be
 *  newer than the server it is pointed at, so the live source asks the
 *  organization first and comes here on a 404, until the server is updated.
 *  Delete this once every server the app knows has them. */

async function settled<T>(work: Promise<T>, fallback: T): Promise<T> {
    try {
        return await work;
    } catch {
        return fallback;
    }
}

function readWaits(listed: unknown, names: ReadonlyMap<string, string>): WaitRow[] {
    return items(listed).flatMap((entry) => {
        const wait = record(record(entry).wait);
        const run = record(record(entry).run);
        const id = words(wait.id);
        const runId = words(wait.run_id) ?? words(run.id);
        /* A form waits as HUMAN (`WorkflowRunWaitType`): the queue's FORM. */
        const said = words(wait.wait_type);
        const type = said === "HUMAN" || said === "FORM" ? "FORM" : said === "DECISION" ? "DECISION" : null;
        if (!id || !runId || !type) return [];
        const schema = record(record(wait.payload).schema);
        const answer = record(record(schema.properties).answer);
        return [{
            id,
            type,
            question: words(answer.description),
            createdAt: words(wait.created_at),
            runId,
            workflowName: names.get(words(run.workflow_id) ?? "") ?? "a workflow",
        }];
    });
}

/** What waits on the reader in one Lemma: its pauses, the workflow forms
 *  and decisions assigned to them, and the contacts waiting on a person. */
export async function needsIn(pod: LemmaClient, one: Lemma, now: number): Promise<NeedItem[]> {
    const [asks, waits, contacts] = await Promise.all([
        settled(pod.conversations.pendingInteractions({ pod_id: one.id, limit: 100 } as never).then((listed) => items(listed) as PendingAsk[]), [] as PendingAsk[]),
        settled((async () => {
            const owed = await pod.workflows.runs.waitingAssignedToMe({ limit: 50 } as never);
            if (items(owed).length === 0) return [] as WaitRow[];
            const flows = await settled(pod.workflows.list({ limit: 100 } as never), { items: [] } as unknown);
            const names = new Map(items(flows).map((flow) => [words(record(flow).id) ?? "", readableName(words(record(flow).name) ?? "a workflow")]));
            return readWaits(owed, names);
        })(), [] as WaitRow[]),
        settled(pod.channels.list(one.id, { kind: "CONTACT", archived: false, needsPerson: true } as never).then((listed) => items(listed).map(readChannel).filter((channel): channel is Channel => channel !== null)), [] as Channel[]),
    ]);
    return needItems(one, asks, waits, contacts, now);
}

/** A Lemma's recent conversations, the first page, newest first. */
export async function conversationsIn(pod: LemmaClient, one: Lemma): Promise<ConversationRef[]> {
    return settled(pod.conversations.list({ pod_id: one.id, limit: 25, in_channel: false } as never).then((listed) => items(listed).map(conversationRefOf)), [] as ConversationRef[]);
}
