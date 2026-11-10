/** What the app reads, in its own words. Live, most of it is one read for
 *  the whole organization (`/organizations/{org}/needs-you`, `/recents`,
 *  `/apps`, `/home-seen`) and a Lemma's own `/outcome/standing`; `live.ts`
 *  turns those into these shapes, and the sample source builds the same. */

import type { ConversationOrigin } from "./origin";

export interface Me {
    id: string | null;
    email: string | null;
    /** "Ada", for "3 things need you, Ada." */
    firstName: string | null;
    name: string;
}

export interface Org {
    id: string;
    name: string;
}

/** A Lemma: a pod, as the phone shows it. */
export interface Lemma {
    id: string;
    name: string;
    iconUrl: string | null;
    description: string | null;
}

/** A conversation as the lists draw it (`conversationRefOf` on the web). */
export interface ConversationRef {
    id: string;
    title: string;
    /** The server's last-activity time, ISO. */
    activityAt: string | null;
    kind: string;
    boundTo: string | null;
    origin: ConversationOrigin;
    agentId: string | null;
    status: string | null;
    /** Paused on the reader. Only set from a conversation that has run:
     *  one never run reports WAITING too (see `live.ts`). */
    waitingOnYou: boolean;
}

/* ── a channel, the parts the lists read ─────────────────────────────── */

export type ChannelKind = "DIRECT" | "CONTACT" | "GROUP";

export interface PersonRef {
    kind: "MEMBER" | "CONTACT" | "AGENT";
    id: string;
    name: string | null;
}

export interface Channel {
    id: string;
    kind: ChannelKind;
    title: string | null;
    topic: string | null;
    person: PersonRef | null;
    sources: { platform: string; title: string | null }[];
    assignee: PersonRef | null;
    agentsAnswer: boolean;
    status: "open" | "waiting_on_contact" | "closed" | null;
    attention: { since: string; reason: string | null } | null;
    archived: boolean;
    lastLine: { sender: PersonRef; text: string; createdAt: string } | null;
    lastMessageAt: string | null;
    createdAt: string;
    unread: { lines: number; mentions: number; waiting: number };
}

/* ── what needs you ──────────────────────────────────────────────────── */

/** The server's `NeedsYouKind`. A MESSAGE is a question a Lemma asked in
 *  your own chat with it, answered with one of its choices or in words. */
export type NeedKind = "QUESTION" | "APPROVAL" | "PERMISSION" | "FORM" | "DECISION" | "CONTACT" | "MESSAGE";

export interface NeedItem {
    key: string;
    kind: NeedKind;
    podId: string;
    podName: string;
    /** The question, what it wants approved, or "Reply to Maria Chen". */
    title: string;
    detail: string | null;
    sinceMs: number;
    conversationId: string | null;
    /** The paused call: also the approval id the decision endpoint takes. */
    toolCallId: string | null;
    workflowRunId: string | null;
    /** A contact's chat, or the chat a MESSAGE was asked in. */
    channelId: string | null;
    /** A MESSAGE's own line, which its answer is sent to. */
    messageId: string | null;
    /** The answers a MESSAGE offered. */
    choices: string[];
}

/** What a card on Home offers, read off the paused call when it was opened:
 *  approve or decline, a few choices, or only "open it". */
export type CardAnswer =
    | { kind: "approve"; approve: string; decline: string }
    | { kind: "choose"; questionId: string; options: string[] }
    | { kind: "open"; label: string };

export interface NeedCard extends NeedItem {
    /** A line under the title: what saying yes does, or how long it has waited. */
    note: string | null;
    answer: CardAnswer;
}

/* ── each Lemma's recent things ──────────────────────────────────────── */

/** What a row in a Lemma's section is: a chat with it, someone's private
 *  line (WhatsApp, Notices), a chat shared with people, one of its
 *  automations, or a table it changed. */
export type RowKind = "chat" | "line" | "shared" | "automation" | "table";

export interface SideRow {
    key: string;
    kind: RowKind;
    title: string;
    /** More about it: the last thing said, where it came from. */
    said: string | null;
    at: number | null;
    /** Where it opens, an app route. */
    href: string;
    needsYou: boolean;
    unread: number;
    running: boolean;
    /** The platforms feeding it, for its glyph. */
    platforms: string[];
    /** An automation's runs lately, when more than one. */
    runs?: number;
}

/** What a Lemma owns and where it stands: its `/outcome/standing`, or for a
 *  Lemma that keeps none on the server yet, `/plan.md` and its measure. */
export interface Owns {
    /** "every customer hears back within a day", or null for nothing yet. */
    sentence: string | null;
    /** As the number reads: "31 of 44", "88%", "18 days". */
    display: string | null;
    /** Said after the number: "heard back this week". */
    label: string | null;
    /** Where the bar is filled to, 0..1, and where the target sits. */
    fill: number | null;
    mark: number | null;
    /** "Aiming for 40 by Friday. On track." */
    note: string | null;
    standing: "on" | "behind" | null;
}

/** One of a Lemma's workflows or schedules that ran lately: its newest run,
 *  and how many it ran in the window (`/organizations/{org}/recents`). */
export interface Automation {
    id: string;
    kind: "WORKFLOW" | "SCHEDULE";
    name: string | null;
    latestAt: string;
    running: boolean;
    runs: number;
    /** The run to open for the newest: the workflow's, or one a schedule started. */
    workflowRunId: string | null;
    /** The conversation a schedule's agent ran in, for the newest fire. */
    conversationId: string | null;
}

/** One day's conversations of the reader's, by who started them. */
export interface DayCount {
    /** A calendar day in the phone's time zone, `2026-11-10`. */
    day: string;
    byAgent: number;
    byPeople: number;
}

export interface LemmaSection {
    lemma: Lemma;
    rows: SideRow[];
    latest: number | null;
    waiting: number;
    /** Open unless it has been quiet for a week, or the reader folded it. */
    open: boolean;
    owns: Owns | null;
}

/** An app, as `/organizations/{org}/apps` lists it. */
export interface AppItem {
    id: string;
    podId: string;
    podName: string;
    name: string;
    label: string;
    description: string | null;
    url: string | null;
    updatedAt: string | null;
}

/** What a Lemma runs, for a new chat: its apps, tables and automations. */
export interface Runs {
    apps: AppItem[];
    tables: { name: string; label: string }[];
    automations: { name: string; label: string }[];
}

/** A way to start a chat with people (`groupStarters` on the web). */
export interface Starter {
    platform: "GROUP" | "WHATSAPP" | "SLACK" | "TELEGRAM";
    label: string;
    /** Ready, or one tap away from connecting. */
    ready: boolean;
    note: string | null;
}

export interface Home {
    me: Me;
    org: Org;
    orgs: Org[];
    lemmas: Lemma[];
    needs: NeedItem[];
    /** Runs a Lemma started on its own since the reader last looked. */
    startedOnTheirOwn: number;
    /** "since last night", "since you were last here", "in the last day". */
    since: string;
}
