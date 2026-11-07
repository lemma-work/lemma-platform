import type { RawMessage } from "@web/thread/turns";
import { originOf } from "./origin";
import type { PendingAsk } from "./model";
import type { ChatLine, LemmaPulse, LemmaSource } from "./source";
import type { AppItem, Channel, ConversationRef, Lemma, NeedCard, NeedItem, Owns, Starter } from "./types";

/** Sample data shaped like the design canvas, so every screen can be built
 *  and checked without signing in. Generic on purpose: Acme, its five
 *  Lemmas, and nobody real. The fixtures are raw reads -- conversations,
 *  pauses, channels -- and the same model functions the live source feeds
 *  turn them into the queue and the rows, so sample mode tests that code
 *  too. (`lemma-frontend/src/data/samples.ts` is the web's version.) */

const MIN = 60_000;
const HOUR = 60 * MIN;
const DAY = 24 * HOUR;

/** Ids chosen so each face lands on the canvas's tint and a different
 *  character (`characterForSeed`, `hueOf`). */
export const SAMPLE_LEMMAS: Lemma[] = [
    { id: "sample-support-1140", name: "Support", iconUrl: null, description: "Answers customers." },
    { id: "sample-launch-1440", name: "Launch", iconUrl: null, description: "Runs the October launch." },
    { id: "sample-renewals-790", name: "Renewals", iconUrl: null, description: "Keeps renewals on time." },
    { id: "sample-hiring-1078", name: "Hiring", iconUrl: null, description: "Fills the open roles." },
    { id: "sample-books-986", name: "Books", iconUrl: null, description: "Keeps the books." },
];

const [SUPPORT, LAUNCH, RENEWALS, HIRING, BOOKS] = SAMPLE_LEMMAS;

function iso(ago: number, now: number): string {
    return new Date(now - ago).toISOString();
}

function chat(id: string, title: string, ago: number, now: number, extra: Partial<ConversationRef> & { meta?: Record<string, unknown> } = {}): ConversationRef {
    const { meta, ...rest } = extra;
    return {
        id,
        title,
        activityAt: iso(ago, now),
        kind: "CHAT",
        boundTo: null,
        origin: originOf(meta ?? null, "CHAT"),
        agentId: null,
        status: "IDLE",
        waitingOnYou: false,
        ...rest,
    };
}

/** `n` runs of one automation, the latest `ago` ago. */
function runs(prefix: string, meta: Record<string, unknown>, title: string, n: number, ago: number, now: number, running = false, every = 3 * HOUR): ConversationRef[] {
    return Array.from({ length: n }, (_, index) => chat(`${prefix}-${index}`, title, ago + index * every, now, {
        meta: meta.workflow_run_id ? { ...meta, workflow_run_id: `${String(meta.workflow_run_id)}-${index}` } : meta,
        kind: "TASK",
        status: index === 0 && running ? "RUNNING" : "IDLE",
    }));
}

function channel(id: string, kind: Channel["kind"], title: string | null, ago: number, now: number, extra: Partial<Channel> = {}): Channel {
    return {
        id,
        kind,
        title,
        topic: null,
        person: null,
        sources: [],
        assignee: null,
        agentsAnswer: true,
        status: null,
        attention: null,
        archived: false,
        lastLine: { sender: { kind: "MEMBER", id: "sam", name: "Sam" }, text: "On it.", createdAt: iso(ago, now) },
        lastMessageAt: iso(ago, now),
        createdAt: iso(30 * DAY, now),
        unread: { lines: 0, mentions: 0, waiting: 0 },
        ...extra,
    };
}

function ask(conversation: string, title: string, kind: string, summary: string, ago: number, now: number): PendingAsk {
    return { conversation_id: conversation, conversation_title: title, kind, summary, created_at: iso(ago, now), tool_call_id: "call-" + conversation };
}

function contact(id: string, name: string, reason: string, ago: number, now: number): Channel {
    return channel(id, "CONTACT", name, ago, now, {
        person: { kind: "CONTACT", id: id + "-person", name },
        status: "open",
        attention: { since: iso(ago, now), reason },
        agentsAnswer: false,
    });
}

const SCHEDULE = (name: string) => ({ source: "SCHEDULE", schedule_name: name });
const WORKFLOW = (run: string) => ({ source: "WORKFLOW_RUN", workflow_run_id: run });

export function samplePulse(now: number): LemmaPulse[] {
    return [
        {
            lemma: SUPPORT,
            conversations: [
                chat("support-refund", "Refund for a broken order", 12 * MIN, now, { waitingOnYou: true, status: "WAITING" }),
                ...runs("support-chase", WORKFLOW("run-chase"), "Workflow run: chase_unanswered_tickets", 12, 4 * MIN, now, true, 45 * MIN),
                ...runs("support-triage", WORKFLOW("run-triage"), "Workflow run: triage_new_tickets", 28, 10 * MIN, now, false, 20 * MIN),
                ...runs("support-digest", SCHEDULE("morning-digest"), "Morning digest", 1, 9 * HOUR, now),
                ...runs("support-sweep", SCHEDULE("stale-ticket-sweep"), "Stale ticket sweep", 1, 26 * HOUR, now),
                chat("support-tone", "Tone for the apology emails", 3 * DAY, now),
            ],
            asks: [ask("support-refund", "Refund for a broken order", "APPROVAL", "Refund $240 to Northwind? Their order arrived broken.", 12 * MIN, now),
                ask("support-credit", "Store credit for Globex", "APPROVAL", "Approve $60 store credit for Globex", 7 * HOUR, now)],
            waits: [],
            channels: [channel("support-line", "DIRECT", null, 3 * MIN, now, { sources: [{ platform: "WHATSAPP", title: null }], unread: { lines: 2, mentions: 0, waiting: 0 } })],
            contacts: [contact("support-maria", "Maria Chen", "About delivery dates", 3 * HOUR, now), contact("support-omar", "Omar Haddad", "Wants a person", 9 * HOUR, now), contact("support-li", "Li Wei", "Asked about invoices", 30 * HOUR, now)],
            tables: [{ name: "tickets", updated: iso(2 * HOUR, now) }, { name: "customers", updated: iso(3 * DAY, now) }],
        },
        {
            lemma: LAUNCH,
            conversations: [
                chat("launch-copy", "Landing copy, second pass", 6 * MIN, now, { status: "RUNNING" }),
                chat("launch-day", "Monday launch", 2 * HOUR, now, { waitingOnYou: true, status: "WAITING" }),
                chat("launch-press", "Press list follow-ups", 2 * DAY, now),
            ],
            asks: [ask("launch-day", "Monday launch", "QUESTION", "Which day should the launch post go out?", 2 * HOUR, now),
                ask("launch-hero", "Landing hero", "QUESTION", "Blue or green for the landing hero?", 2 * DAY + 2 * HOUR, now)],
            waits: [{ id: "wait-launch-form", type: "FORM", question: "Fill in the partner quote form", createdAt: iso(26 * HOUR, now), runId: "run-quotes", workflowName: "Partner quotes" }],
            channels: [channel("launch-week", "GROUP", "launch-week", 5 * MIN, now, { unread: { lines: 3, mentions: 0, waiting: 0 }, lastLine: { sender: { kind: "MEMBER", id: "dev", name: "Dev" }, text: "Screens are in the folder.", createdAt: iso(5 * MIN, now) } })],
            contacts: [],
            tables: [{ name: "launch_checklist", updated: iso(2 * HOUR, now) }],
        },
        {
            lemma: RENEWALS,
            conversations: [
                chat("renewals-globex", "Globex renewal", 1 * DAY, now, { waitingOnYou: true, status: "WAITING", meta: { source: "agent_surfaces", surface_platform: "RESEND", conversation_kind: "EMAIL" } }),
                ...runs("renewals-radar", SCHEDULE("renewal-radar"), "Renewal radar", 4, 4 * DAY, now),
            ],
            asks: [ask("renewals-globex", "Globex renewal", "QUESTION", "Sam hasn’t confirmed Globex. It renews in 3 days.", 1 * DAY, now)],
            waits: [],
            channels: [],
            contacts: [],
            tables: [],
        },
        {
            lemma: HIRING,
            conversations: [
                chat("hiring-lee", "Lee’s final round", 5 * HOUR, now, { waitingOnYou: true, status: "WAITING" }),
                chat("hiring-remote", "The second role", 3 * DAY, now, { waitingOnYou: true, status: "WAITING" }),
                chat("hiring-old", "Interview loop", 9 * DAY, now),
            ],
            asks: [ask("hiring-lee", "Lee’s final round", "QUESTION", "Pick a time for Lee’s final round", 5 * HOUR, now),
                ask("hiring-remote", "The second role", "QUESTION", "Remote or hybrid for the second role?", 3 * DAY, now)],
            waits: [],
            channels: [],
            contacts: [],
            tables: [],
        },
        {
            lemma: BOOKS,
            conversations: [chat("books-close", "September close", 8 * DAY, now)],
            asks: [ask("books-initech", "Supplier payments", "APPROVAL", "Approve paying Initech $1,180", 2 * DAY, now),
                ask("books-umbrella", "Supplier payments", "APPROVAL", "Approve paying Umbrella $420", 4 * DAY, now)],
            waits: [{ id: "wait-books-supplier", type: "FORM", question: "Fill in the supplier-onboarding form", createdAt: iso(1 * DAY + 3 * HOUR, now), runId: "run-supplier", workflowName: "Supplier onboarding" }],
            channels: [],
            contacts: [],
            tables: [{ name: "invoices", updated: iso(8 * DAY, now) }],
        },
    ];
}

/** Where each Lemma stands, as the canvas draws it. */
export const SAMPLE_OWNS: Record<string, Owns> = {
    [SUPPORT.id]: { sentence: "every customer hears back within a day", display: "31 of 44", label: "heard back this week", fill: 31 / 44, mark: 40 / 44, note: "Aiming for 40 by Friday. On track. 4 wait on Dev.", standing: "on" },
    [LAUNCH.id]: { sentence: "the October launch ships on the 28th", display: "18 days", label: "to go, 14 of 22 steps done", fill: 14 / 22, mark: null, note: "Due Oct 28. On pace.", standing: null },
    [RENEWALS.id]: { sentence: "every renewal is confirmed a week ahead", display: "9 of 12", label: "confirmed this month", fill: 9 / 12, mark: 11 / 12, note: "Aiming for 11. Behind on Globex.", standing: "behind" },
    [HIRING.id]: { sentence: "both open roles are filled by December", display: "0 of 2", label: "roles filled", fill: 0, mark: null, note: "Two candidates in final rounds.", standing: null },
    [BOOKS.id]: { sentence: "invoices are paid within 30 days", display: "88%", label: "paid on time", fill: 0.88, mark: 0.9, note: "Aiming for 90%.", standing: "on" },
};

const SAMPLE_APPS: AppItem[] = [
    { id: "app-queue", podId: SUPPORT.id, podName: SUPPORT.name, name: "ticket-queue", label: "Ticket queue", description: "Every open ticket, oldest first.", url: null, updatedAt: null },
    { id: "app-refund", podId: SUPPORT.id, podName: SUPPORT.name, name: "refund-desk", label: "Refund desk", description: "Refunds waiting and done.", url: null, updatedAt: null },
    { id: "app-board", podId: LAUNCH.id, podName: LAUNCH.name, name: "launch-board", label: "Launch board", description: null, url: null, updatedAt: null },
    { id: "app-press", podId: LAUNCH.id, podName: LAUNCH.name, name: "press-list", label: "Press list", description: null, url: null, updatedAt: null },
    { id: "app-radar", podId: RENEWALS.id, podName: RENEWALS.name, name: "renewal-radar", label: "Renewal radar", description: null, url: null, updatedAt: null },
    { id: "app-invoices", podId: BOOKS.id, podName: BOOKS.name, name: "invoices", label: "Invoices", description: null, url: null, updatedAt: null },
];

/** What the canvas's cards say, beyond the queue's one line. */
const CARDS: Record<string, Pick<NeedCard, "title" | "note" | "answer">> = {
    "ask:support-refund": { title: "Refund $240 to Northwind? Their order arrived broken.", note: "Then they hear back today: 32 of 44", answer: { kind: "approve", approve: "Approve", decline: "Not this one" } },
    "ask:launch-day": { title: "Which day should the launch post go out?", note: null, answer: { kind: "choose", questionId: "day", options: ["Mon 10", "Tue 11", "Wed 12"] } },
    "ask:renewals-globex": { title: "Sam hasn’t confirmed Globex. It renews in 3 days.", note: null, answer: { kind: "choose", questionId: "next", options: ["Nudge Sam", "I’ll take it"] } },
};

/** The Needs list's one-line titles, where they differ from a card's. */
export const SAMPLE_SHORT: Record<string, string> = {
    "ask:support-refund": "Approve a $240 refund to Northwind",
    "ask:renewals-globex": "Nudge Sam about Globex’s renewal",
    "contact:support-maria": "Reply to Maria Chen, about delivery dates",
};

/** The canvas's chat, as the server would store it, so the real transcript
 *  mapping (`thread/turns.ts`) draws it. */
export function sampleTranscript(conversationId: string, now: number): RawMessage[] {
    if (conversationId !== "support-refund") {
        return [
            { id: "m1", role: "user", kind: "TEXT", content: "Where are we on this?", sequence: 1, created_at: iso(2 * HOUR, now) },
            { id: "m2", role: "assistant", kind: "TEXT", content: "This is sample data, so nothing here is live. Sign in to talk to your own Lemmas.", sequence: 2, created_at: iso(2 * HOUR - MIN, now) },
        ];
    }
    const start = now - 15 * MIN;
    const at = (seconds: number) => new Date(start + seconds * 1000).toISOString();
    const step = (n: number, name: string, args: Record<string, unknown>, seconds: number): RawMessage[] => [
        { id: `t${n}`, role: "assistant", kind: "TOOL_CALL", tool_name: name, tool_args: args, tool_call_id: `c${n}`, sequence: 10 + n * 2, created_at: at(seconds) },
        { id: `r${n}`, role: "tool", kind: "TOOL_RETURN", tool_name: name, tool_result: { ok: true }, tool_call_id: `c${n}`, sequence: 11 + n * 2, created_at: at(seconds + 4) },
    ];
    return [
        { id: "u1", role: "user", kind: "TEXT", content: "Northwind says their order arrived broken. Photos attached. What do we do?", sequence: 1, created_at: at(0) },
        ...step(1, "read_file", { path: "/inbox/northwind-photos", comment: "Looking at the photos" }, 6),
        ...step(2, "query_table", { table: "orders", comment: "Finding order 4471" }, 14),
        ...step(3, "read_file", { path: "/policies/refunds.md", comment: "Checking the refund policy" }, 26),
        ...step(4, "update_record", { table: "tickets", comment: "Marking the ticket as a refund" }, 40),
        ...step(5, "draft_email", { to: "Maria Chen", comment: "Drafting the reply to Maria" }, 58),
        { id: "a1", role: "assistant", kind: "TEXT", content: "Their order had 4 lamps and one cracked in transit, which our policy covers. I drafted the reply to Maria and a refund for the broken lamp.", sequence: 30, created_at: at(66) },
        { id: "t6", role: "assistant", kind: "TOOL_CALL", tool_name: "request_approval", tool_args: { title: "Refund $240 to Northwind", reason: "Stripe · order 4471 · 1 of 4 items", tool_name: "stripe_refund", args: { amount: "$240", order: "4471" } }, tool_call_id: "call-support-refund", sequence: 31, created_at: at(70) },
        { id: "t7", role: "assistant", kind: "TOOL_CALL", tool_name: "display_resource", tool_args: { type: "TABLE", name: "tickets" }, tool_call_id: "c7", sequence: 32, created_at: at(72) },
        { id: "r7", role: "tool", kind: "TOOL_RETURN", tool_name: "display_resource", tool_result: { ok: true }, tool_call_id: "c7", sequence: 33, created_at: at(72) },
    ];
}

const LINES: Record<string, { title: string; kind: Channel["kind"]; lines: Omit<ChatLine, "at">[] }> = {
    "launch-week": {
        title: "launch-week",
        kind: "GROUP",
        lines: [
            { id: "l1", who: "Sam", whoKind: "MEMBER", mine: false, text: "Can we move the post to Tuesday?" },
            { id: "l2", who: "Launch", whoKind: "AGENT", mine: false, text: "Tuesday works. I asked Ada to pick the day, since it moves the press list too." },
            { id: "l3", who: "Dev", whoKind: "MEMBER", mine: false, text: "Screens are in the folder." },
        ],
    },
    "support-maria": {
        title: "Maria Chen",
        kind: "CONTACT",
        lines: [
            { id: "l1", who: "Maria Chen", whoKind: "CONTACT", mine: false, text: "Hi, when will the replacement lamp arrive?" },
            { id: "l2", who: "Support", whoKind: "AGENT", mine: false, text: "I’ve asked a person to confirm the delivery date for you." },
        ],
    },
    "support-line": {
        title: "WhatsApp",
        kind: "DIRECT",
        lines: [
            { id: "l1", who: "Support", whoKind: "AGENT", mine: false, text: "Northwind’s refund needs your yes." },
            { id: "l2", who: "Support", whoKind: "AGENT", mine: false, text: "9 customers heard back overnight." },
        ],
    },
};

export function sampleSource(): LemmaSource {
    const answered = new Set<string>();
    return {
        label: "sample",
        me: async () => ({ id: "me", email: "ada@acme.example", firstName: "Ada", name: "Ada Lovelace" }),
        orgs: async () => [{ id: "sample-acme", name: "Acme" }],
        lemmas: async () => SAMPLE_LEMMAS,
        pulse: async () => {
            const now = Date.now();
            return samplePulse(now).map((pulse) => ({
                ...pulse,
                asks: pulse.asks.filter((one) => !answered.has("ask:" + one.conversation_id)),
                conversations: pulse.conversations.map((one) => (answered.has("ask:" + one.id) ? { ...one, waitingOnYou: false } : one)),
            }));
        },
        owns: async (lemma) => SAMPLE_OWNS[lemma.id] ?? { sentence: null, display: null, label: null, fill: null, mark: null, note: null, standing: null },
        apps: async () => SAMPLE_APPS,
        card: async (item: NeedItem): Promise<NeedCard> => {
            const said = CARDS[item.key];
            if (said) return { ...item, ...said };
            if (item.kind === "APPROVAL") return { ...item, note: item.detail, answer: { kind: "approve", approve: "Approve", decline: "Not this one" } };
            return { ...item, note: item.detail, answer: { kind: "open", label: item.kind === "CONTACT" ? "Reply" : item.kind === "FORM" ? "Open the form" : "Answer" } };
        },
        answer: async (item) => {
            await new Promise((resolve) => setTimeout(resolve, 350));
            answered.add(item.key);
        },
        runs: async (lemma) => ({
            apps: SAMPLE_APPS.filter((app) => app.podId === lemma.id),
            tables: lemma.id === SUPPORT.id ? [{ name: "tickets", label: "Tickets" }, { name: "customers", label: "Customers" }] : [],
            automations: lemma.id === SUPPORT.id ? [{ name: "chase_unanswered_tickets", label: "Chase unanswered tickets" }] : [],
        }),
        starters: async (lemma): Promise<Starter[]> => [
            { platform: "GROUP", label: "Group chat", ready: true, note: `${lemma.name} answers when named` },
            { platform: "WHATSAPP", label: "WhatsApp group", ready: true, note: null },
            { platform: "SLACK", label: "Slack channel", ready: true, note: null },
            { platform: "TELEGRAM", label: "Telegram group", ready: false, note: null },
        ],
        lines: async (_podId, channelId) => {
            const now = Date.now();
            const held = LINES[channelId] ?? { title: "Chat", kind: "GROUP" as const, lines: [] };
            return { ...held, lines: held.lines.map((line, index) => ({ ...line, at: now - (held.lines.length - index) * 7 * MIN })) };
        },
        say: async (_podId, channelId, text) => {
            const held = LINES[channelId];
            if (held) held.lines.push({ id: "l" + (held.lines.length + 1), who: "Ada", whoKind: "MEMBER", mine: true, text });
        },
        /* Last looked at ten last night. */
        homeSeen: async () => {
            const midnight = new Date();
            midnight.setHours(0, 0, 0, 0);
            return midnight.getTime() - 2 * HOUR;
        },
        markHomeSeen: async () => undefined,
    };
}
