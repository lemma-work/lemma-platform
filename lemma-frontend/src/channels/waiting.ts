/** What is waiting on the person: the questions put to them, read from the
 *  pod's inbox, and how each one is answered.
 *
 *  A question is a line in a channel that waits on one person -- an agent's
 *  `ask_user`, a request to approve something, a member's agent asking them
 *  something, a workflow's form. It is answered on the line itself, once.
 */

export interface PersonRef {
    kind: "MEMBER" | "CONTACT" | "AGENT";
    id: string | null;
    name: string | null;
}

export interface WaitingQuestion {
    /** The line's id: what an answer is posted against. */
    id: string;
    channelId: string;
    channelTitle: string | null;
    channelKind: "DIRECT" | "CONTACT" | "GROUP";
    kind: "ASK" | "APPROVAL" | "FORM";
    text: string;
    from: string;
    askedAt: string;
    question: Record<string, unknown>;
    /** The thread it was asked in, when it is a reply: opening it opens that. */
    threadRootId: string | null;
}

/** A line naming the reader that they have not read yet. */
export interface MentionItem {
    id: string;
    seq: number;
    channelId: string;
    channelTitle: string | null;
    channelKind: "DIRECT" | "CONTACT" | "GROUP";
    from: string;
    text: string;
    at: string;
    /** The thread it was said in, when it is a reply. */
    threadRootId: string | null;
}

export interface Inbox {
    channels: number;
    mentions: number;
    waiting: number;
    items: WaitingQuestion[];
    mentioned: MentionItem[];
}

type Wire = Record<string, unknown>;

const record = (value: unknown): Wire =>
    value && typeof value === "object" ? (value as Wire) : {};

function sender(line: Wire): string {
    const who = record(line.sender);
    if (typeof who.name === "string" && who.name) return who.name;
    return who.kind === "AGENT" ? "An agent" : who.kind === "CONTACT" ? "A contact" : "A member";
}

function threadOf(line: Wire): string | null {
    return typeof line.thread_root_id === "string" && line.thread_root_id ? line.thread_root_id : null;
}

/** The inbox as the API returns it, read into what the app draws. */
export function readInbox(raw: unknown): Inbox {
    const body = record(raw);
    const totals = record(body.totals);
    const items: WaitingQuestion[] = [];
    for (const entry of Array.isArray(body.waiting) ? body.waiting : []) {
        const channel = record(record(entry).channel);
        const line = record(record(entry).message);
        const interaction = record(line.question);
        if (typeof line.id !== "string" || typeof interaction.kind !== "string") continue;
        items.push({
            id: line.id,
            channelId: String(channel.id ?? ""),
            channelTitle: typeof channel.title === "string" ? channel.title : null,
            channelKind: (channel.kind as WaitingQuestion["channelKind"]) ?? "DIRECT",
            kind: interaction.kind as WaitingQuestion["kind"],
            text: typeof line.text === "string" ? line.text : "",
            from: sender(line),
            askedAt: String(line.created_at ?? ""),
            question: record(interaction.question),
            threadRootId: threadOf(line),
        });
    }
    const mentioned: MentionItem[] = [];
    for (const entry of Array.isArray(body.mentions) ? body.mentions : []) {
        const channel = record(record(entry).channel);
        const line = record(record(entry).message);
        if (typeof line.id !== "string" || typeof channel.id !== "string") continue;
        mentioned.push({
            id: line.id,
            seq: typeof line.seq === "number" ? line.seq : 0,
            channelId: channel.id,
            channelTitle: typeof channel.title === "string" ? channel.title : null,
            channelKind: (channel.kind as MentionItem["channelKind"]) ?? "GROUP",
            from: sender(line),
            text: line.withheld === true ? "" : typeof line.text === "string" ? line.text : "",
            at: String(line.created_at ?? ""),
            threadRootId: threadOf(line),
        });
    }
    return {
        channels: Number(totals.channels ?? 0),
        mentions: Number(totals.mentions ?? 0),
        waiting: Number(totals.waiting ?? items.length),
        items,
        mentioned,
    };
}

/** How a question is answered here.
 *
 *  - `choose`  -- an agent's question with choices: pick one, or write your own.
 *  - `approve` -- an action waiting for a yes or a no.
 *  - `form`    -- a workflow's form, drawn and submitted here.
 *  - `write`   -- anything else: an answer in words.
 */
export type AnswerMove = "choose" | "approve" | "form" | "write";

export function moveFor(question: WaitingQuestion): AnswerMove {
    if (question.kind === "APPROVAL") return "approve";
    if (question.kind === "FORM") return formTarget(question) ? "form" : "write";
    return choicesOf(question).length ? "choose" : "write";
}

/** The first question's choices, for an agent's `ask_user`. */
export function choicesOf(question: WaitingQuestion): string[] {
    const asked = question.question.questions;
    const first = Array.isArray(asked) ? record(asked[0]) : {};
    const options = Array.isArray(first.options) ? first.options : [];
    return options
        .map((option) => record(option).label)
        .filter((label): label is string => typeof label === "string" && label.length > 0);
}

/** The run and node a form is submitted against, as the question names them. */
export function formTarget(question: WaitingQuestion): { runId: string; nodeId: string } | null {
    const runId = question.question.run_id;
    const nodeId = question.question.node_id;
    return typeof runId === "string" && typeof nodeId === "string" && runId && nodeId
        ? { runId, nodeId }
        : null;
}

/** What the badge says: nothing, a count, or "99+". */
export function badgeCount(waiting: number): string | null {
    if (waiting <= 0) return null;
    return waiting > 99 ? "99+" : String(waiting);
}

/** What the bell's badge counts: the questions waiting on the reader, the
 *  lines naming them, and the new lines in their own channel -- each of
 *  which the inbox lists -- and the sentence that says it. */
export function bellCount(inbox: Pick<Inbox, "waiting" | "mentions"> | null, directLines: number): { badge: string | null; said: string } {
    const waiting = inbox?.waiting ?? 0;
    const mentions = inbox?.mentions ?? 0;
    const parts: string[] = [];
    if (waiting) parts.push(waiting === 1 ? "1 question waiting on you" : waiting + " questions waiting on you");
    if (mentions) parts.push(mentions === 1 ? "1 mention of you" : mentions + " mentions of you");
    if (directLines) parts.push(directLines === 1 ? "1 new message for you" : directLines + " new messages for you");
    return { badge: badgeCount(waiting + mentions + directLines), said: parts.length ? parts.join(", ") : "Nothing new for you" };
}

/** Where it was asked, said plainly. */
export function whereAsked(question: WaitingQuestion): string {
    if (question.channelKind === "DIRECT") return "Sent to you";
    return question.channelTitle ? "In " + question.channelTitle : "In a shared chat";
}

/** A run carrying a wait, as much of it as the form readers need. */
type WaitingRun = {
    active_wait?: {
        node_id?: string;
        payload?: { input_schema?: unknown; ui_schema?: unknown } | null;
    } | null;
} | null | undefined;

/** The payload of the wait this node is stopped on, or nothing.
 *
 *  The node has to match: a run moves on, and a form submitted against a node
 *  it has already passed is a 422 -- so a stale question says the work moved
 *  rather than drawing a form that cannot be sent.
 */
function waitingPayload(run: WaitingRun, nodeId: string): Record<string, unknown> | null {
    const wait = run?.active_wait;
    if (!wait || wait.node_id !== nodeId) return null;
    const payload = wait.payload;
    return payload && typeof payload === "object" ? (payload as Record<string, unknown>) : null;
}

function plainObject(value: unknown): Record<string, unknown> | null {
    return value && typeof value === "object" && !Array.isArray(value)
        ? (value as Record<string, unknown>)
        : null;
}

/** The schema a run is waiting on, if it is still waiting on this node. */
export function waitingSchema(run: WaitingRun, nodeId: string): Record<string, unknown> | null {
    return plainObject(waitingPayload(run, nodeId)?.input_schema);
}

/** How the author meant the form to read: without `ui:order` the fields come
 *  out alphabetically by label, an order nobody chose. */
export function waitingUiSchema(run: WaitingRun, nodeId: string): Record<string, unknown> | null {
    return plainObject(waitingPayload(run, nodeId)?.ui_schema);
}
