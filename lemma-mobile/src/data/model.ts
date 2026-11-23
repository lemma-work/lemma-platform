/** The pure parts of the `/o` frame, for a phone: what needs you, each
 *  Lemma's recent rows, how a section folds.
 *  Ported from `lemma-frontend/src/outcomes/model.ts` (PR #1042) with the
 *  web's address grammar swapped for this app's routes. Nothing here reaches
 *  the network, so it is tested directly (`tests/model.test.ts`). */

import { platformName, type ConversationOrigin, type OriginKind } from "./origin";
import { tablesRoutes } from "../areas/tables/routes";
import { workRoutes } from "../areas/work/routes";
import { NEW_CHAT, routes } from "./routes";
import type { Automation, Channel, ConversationRef, DayCount, NeedItem, NeedKind, SideRow } from "./types";

export { NEW_CHAT };

/* ── time ─────────────────────────────────────────────────────────────── */

export const DAY_MS = 86_400_000;

/** After a week a section folds itself away, and an ask stops counting. */
export const QUIET_AFTER_MS = 7 * DAY_MS;

export function timeOf(iso: string | null | undefined): number | null {
    const at = iso ? Date.parse(iso) : Number.NaN;
    return Number.isNaN(at) ? null : at;
}

/** "12m", "2h", "1d": the canvas's ages, short enough for the end of a row. */
export function ageOf(sinceMs: number, now: number): string {
    const minutes = Math.max(0, Math.round((now - sinceMs) / 60_000));
    if (minutes < 60) return `${Math.max(1, minutes)}m`;
    const hours = Math.round(minutes / 60);
    if (hours < 24) return `${hours}h`;
    return `${Math.round(hours / 24)}d`;
}

const WEEKDAY = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];

const MONTH = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

/** A row's time: "12m" or "2h" today, "Mon" this week, "Oct 14" before. */
export function stampOf(ms: number, now: number): string {
    const at = new Date(ms);
    const today = new Date(now);
    today.setHours(0, 0, 0, 0);
    if (ms >= today.getTime()) return ageOf(ms, now);
    if (today.getTime() - ms < 6 * DAY_MS) return WEEKDAY[at.getDay()];
    return `${MONTH[at.getMonth()]} ${at.getDate()}`;
}

export function clockOf(ms: number): string {
    const at = new Date(ms);
    return `${at.getHours()}:${String(at.getMinutes()).padStart(2, "0")}`;
}

/* ── names ────────────────────────────────────────────────────────────── */

const SHOUTED = new Set([
    "id", "api", "url", "uri", "ai", "ui", "ux", "sla", "seo", "oss", "arpa",
    "csat", "nps", "faq", "pdf", "csv", "html", "css", "sql", "http", "https",
    "uuid", "eta", "poc", "roi", "saas", "b2b", "b2c", "cta", "kpi", "okr",
]);

/** `content_ideas` as a person writes it: "Content Ideas". `library/reading.ts`. */
export function readableName(name: string): string {
    const words = String(name ?? "").split(/[_\-\s]+/).filter(Boolean);
    if (words.length === 0) return String(name ?? "");
    return words
        .map((word) => {
            const lower = word.toLowerCase();
            if (SHOUTED.has(lower)) return lower.toUpperCase();
            if (lower.length <= 4 && !/[aeiouy]/.test(lower) && /^[a-z]+$/.test(lower)) return lower.toUpperCase();
            return word[0].toUpperCase() + word.slice(1);
        })
        .join(" ");
}

/** The word before the first space: "Ada" of "Ada Lovelace". */
export function firstNameOf(name: string | null | undefined): string | null {
    const first = (name ?? "").trim().split(/\s+/)[0];
    return first || null;
}

/* ── conversations ────────────────────────────────────────────────────── */

const STARTED_BY_LEMMA = new Set<OriginKind>(["schedule", "workflow", "notification", "task", "ask"]);

export function startedByLemma(conversation: { origin: ConversationOrigin | null }): boolean {
    const kind = conversation.origin?.kind;
    return kind ? STARTED_BY_LEMMA.has(kind) : false;
}

/** Work since a moment, split by who started it. */
export function workSince(conversations: readonly ConversationRef[], sinceMs: number): { lemma: number; people: number } {
    let lemma = 0;
    let people = 0;
    for (const conversation of conversations) {
        const at = timeOf(conversation.activityAt);
        if (at === null || at < sinceMs) continue;
        if (startedByLemma(conversation)) lemma += 1;
        else people += 1;
    }
    return { lemma, people };
}

/** A day as the server's day counts name it, in the phone's own time zone. */
export function dayOf(ms: number): string {
    const at = new Date(ms);
    return `${at.getFullYear()}-${String(at.getMonth() + 1).padStart(2, "0")}-${String(at.getDate()).padStart(2, "0")}`;
}

/** Conversations no person started, from the day of `sinceMs` on. The
 *  server counts by day, so the day the reader last looked counts whole. */
export function ranOnTheirOwn(activity: readonly DayCount[], sinceMs: number): number {
    const from = dayOf(sinceMs);
    return activity.reduce((sum, day) => sum + (day.day >= from ? day.byAgent : 0), 0);
}

export function isRunning(conversation: { status: string | null }): boolean {
    return (conversation.status ?? "").toUpperCase() === "RUNNING";
}

const CHAT_ORIGINS = new Set<OriginKind>(["chat", "channel"]);

/** Whether a conversation is a chat: one a person or a platform started.
 *  A run a schedule, a workflow or another Lemma started is the Lemma's own
 *  work, read as an automation; still listed while it waits on you. */
export function isChat(conversation: Pick<ConversationRef, "origin" | "waitingOnYou" | "boundTo">): boolean {
    if (conversation.waitingOnYou) return true;
    if (conversation.boundTo) return false;
    return CHAT_ORIGINS.has(conversation.origin?.kind ?? "chat");
}

/* ── channels ─────────────────────────────────────────────────────────── */

function sayList(names: readonly string[]): string {
    if (names.length <= 1) return names[0] ?? "";
    return names.slice(0, -1).join(", ") + " and " + names[names.length - 1];
}

/** A channel's name. A person's own line is named by where it comes from,
 *  "WhatsApp", or "Notices" when nothing feeds it but what the Lemma sends. */
export function channelTitle(channel: Pick<Channel, "kind" | "title" | "person" | "sources">): string {
    if (channel.kind === "DIRECT") {
        const names = [...new Set(channel.sources.map((source) => platformName(source.platform)))];
        return names.length ? sayList(names) : "Notices";
    }
    if (channel.kind === "CONTACT") return channel.person?.name ?? channel.title ?? "A contact";
    return (channel.title ?? "A group").replace(/^#/, "");
}

export function platformsOf(channel: Pick<Channel, "sources">): string[] {
    return [...new Set(channel.sources.map((source) => source.platform.toUpperCase()))];
}

/** Whether a channel is a chat in the lists: not a contact's (those wait in
 *  Needs you and on the Lemma's Contacts), and a person's line only once
 *  something is in it. */
export function listedAsChat(channel: Channel): boolean {
    if (channel.kind === "CONTACT" || channel.archived) return false;
    if (channel.kind === "DIRECT") return channel.sources.length > 0 || channel.lastLine !== null;
    return true;
}

/** A contact's channel that needs a person: open, nobody on it, and its
 *  Lemma is off for them or asked for one. The server's rule. */
export function needsPerson(channel: Pick<Channel, "kind" | "assignee" | "status" | "agentsAnswer" | "attention">): boolean {
    return channel.kind === "CONTACT" && !channel.assignee && channel.status !== "closed" && (Boolean(channel.attention) || !channel.agentsAnswer);
}

/* ── the queue ────────────────────────────────────────────────────────── */

/** One pause as `conversations.pendingInteractions` lists it. */
export interface PendingAsk {
    conversation_id: string;
    conversation_title?: string | null;
    kind?: string | null;
    summary?: string | null;
    created_at?: string | null;
    tool_call_id?: string | null;
}

/** A workflow wait assigned to the reader (`workflows.runs.waitingAssignedToMe`). */
export interface WaitRow {
    id: string;
    type: "FORM" | "DECISION";
    question: string | null;
    createdAt: string | null;
    runId: string;
    workflowName: string;
}

const ASK_KINDS: Record<string, NeedKind> = { QUESTION: "QUESTION", APPROVAL: "APPROVAL", PERMISSION: "PERMISSION" };

/** Everything waiting on the reader in one Lemma, oldest first, one item per
 *  conversation aged by its oldest pause (as #1045's server does). */
export function needItems(
    lemma: { id: string; name: string },
    asks: readonly PendingAsk[],
    waits: readonly WaitRow[],
    contacts: readonly Channel[],
    now: number,
): NeedItem[] {
    const items = new Map<string, NeedItem>();
    for (const ask of asks) {
        const key = "ask:" + ask.conversation_id;
        if (items.has(key)) continue;
        const summary = (ask.summary ?? "").trim() || null;
        const title = (ask.conversation_title ?? "").trim() || "A chat";
        items.set(key, {
            key,
            kind: ASK_KINDS[(ask.kind ?? "").toUpperCase()] ?? "APPROVAL",
            podId: lemma.id,
            podName: lemma.name,
            title: summary ?? title,
            detail: summary ? title : null,
            sinceMs: timeOf(ask.created_at) ?? now,
            conversationId: ask.conversation_id,
            toolCallId: ask.tool_call_id ?? null,
            workflowRunId: null,
            channelId: null,
            messageId: null,
            choices: [],
        });
    }
    for (const wait of waits) {
        items.set("wait:" + wait.id, {
            key: "wait:" + wait.id,
            kind: wait.type,
            podId: lemma.id,
            podName: lemma.name,
            title: wait.question ?? `Fill in the ${wait.workflowName} form`,
            detail: wait.workflowName,
            sinceMs: timeOf(wait.createdAt) ?? now,
            conversationId: null,
            toolCallId: null,
            workflowRunId: wait.runId,
            channelId: null,
            messageId: null,
            choices: [],
        });
    }
    for (const channel of contacts) {
        if (!needsPerson(channel)) continue;
        items.set("contact:" + channel.id, {
            key: "contact:" + channel.id,
            kind: "CONTACT",
            podId: lemma.id,
            podName: lemma.name,
            title: `Reply to ${channelTitle(channel)}`,
            detail: channel.attention?.reason ?? "Waiting on a person",
            sinceMs: timeOf(channel.attention?.since ?? channel.lastMessageAt ?? channel.createdAt) ?? now,
            conversationId: null,
            toolCallId: null,
            workflowRunId: null,
            channelId: channel.id,
            messageId: null,
            choices: [],
        });
    }
    return [...items.values()];
}

/** Oldest first, and only what is still worth a badge: an ask left for a
 *  week is one nobody is going to answer. */
export function freshQueue(items: readonly NeedItem[], now: number): NeedItem[] {
    return items.filter((item) => now - item.sinceMs <= QUIET_AFTER_MS).sort((a, b) => a.sinceMs - b.sinceMs);
}

/** What a person does with what waits on them, as the web's Needs you
 *  sorts it (`queueFromNeeds`): decide (a yes or no), answer (a question, or
 *  somebody waiting on a person), do (a form to fill in). */
export type NeedFilter = "all" | "decide" | "answer" | "do";

export function filterOf(kind: NeedKind): Exclude<NeedFilter, "all"> {
    if (kind === "QUESTION" || kind === "MESSAGE" || kind === "CONTACT") return "answer";
    if (kind === "FORM") return "do";
    return "decide";
}

export const FILTER_LABELS: Record<NeedFilter, string> = {
    all: "All", decide: "Decide", answer: "Answer", do: "Do",
};

/** Under each group's name, what it holds (the web's Needs you). */
export const FILTER_LINES: Record<Exclude<NeedFilter, "all">, string> = {
    decide: "yes or no, from you",
    answer: "questions, and people waiting on a person",
    do: "forms to fill in",
};

/** The Needs you list, grouped Decide, Answer, Do, each oldest first. */
export function needGroups(items: readonly NeedItem[]): Record<Exclude<NeedFilter, "all">, NeedItem[]> {
    const oldest = [...items].sort((a, b) => a.sinceMs - b.sinceMs);
    return {
        decide: oldest.filter((item) => filterOf(item.kind) === "decide"),
        answer: oldest.filter((item) => filterOf(item.kind) === "answer"),
        do: oldest.filter((item) => filterOf(item.kind) === "do"),
    };
}

/** A few for a page that shows only some: the newest first. */
export function homeCards(items: readonly NeedItem[], shown = 3): NeedItem[] {
    return [...items].sort((a, b) => b.sinceMs - a.sinceMs).slice(0, shown);
}

/** Where an item opens: its chat, its contact's chat, or its run's form. */
export function hrefOfNeed(item: Pick<NeedItem, "podId" | "conversationId" | "channelId" | "workflowRunId" | "title">): string {
    if (item.channelId) return routes.shared(item.podId, item.channelId);
    if (item.conversationId) return routes.chat(item.podId, item.conversationId);
    if (item.workflowRunId) return workRoutes.run(item.podId, item.workflowRunId);
    return routes.lemma(item.podId);
}

/* ── a Lemma's rows ───────────────────────────────────────────────────── */

function newestFirst(a: SideRow, b: SideRow): number {
    if (a.at !== null && b.at !== null) return b.at - a.at || a.title.localeCompare(b.title);
    if (a.at !== b.at) return a.at === null ? 1 : -1;
    return a.title.localeCompare(b.title);
}

/** A Lemma's chats as rows: its conversations that are chats, and its
 *  channels -- a person's line, groups -- listed as chats. */
export function chatRows(podId: string, conversations: readonly ConversationRef[], channels: readonly Channel[]): SideRow[] {
    const rows: SideRow[] = [];
    for (const channel of channels) {
        if (!listedAsChat(channel)) continue;
        const line = channel.kind === "DIRECT";
        rows.push({
            key: `g:${podId}:${channel.id}`,
            kind: line ? "line" : "shared",
            title: channelTitle(channel),
            said: channel.lastLine?.text ?? channel.topic,
            at: timeOf(channel.lastMessageAt ?? channel.createdAt),
            href: routes.shared(podId, channel.id),
            needsYou: channel.unread.waiting > 0,
            unread: channel.unread.lines,
            running: false,
            platforms: platformsOf(channel),
        });
    }
    for (const conversation of conversations) {
        if (!isChat(conversation)) continue;
        const origin = conversation.origin;
        rows.push({
            key: `c:${podId}:${conversation.id}`,
            kind: "chat",
            title: conversation.title,
            said: origin && origin.kind !== "chat" ? origin.label : null,
            at: timeOf(conversation.activityAt),
            href: routes.chat(podId, conversation.id),
            needsYou: conversation.waitingOnYou,
            unread: 0,
            running: isRunning(conversation),
            platforms: origin?.platform ? [origin.platform.toUpperCase()] : [],
        });
    }
    return rows;
}

const SCHEDULE_LABEL = /^Schedule\s*·\s*/;
const WORKFLOW_TITLE = /^Workflow run:\s*/i;

function automationOf(conversation: ConversationRef): { key: string; name: string } | null {
    const origin = conversation.origin;
    if (origin?.kind === "schedule") {
        const name = origin.label.replace(SCHEDULE_LABEL, "").trim() || conversation.title;
        return { key: "s:" + name.toLowerCase(), name };
    }
    if (origin?.kind === "workflow") {
        const said = conversation.title.replace(WORKFLOW_TITLE, "").trim() || "Workflow run";
        const name = WORKFLOW_TITLE.test(conversation.title) ? readableName(said) : said;
        return { key: "w:" + name.toLowerCase(), name };
    }
    return null;
}

/** A Lemma's own recent work: one row per automation -- its latest run, and
 *  how many it ran lately -- and the tables changed within the week. Runs
 *  waiting on the reader are chats, not here. */
export function workRows(input: {
    podId: string;
    conversations: readonly ConversationRef[];
    /** The server's one line per automation; without it, they are read off
     *  the conversations. */
    automations?: readonly Automation[] | null;
    tables: readonly { name: string; updated: string | null }[];
    now: number;
}): SideRow[] {
    const latest = new Map<string, { row: SideRow; runs: Set<string> }>();
    for (const conversation of input.automations ? [] : input.conversations) {
        const origin = conversation.origin;
        if (!origin || conversation.waitingOnYou) continue;
        const automation = automationOf(conversation);
        if (!automation) continue;
        const run = origin.runId ?? conversation.id;
        const row: SideRow = {
            key: "auto:" + automation.key,
            kind: "automation",
            title: automation.name,
            said: origin.kind === "schedule" ? "Runs on a schedule" : "A workflow",
            at: timeOf(conversation.activityAt),
            href: origin.runId ? workRoutes.run(input.podId, origin.runId) : routes.chat(input.podId, conversation.id),
            needsYou: false,
            unread: 0,
            running: isRunning(conversation),
            platforms: [],
            runs: 1,
        };
        const held = latest.get(row.key);
        if (!held) {
            latest.set(row.key, { row, runs: new Set([run]) });
            continue;
        }
        held.runs.add(run);
        if ((row.at ?? 0) > (held.row.at ?? 0)) held.row = { ...row, running: row.running || held.row.running };
        else if (row.running) held.row = { ...held.row, running: true };
    }
    const automations = input.automations
        ? input.automations.map((automation) => automationRow(input.podId, automation))
        : [...latest.values()].map(({ row, runs }) => ({ ...row, runs: runs.size }));
    const tables = input.tables.flatMap((table): SideRow[] => {
        const at = timeOf(table.updated);
        if (at === null || input.now - at > QUIET_AFTER_MS) return [];
        const title = readableName(table.name);
        return [{
            key: "table:" + table.name,
            kind: "table",
            title,
            said: "A table",
            at,
            href: tablesRoutes.table(input.podId, table.name),
            needsYou: false,
            unread: 0,
            running: false,
            platforms: [],
        }];
    });
    return [...automations, ...tables];
}

/** One automation's row, from the server's line for it. */
export function automationRow(podId: string, automation: Automation): SideRow {
    const schedule = automation.kind === "SCHEDULE";
    const title = automation.name ? readableName(automation.name) : schedule ? "A schedule" : "A workflow";
    const href = automation.workflowRunId
        ? workRoutes.run(podId, automation.workflowRunId)
        : automation.conversationId
            ? routes.chat(podId, automation.conversationId)
            : schedule ? workRoutes.schedule(podId, automation.id) : workRoutes.runs(podId);
    return {
        key: "auto:" + automation.id,
        kind: "automation",
        title,
        said: schedule ? "Runs on a schedule" : "A workflow",
        at: timeOf(automation.latestAt),
        href,
        needsYou: false,
        unread: 0,
        running: automation.running,
        platforms: [],
        runs: automation.runs,
    };
}

/** Whether a chat earns a line: a person's own line is a way in, not a
 *  chat, so it is listed only while it has something new. */
export function inSidebar(row: Pick<SideRow, "kind" | "unread" | "needsYou">): boolean {
    return row.kind !== "line" || row.unread > 0 || row.needsYou;
}

/** A section's rows: what waits on the reader first, then the rest, newest
 *  first. */
export function sectionRows(chats: readonly SideRow[], work: readonly SideRow[]): SideRow[] {
    const rows = [...chats.filter(inSidebar), ...work];
    return [...rows.filter((row) => row.needsYou).sort(newestFirst), ...rows.filter((row) => !row.needsYou).sort(newestFirst)];
}

/** What a section shows before "N more": the first `limit` rows, plus
 *  anything waiting on the reader wherever it falls. Order is kept. */
export function foldSection<T extends { needsYou: boolean }>(rows: readonly T[], limit: number): { shown: T[]; hidden: number } {
    const shown = rows.filter((row, index) => index < limit || row.needsYou);
    return { shown, hidden: rows.length - shown.length };
}

/** The sections, busiest first; each open unless quiet for a week, and
 *  always open when something in it waits on the reader. What the reader
 *  folded or opened themselves wins. */
export function sidebarSections<T extends { id: string; name: string }, R extends { at: number | null; needsYou: boolean }>(
    sections: readonly { lemma: T; rows: readonly R[] }[],
    now: number,
    chosen: ReadonlyMap<string, boolean>,
): { lemma: T; rows: R[]; latest: number | null; waiting: number; open: boolean }[] {
    return sections
        .map(({ lemma, rows }) => {
            const times = rows.map((row) => row.at).filter((at): at is number => at !== null);
            const latest = times.length > 0 ? Math.max(...times) : null;
            const waiting = rows.filter((row) => row.needsYou).length;
            const lively = waiting > 0 || (latest !== null && now - latest < QUIET_AFTER_MS);
            return { lemma, rows: [...rows], latest, waiting, open: chosen.get(lemma.id) ?? lively };
        })
        .sort((a, b) => {
            if (a.open !== b.open) return a.open ? -1 : 1;
            if (a.latest !== null && b.latest !== null && a.latest !== b.latest) return b.latest - a.latest;
            if ((a.latest === null) !== (b.latest === null)) return a.latest === null ? 1 : -1;
            return a.lemma.name.localeCompare(b.lemma.name);
        });
}
