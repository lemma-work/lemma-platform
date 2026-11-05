import type { Pod } from "@/data";
import { isLandingPreview } from "@/marketing/preview-mode";
import { NOWHERE, writeAddress } from "@/shell/address";

/** A conversation paused on the person looking at it.
 *
 *  The workflow queue was the only thing "Needs you" read, so a conversation
 *  stopped on `ask_user` or `request_approval` never reached the rail, the
 *  Teammates page or Home. That is the conversation a scheduled run leaves
 *  behind when it stops to ask something: it reads as quiet everywhere while
 *  it waits for somebody who is not there.
 *
 *  A permission request is the third kind: an agent running on an Agent Host
 *  asking before it uses a tool on that computer. Its conversation stays
 *  running while it asks, which is why reading WAITING conversations never
 *  found it. */
export interface AskedRow {
    podId: string;
    podName: string;
    conversationId: string;
    title: string;
    kind: "question" | "approval" | "permission";
    /** The question, or what it wants to do, on one line — when it said. */
    summary: string | null;
    /** When it asked, for "waiting 3h" and for the order: longest first. */
    sinceMs: number;
}

/** One pause as `conversations.pendingInteractions` lists it. */
interface PendingAsk {
    conversation_id: string;
    conversation_title?: string | null;
    kind?: string | null;
    summary?: string | null;
    created_at?: string | null;
}

const KINDS: Record<string, AskedRow["kind"]> = { QUESTION: "question", APPROVAL: "approval", PERMISSION: "permission" };

/** The rows for one pod, one per conversation, from what the server listed.
 *
 *  The list is longest-waiting first, so the first pause seen for a
 *  conversation is the one that has waited longest — the row says how long
 *  the conversation has been waiting, not how long its newest ask has. */
export function askedRows(pod: { id: string; name: string }, pending: readonly PendingAsk[]): AskedRow[] {
    const rows = new Map<string, AskedRow>();
    for (const ask of pending) {
        if (rows.has(ask.conversation_id)) continue;
        const since = ask.created_at ? new Date(ask.created_at).getTime() : Number.NaN;
        rows.set(ask.conversation_id, {
            podId: pod.id,
            podName: pod.name,
            conversationId: ask.conversation_id,
            title: (ask.conversation_title ?? "").trim() || "A conversation",
            kind: KINDS[ask.kind ?? ""] ?? "approval",
            summary: (ask.summary ?? "").trim() || null,
            sinceMs: Number.isNaN(since) ? Date.now() : since,
        });
    }
    return [...rows.values()];
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
    if (row.kind === "question") return "Asked you something";
    if (row.kind === "permission") return "Asks permission to go on";
    return "Needs your approval";
}

/** Where a row opens: its conversation, in its own teammate's space — the
 *  inbox crosses teammates, so it cannot assume the one on screen. */
export function askedHref(row: Pick<AskedRow, "podId" | "conversationId">): string {
    return writeAddress({ ...NOWHERE, podId: row.podId, tabId: "conversation", conversationId: row.conversationId });
}

/** Every conversation, in every pod given, paused on the caller.
 *
 *  One request per pod: the server lists every pause waiting on the caller
 *  across their conversations there, permission requests included. It used to
 *  be a list of WAITING conversations and then a request per conversation,
 *  which could not see a permission request at all. `allSettled`, so a pod
 *  that refuses does not empty the answer for the others. */
export async function gatherAsked(pods: readonly Pick<Pod, "id" | "name">[], sample: boolean): Promise<AskedRow[]> {
    if (sample) {
        /* The landing tour's teammates say what they wait on in their own
           words; a sample conversation asking something would be a second
           voice in a scripted scene. */
        const first = pods[0];
        if (!first || isLandingPreview()) return [];
        return [{ podId: first.id, podName: first.name, conversationId: "fixture", title: "Monday launch", kind: "question", summary: "Which day should the launch post go out?", sinceMs: Date.now() - 2 * 3_600_000 }];
    }
    /* Imported here so the pure helpers above load without a session. */
    const { lemma } = await import("@/session/client");
    const perPod = await Promise.allSettled(pods.map(async (pod) => {
        const listed = await lemma(pod.id).conversations.pendingInteractions({ pod_id: pod.id, limit: 100 });
        return askedRows(pod, (listed.items ?? []) as PendingAsk[]);
    }));
    return byAskedLongest(perPod.flatMap((one) => one.status === "fulfilled" ? one.value : []));
}

/** The query every "waiting on you" list reads: the inbox beside the bell,
 *  the rail's badges and Home. One key, so the three share one request.
 *
 *  Polled once a minute while it is on screen. Nothing pushes a new ask — a
 *  run that stops on a question in another conversation tells nobody — so a
 *  list that only refreshed on navigation could sit on a page for an hour
 *  saying nothing was owed. React Query does not poll a hidden tab, so the
 *  poll costs nothing while the window is in the background. */
export const CONVERSATION_ASKS = "conversation-asks";
export const ASKS_REFETCH_MS = 60_000;

export function conversationAsksQuery(pods: readonly Pick<Pod, "id" | "name">[], sample: boolean, enabled = true) {
    return {
        queryKey: [CONVERSATION_ASKS, pods.map((pod) => pod.id).join(",")],
        queryFn: () => gatherAsked(pods, sample),
        enabled: enabled && pods.length > 0,
        staleTime: ASKS_REFETCH_MS,
        refetchInterval: ASKS_REFETCH_MS,
    };
}
