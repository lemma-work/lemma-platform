/** The pure parts of the `/o` frame, for a phone: what needs you, each
 *  Lemma's recent rows, how a section folds, the plan file and its number.
 *  Ported from `lemma-frontend/src/outcomes/model.ts` (PR #1042) with the
 *  web's address grammar swapped for this app's routes. Nothing here reaches
 *  the network, so it is tested directly (`tests/model.test.ts`). */

import { CORE_SCHEMA, load } from "js-yaml";
import { platformName, type ConversationOrigin, type OriginKind } from "./origin";
import { tablesRoutes } from "../areas/tables/routes";
import { workRoutes } from "../areas/work/routes";
import { NEW_CHAT, routes } from "./routes";
import type { Channel, ConversationRef, NeedItem, NeedKind, Owns, SideRow } from "./types";

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
        });
    }
    return [...items.values()];
}

/** Oldest first, and only what is still worth a badge: an ask left for a
 *  week is one nobody is going to answer. */
export function freshQueue(items: readonly NeedItem[], now: number): NeedItem[] {
    return items.filter((item) => now - item.sinceMs <= QUIET_AFTER_MS).sort((a, b) => a.sinceMs - b.sinceMs);
}

export type NeedFilter = "all" | "yes" | "questions" | "people" | "forms";

export function filterOf(kind: NeedKind): Exclude<NeedFilter, "all"> {
    if (kind === "QUESTION") return "questions";
    if (kind === "CONTACT") return "people";
    if (kind === "FORM") return "forms";
    return "yes";
}

export const FILTER_LABELS: Record<NeedFilter, string> = {
    all: "All", yes: "Your yes", questions: "Questions", people: "People", forms: "Forms",
};

/** The Needs you list: what is behind first, then today, then earlier, each
 *  newest first. Behind is anything from a Lemma behind its bar. */
export function needGroups(items: readonly NeedItem[], behind: ReadonlySet<string>, now: number): { behind: NeedItem[]; today: NeedItem[]; earlier: NeedItem[] } {
    const midnight = new Date(now);
    midnight.setHours(0, 0, 0, 0);
    const newest = [...items].sort((a, b) => b.sinceMs - a.sinceMs);
    return {
        behind: newest.filter((item) => behind.has(item.podId)),
        today: newest.filter((item) => !behind.has(item.podId) && item.sinceMs >= midnight.getTime()),
        earlier: newest.filter((item) => !behind.has(item.podId) && item.sinceMs < midnight.getTime()),
    };
}

/** Home's few cards: anything from a Lemma that is behind makes the cut
 *  first, the rest are the newest; shown newest first. */
export function homeCards(items: readonly NeedItem[], behind: ReadonlySet<string>, shown = 3): NeedItem[] {
    const newest = [...items].sort((a, b) => b.sinceMs - a.sinceMs);
    const late = newest.filter((item) => behind.has(item.podId)).slice(0, 1);
    const rest = newest.filter((item) => !late.includes(item)).slice(0, Math.max(0, shown - late.length));
    return [...late, ...rest].sort((a, b) => b.sinceMs - a.sinceMs);
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
    tables: readonly { name: string; updated: string | null }[];
    now: number;
}): SideRow[] {
    const latest = new Map<string, { row: SideRow; runs: Set<string> }>();
    for (const conversation of input.conversations) {
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
    const automations = [...latest.values()].map(({ row, runs }) => ({ ...row, runs: runs.size }));
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

/* ── the plan ─────────────────────────────────────────────────────────── */

export const PLAN_PATH = "/plan.md";

export interface Plan {
    outcome: string;
    kind: "week" | "date" | null;
    target: string | null;
    due: string | null;
    measure: string | null;
    unit: string | null;
}

function words(value: unknown): string | null {
    if (typeof value === "number") return String(value);
    return typeof value === "string" && value.trim() ? value.trim() : null;
}

/** Reads `/plan.md` the way `/o` does: front matter when it parses, else the
 *  first line under "What must be true". Forgiving on purpose. */
export function parsePlan(source: string): Plan {
    const plan: Plan = { outcome: "", kind: null, target: null, due: null, measure: null, unit: null };
    let body = source.replace(/\r\n/g, "\n");
    if (body.startsWith("---\n")) {
        const end = body.indexOf("\n---", 4);
        if (end > 0) {
            const front = body.slice(4, end);
            body = body.slice(end + 4);
            let meta: Record<string, unknown> = {};
            try {
                /* The core schema keeps an unquoted `due: 2026-10-30` the string
                   it was written as; the default one reads a Date, and the plan
                   would lose it (as `/o` found). */
                const loaded = load(front, { schema: CORE_SCHEMA });
                if (loaded && typeof loaded === "object" && !Array.isArray(loaded)) meta = loaded as Record<string, unknown>;
            } catch {
                meta = {};
            }
            plan.outcome = words(meta.outcome) ?? "";
            const kind = words(meta.kind);
            plan.kind = kind === "week" || kind === "date" ? kind : null;
            plan.target = words(meta.target);
            plan.due = words(meta.due);
            plan.measure = words(meta.measure);
            plan.unit = words(meta.unit);
        }
    }
    if (!plan.outcome) {
        let inMust = false;
        for (const raw of body.split("\n")) {
            const heading = /^#{2,3}\s+(.*)$/.exec(raw);
            if (heading) {
                const said = heading[1].toLowerCase();
                inMust = said.includes("must") || said.includes("true") || said.includes("outcome");
                continue;
            }
            const item = /^\s*[-*]\s+(.*)$/.exec(raw);
            if (inMust && item && item[1].trim()) {
                plan.outcome = item[1].trim().replace(/\s+(?:—|--)\s+[^,—]+,\s*[^,—]+$/, "");
                break;
            }
        }
    }
    return plan;
}

/** The number a measure answered: the first finite value in its first row. */
export function readMeasure(rows: readonly Record<string, unknown>[]): number | null {
    const first = rows[0];
    if (!first) return null;
    for (const value of Object.values(first)) {
        const number = typeof value === "number" ? value : typeof value === "string" && value.trim() ? Number(value) : Number.NaN;
        if (Number.isFinite(number)) return number;
    }
    return null;
}

export function formatMeasure(value: number, unit: string | null): string {
    const rounded = Math.abs(value) >= 10 ? Math.round(value) : Math.round(value * 10) / 10;
    return unit === "%" ? `${rounded}%` : String(rounded);
}

/** Holding its bar or behind: only a weekly bar with a target and a number.
 *  A finish line has no pace to compare against, so it says nothing. */
export function standingOf(plan: Plan | null, value: number | null): "on" | "behind" | null {
    if (!plan || value === null || !plan.target || plan.kind === "date") return null;
    const target = Number.parseFloat(plan.target);
    if (!Number.isFinite(target)) return null;
    return value >= target ? "on" : "behind";
}

export const NOTHING_OWNED = "Nothing it owns yet.";

/** What a Lemma's page and its row in Lemmas say about what it owns. */
export function ownsOf(plan: Plan | null, value: number | null, now: number): Owns {
    if (!plan || !plan.outcome) return { sentence: null, display: null, label: null, fill: null, mark: null, note: null, standing: null };
    const sentence = plan.outcome.replace(/\.$/, "");
    const standing = standingOf(plan, value);
    if (value === null) {
        return { sentence: lowerFirst(sentence), display: null, label: null, fill: null, mark: null, note: plan.measure ? "No number yet." : null, standing: null };
    }
    const target = plan.target ? Number.parseFloat(plan.target) : Number.NaN;
    const scale = plan.unit === "%" ? 100 : Number.isFinite(target) ? Math.max(target, value) * 1.1 : null;
    const due = plan.kind === "date" && plan.due ? dueIn(plan.due, now) : null;
    const pieces = [
        plan.target ? `Aiming for ${plan.target}${due ? `, ${due}` : ""}.` : null,
        standing === "on" ? "On track." : standing === "behind" ? "Behind." : null,
    ].filter(Boolean);
    return {
        sentence: lowerFirst(sentence),
        display: formatMeasure(value, plan.unit),
        label: plan.kind === "week" ? "this week" : null,
        fill: scale ? Math.max(0, Math.min(1, value / scale)) : null,
        mark: scale && Number.isFinite(target) ? Math.max(0, Math.min(1, target / scale)) : null,
        note: pieces.length ? pieces.join(" ") : null,
        standing,
    };
}

function lowerFirst(value: string): string {
    return /^[A-Z][a-z]/.test(value) ? value.charAt(0).toLowerCase() + value.slice(1) : value;
}

function dueIn(due: string, now: number): string | null {
    const bare = /^(\d{4})-(\d{2})-(\d{2})$/.exec(due);
    if (!bare) return null;
    const at = new Date(Number(bare[1]), Number(bare[2]) - 1, Number(bare[3])).getTime();
    const midnight = new Date(now);
    midnight.setHours(0, 0, 0, 0);
    const days = Math.round((at - midnight.getTime()) / DAY_MS);
    if (days < 0) return `${-days} days late`;
    if (days === 0) return "due today";
    return `${days} ${days === 1 ? "day" : "days"} left`;
}
