import type { Pod } from "@/data";
import { isLandingPreview } from "@/marketing/preview-mode";
import { isAskTool } from "./approval";

/** A conversation paused on the person looking at it.
 *
 *  The workflow queue was the only thing "Needs you" read, so a conversation
 *  stopped on `ask_user` or `request_approval` never reached the rail, the
 *  Teammates page or Home. That is the conversation a scheduled run leaves
 *  behind when it stops to ask something: it reads as quiet everywhere while
 *  it waits for somebody who is not there. */
export interface AskedRow {
    podId: string;
    podName: string;
    conversationId: string;
    title: string;
    kind: "question" | "approval";
    /** When it asked, for "waiting 3h" and for the order: longest first. */
    sinceMs: number;
}

interface WaitingConversation { id: string; title?: string | null }
interface PendingAsk { tool_name?: string | null; metadata?: Record<string, unknown> | null; created_at?: string | null }

/** The rows for one pod, from what the two endpoints said.
 *
 *  A conversation is WAITING for several reasons, and only some are a person:
 *  a timer, a sub-agent or a process can hold it too. The pending approvals
 *  are what say it is waiting on you — an empty list means it is waiting on
 *  something else and is not owed by anybody. */
export function askedRows(pod: { id: string; name: string }, waiting: readonly WaitingConversation[], pending: ReadonlyMap<string, readonly PendingAsk[]>): AskedRow[] {
    const rows: AskedRow[] = [];
    for (const conversation of waiting) {
        const asks = pending.get(conversation.id) ?? [];
        if (asks.length === 0) continue;
        const last = asks[asks.length - 1];
        const since = last.created_at ? new Date(last.created_at).getTime() : Number.NaN;
        rows.push({
            podId: pod.id,
            podName: pod.name,
            conversationId: conversation.id,
            title: (conversation.title ?? "").trim() || "A conversation",
            kind: isAskTool(last.tool_name, last.metadata ?? null) ? "question" : "approval",
            sinceMs: Number.isNaN(since) ? Date.now() : since,
        });
    }
    return rows;
}

/** After this, an ask has gone quiet: still listed on Home behind "older",
 *  but no longer counted on the rail or the Teammates page. A question left
 *  for weeks is almost always one nobody is going to answer, and a badge
 *  that never clears is a badge people learn to ignore. */
export const QUIET_AFTER_MS = 7 * 86_400_000;

/** The asks still worth a badge, and the ones that have gone quiet. */
export function byAge(rows: readonly AskedRow[], now: number = Date.now()): { fresh: AskedRow[]; quiet: AskedRow[] } {
    const fresh: AskedRow[] = [];
    const quiet: AskedRow[] = [];
    for (const row of rows) (now - row.sinceMs > QUIET_AFTER_MS ? quiet : fresh).push(row);
    return { fresh, quiet };
}

/** Longest-waiting first, the same order the workflow queue keeps. */
export function byAskedLongest(rows: readonly AskedRow[]): AskedRow[] {
    return [...rows].sort((a, b) => a.sinceMs - b.sinceMs);
}

/** What a row says under its title. */
export function sayAsked(row: AskedRow): string {
    return row.kind === "question" ? "Asked you something" : "Needs your approval";
}

/** Every conversation, in every pod given, paused on the caller.
 *
 *  One list call per pod — the status filter does the narrowing server-side,
 *  and the list is the caller's own conversations — then one approvals call
 *  per waiting conversation, which on a normal day is none. `allSettled`, so a
 *  pod that refuses does not empty the answer for the others. */
export async function gatherAsked(pods: readonly Pod[], sample: boolean): Promise<AskedRow[]> {
    if (sample) {
        /* The landing tour's teammates say what they wait on in their own
           words; a sample conversation asking something would be a second
           voice in a scripted scene. */
        const first = pods[0];
        if (!first || isLandingPreview()) return [];
        return [{ podId: first.id, podName: first.name, conversationId: "fixture", title: "Monday launch", kind: "question", sinceMs: Date.now() - 2 * 3_600_000 }];
    }
    /* Imported here so the pure helpers above load without a session. */
    const { lemma } = await import("@/session/client");
    const perPod = await Promise.allSettled(pods.map(async (pod) => {
        const client = lemma(pod.id);
        const listed = await client.conversations.list({ pod_id: pod.id, status: "WAITING", limit: 20 });
        const waiting = (listed.items ?? []) as WaitingConversation[];
        const pending = new Map<string, PendingAsk[]>();
        await Promise.allSettled(waiting.map(async (conversation) => {
            const asks = await client.conversations.approvals.list(conversation.id, { pod_id: pod.id });
            pending.set(conversation.id, (asks.items ?? []) as PendingAsk[]);
        }));
        return askedRows(pod, waiting, pending);
    }));
    return byAskedLongest(perPod.flatMap((one) => one.status === "fulfilled" ? one.value : []));
}
