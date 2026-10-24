/** The outcome frame's pure parts: its address grammar (`address.ts`,
 *  re-exported here), a day-by-day count of work, the plan file, and how the
 *  queue groups what is waiting on you.
 *  Nothing here reaches the network or the page, so all of it is tested
 *  directly (`tests/outcomes.test.ts`). */

import { CORE_SCHEMA, dump, load } from "js-yaml";
import type { ConversationRef } from "@/data/types";
import { listedAsChat } from "@/channels/chat-list";
import { channelTitle, platformsOf, previewOf, type Channel } from "@/channels/model";
import type { OriginKind } from "@/thread/conversation-origin";
import { readableName } from "@/library/reading";
import type { AskedRow } from "@/thread/waiting-on-you";
import type { WaitingRow } from "@/workflow/waiting-inbox";

export * from "./address";
import { chatView, outcomeView, writeView, type ChatOpen, type OpenChat, type View } from "./address";

/** Where something waiting on you opens, inside the frame: its conversation,
 *  or the workflow run whose form it is. */
export function viewOfItem(item: Pick<QueueItem, "podId" | "conversationId" | "runId" | "contactId">): View {
    if (item.contactId) return chatView({ kind: "contact", podId: item.podId, id: item.contactId });
    if (item.conversationId) return chatView({ kind: "chat", podId: item.podId, id: item.conversationId });
    if (item.runId) return { name: "run", podId: item.podId, runId: item.runId };
    return outcomeView(item.podId);
}

/** A workspace address (`/t/…`, read by the workspace's own `readAddress`)
 *  as a view of this frame, for the places the frame shows itself; null for
 *  the rest, which the workspace opens. The thread and the shared-chat page
 *  write their links for the workspace, and this is how following one stays
 *  here. */
export function viewOfAddress(address: { podId: string | null; tabId: string | null; conversationId: string | null; section?: string | null; agentName?: string | null }): View | null {
    const { podId, tabId } = address;
    if (!podId) return null;
    switch (tabId) {
        case null:
        case "space:home":
        case "space:chats":
        case "space:channels":
        case "history":
            return outcomeView(podId);
        case "space:about":
            return outcomeView(podId, "settings", address.section ?? null);
        case "profile":
            return address.agentName ? { name: "agent", podId, agent: address.agentName } : outcomeView(podId, "settings");
        case "conversation":
            return address.conversationId ? chatView({ kind: "chat", podId, id: address.conversationId }) : outcomeView(podId);
        case "space:contacts":
            return outcomeView(podId, "contacts");
        case "space:apps":
        case "apps":
            return outcomeView(podId, "apps");
        case "space:files":
        case "space:pages":
        case "space:tables":
        case "library":
            return outcomeView(podId, "made");
        case "space:workflows":
            return outcomeView(podId, "xray");
    }
    const colon = tabId.indexOf(":");
    const kind = colon < 0 ? tabId : tabId.slice(0, colon);
    const rest = colon < 0 ? "" : tabId.slice(colon + 1);
    if (!rest) return null;
    switch (kind) {
        case "channel": return chatView({ kind: "shared", podId, id: rest });
        case "run": return { name: "run", podId, runId: rest };
        case "app": return { name: "app", podId, app: rest };
        case "table": return { name: "table", podId, table: rest };
        case "workflow": return { name: "workflow", podId, workflow: rest };
        case "bot": return { name: "agent", podId, agent: rest };
        case "record": {
            const split = rest.indexOf(":");
            return split > 0 ? { name: "record", podId, table: rest.slice(0, split), recordId: rest.slice(split + 1) } : null;
        }
        case "file": return { name: "file", podId, path: rest };
        default: return null;
    }
}

/** Words handed to a new chat to send once its pane is up: what ⌘K asked.
 *  The pane that shows the conversation sends it, so the reply streams
 *  into the thread on screen. */
export interface Handoff {
    text: string;
    id: number;
    podId: string;
    sent: boolean;
    /** What the new conversation is made with: the agent it is with, when
     *  it is not the Lemma's own. */
    createWith?: Record<string, unknown>;
}

/** Words put in a chat's box -- what an app or a widget asked for, or ⌘K
 *  without a live session. Nothing sends them but the reader. */
export interface Fill {
    text: string;
    id: number;
    podId: string;
}

export interface GoOptions {
    /** Rewrite this history entry rather than add one: a new chat taking
     *  its id is the same place, not a step to go back through. */
    replace?: boolean;
    handoff?: Handoff;
    fill?: Fill;
}

export type Go = (view: View, options?: GoOptions) => void;

/* ── activity ─────────────────────────────────────────────────────────── */

export interface DayCount {
    /** The local day, `YYYY-MM-DD`. */
    day: string;
    /** Work nobody typed the first message of: schedules, workflow steps,
     *  background tasks, asks from another pod. */
    lemma: number;
    /** Work a person started: a chat, a message on a channel, a document. */
    people: number;
}

const STARTED_BY_LEMMA = new Set(["schedule", "workflow", "notification", "task", "ask"]);

export function startedByLemma(conversation: Pick<ConversationRef, "origin">): boolean {
    const kind = conversation.origin?.kind;
    return kind ? STARTED_BY_LEMMA.has(kind) : false;
}

function localDay(ms: number): string {
    const at = new Date(ms);
    const month = String(at.getMonth() + 1).padStart(2, "0");
    const day = String(at.getDate()).padStart(2, "0");
    return `${at.getFullYear()}-${month}-${day}`;
}

/** Midnight of `ms`'s local day. Built from the calendar, not by subtracting
 *  24 hours, so a daylight-saving change does not shift the buckets. */
function midnight(ms: number): number {
    const at = new Date(ms);
    at.setHours(0, 0, 0, 0);
    return at.getTime();
}

/** One bucket per local day for the last `days` days, oldest first, counting
 *  conversations by when they were last active. `activityAt` is the server's
 *  own time; `at` is a display stamp and is never parsed. */
export function dailyActivity(conversations: readonly ConversationRef[], now: Date, days = 14): DayCount[] {
    const buckets: DayCount[] = [];
    const index = new Map<string, DayCount>();
    const today = new Date(midnight(now.getTime()));
    for (let back = days - 1; back >= 0; back -= 1) {
        const day = new Date(today);
        day.setDate(today.getDate() - back);
        const bucket = { day: localDay(day.getTime()), lemma: 0, people: 0 };
        buckets.push(bucket);
        index.set(bucket.day, bucket);
    }
    for (const conversation of conversations) {
        const at = conversation.activityAt ? Date.parse(conversation.activityAt) : Number.NaN;
        if (Number.isNaN(at)) continue;
        const bucket = index.get(localDay(at));
        if (!bucket) continue;
        if (startedByLemma(conversation)) bucket.lemma += 1;
        else bucket.people += 1;
    }
    return buckets;
}

/** Work since a moment, split the same way as `dailyActivity`. */
export function workSince(conversations: readonly ConversationRef[], sinceMs: number): { lemma: number; people: number } {
    let lemma = 0;
    let people = 0;
    for (const conversation of conversations) {
        const at = conversation.activityAt ? Date.parse(conversation.activityAt) : Number.NaN;
        if (Number.isNaN(at) || at < sinceMs) continue;
        if (startedByLemma(conversation)) lemma += 1;
        else people += 1;
    }
    return { lemma, people };
}

export function isRunning(conversation: Pick<ConversationRef, "status">): boolean {
    return (conversation.status ?? "").toUpperCase() === "RUNNING";
}

/* ── the plan ─────────────────────────────────────────────────────────── */

/** Where an outcome's plan lives: a pod file people and Lemma both edit. */
export const PLAN_PATH = "/plan.md";

export type PlanSection = "must" | "how" | "who" | "never";

export const PLAN_SECTIONS: readonly PlanSection[] = ["must", "how", "who", "never"];

export const SECTION_TITLES: Record<PlanSection, string> = {
    must: "What must be true",
    how: "How we work",
    who: "Who holds what",
    never: "Never",
};

export interface PlanLine {
    text: string;
    /** Who added the line, as written after it. */
    by?: string;
    /** When, as written: "Oct 14". */
    on?: string;
}

export interface Plan {
    /** The sentence Lemma owns: "Every customer hears back within a day". */
    outcome: string;
    /** A bar held every week, or a finish line by a date. */
    kind: "week" | "date" | null;
    /** As written: "90%", "22 steps". */
    target: string | null;
    /** `YYYY-MM-DD`, for a finish line. */
    due: string | null;
    /** A read-only SQL query over this pod's tables that answers one number. */
    measure: string | null;
    /** `%` when the measure is a share. */
    unit: string | null;
    sections: Record<PlanSection, PlanLine[]>;
    /** The file's text as it was read, when it was: a save edits that text
     *  rather than replacing it, and refuses when the file has moved on. */
    file?: string;
}

export function emptyPlan(outcome = ""): Plan {
    return { outcome, kind: null, target: null, due: null, measure: null, unit: null, sections: { must: [], how: [], who: [], never: [] } };
}

function sectionOf(heading: string): PlanSection | null {
    const said = heading.toLowerCase();
    if (said.includes("never")) return "never";
    if (said.includes("who")) return "who";
    if (said.includes("how")) return "how";
    if (said.includes("must") || said.includes("true") || said.includes("outcome")) return "must";
    return null;
}

/** "Refunds over $200 need Dev — Priya, Oct 14" → the line and who/when. */
function lineOf(raw: string): PlanLine {
    const said = raw.trim();
    const match = /^(.*\S)\s+(?:—|--)\s+([^,—]+),\s*([^,—]+)$/.exec(said);
    if (!match) return { text: said };
    return { text: match[1].trim(), by: match[2].trim(), on: match[3].trim() };
}

function text(value: unknown): string | null {
    if (typeof value === "number") return String(value);
    return typeof value === "string" && value.trim() ? value.trim() : null;
}

const HEADING = /^#{2,3}\s+(.*)$/;
const ITEM = /^(\s*[-*]\s+)(.*)$/;
const META_KEYS = ["outcome", "kind", "target", "due", "unit", "measure"] as const;

/** A plan file cut at its front matter: the raw block with its fences, what
 *  is inside them, and the rest. A file with no closed block is all body. */
function splitFront(source: string): { head: string; front: string | null; body: string } {
    const all = source.replace(/\r\n/g, "\n");
    if (!all.startsWith("---\n")) return { head: "", front: null, body: all };
    const end = all.indexOf("\n---", 4);
    if (end <= 0) return { head: "", front: null, body: all };
    return { head: all.slice(0, end + 4), front: all.slice(4, end), body: all.slice(end + 4) };
}

/** The front matter as a map, or null when it will not parse. The core
 *  schema keeps an unquoted `due: 2026-10-30` the string it was written as,
 *  where the default one would read a Date and the plan would lose it. */
function loadFront(front: string): Record<string, unknown> | null {
    try {
        const loaded = load(front, { schema: CORE_SCHEMA });
        if (loaded === null || loaded === undefined) return {};
        return typeof loaded === "object" && !Array.isArray(loaded) ? (loaded as Record<string, unknown>) : null;
    } catch {
        return null;
    }
}

/** Reads a plan written by a person, by Lemma, or by this frame. Forgiving on
 *  purpose: an unknown heading is skipped, not fatal, and front matter that
 *  will not parse leaves the lines readable. */
export function parsePlan(source: string): Plan {
    const plan = emptyPlan();
    const { front, body } = splitFront(source);
    if (front !== null) {
        const meta = loadFront(front) ?? {};
        plan.outcome = text(meta.outcome) ?? "";
        const kind = text(meta.kind);
        plan.kind = kind === "week" || kind === "date" ? kind : null;
        plan.target = text(meta.target);
        plan.due = text(meta.due);
        plan.measure = text(meta.measure);
        plan.unit = text(meta.unit);
    }
    let current: PlanSection | null = null;
    for (const raw of body.split("\n")) {
        const heading = HEADING.exec(raw);
        if (heading) {
            current = sectionOf(heading[1]);
            continue;
        }
        const item = ITEM.exec(raw);
        if (item && current && item[2].trim()) plan.sections[current].push(lineOf(item[2]));
    }
    if (!plan.outcome && plan.sections.must[0]) plan.outcome = plan.sections.must[0].text;
    return plan;
}

const PREAMBLE = "<!-- The plan for what this Lemma owns. People and the Lemma both edit it: every line ends with who added it and when. The measure is one read-only SQL query that answers a single number. -->";

function metaOf(plan: Plan): Record<string, string> {
    const meta: Record<string, string> = {};
    for (const key of META_KEYS) {
        const value = plan[key];
        if (value) meta[key] = value;
    }
    return meta;
}

function written(line: PlanLine): string {
    const by = line.by && line.on ? ` — ${line.by}, ${line.on}` : "";
    return `${line.text}${by}`;
}

function sameLine(a: PlanLine, b: PlanLine): boolean {
    return a.text === b.text && a.by === b.by && a.on === b.on;
}

/** The steps from a section's old lines to its new ones, as the longest run
 *  they share: each old line kept or dropped, and the new lines that follow
 *  each (-1: before the first). An edited line is a drop and an add in the
 *  same place, so it stays where it was. */
function alignLines(old: readonly PlanLine[], next: readonly PlanLine[]): { kept: boolean[]; after: Map<number, PlanLine[]> } {
    const shared = Array.from({ length: old.length + 1 }, () => new Array<number>(next.length + 1).fill(0));
    for (let i = old.length - 1; i >= 0; i -= 1) {
        for (let j = next.length - 1; j >= 0; j -= 1) {
            shared[i][j] = sameLine(old[i], next[j]) ? shared[i + 1][j + 1] + 1 : Math.max(shared[i + 1][j], shared[i][j + 1]);
        }
    }
    const kept = new Array<boolean>(old.length).fill(false);
    const after = new Map<number, PlanLine[]>();
    const add = (at: number, line: PlanLine) => after.set(at, [...(after.get(at) ?? []), line]);
    let i = 0;
    let j = 0;
    while (i < old.length || j < next.length) {
        if (i < old.length && j < next.length && sameLine(old[i], next[j])) {
            kept[i] = true;
            i += 1;
            j += 1;
        } else if (i < old.length && (j >= next.length || shared[i + 1][j] >= shared[i][j + 1])) {
            i += 1;
        } else {
            add(i - 1, next[j]);
            j += 1;
        }
    }
    return { kept, after };
}

/** The new front matter block, or the old one untouched when the plan's
 *  own keys did not change, so a line edit never reformats it. */
function mergedHead(plan: Plan, before: Plan, head: string, front: string | null): string {
    const meta = metaOf(plan);
    const was = metaOf(before);
    if (META_KEYS.every((key) => meta[key] === was[key])) return head;
    if (front === null) return Object.keys(meta).length ? "---\n" + dump(meta, { lineWidth: -1 }) + "---\n\n" : "";
    const kept = loadFront(front);
    if (kept === null) throw new Error("plan.md's front matter doesn't parse, so saving the measure would lose it. Fix it in plan.md first.");
    for (const key of META_KEYS) {
        if (meta[key]) kept[key] = meta[key];
        else delete kept[key];
    }
    return "---\n" + dump(kept, { lineWidth: -1 }) + "---";
}

/** Writes the plan. Given the file it was read from, it edits that file in
 *  place: only the lines and front matter keys the plan holds change, and
 *  everything the parser does not read -- a title, prose, another heading,
 *  another key -- stays as it was written. */
export function serializePlan(plan: Plan, onto?: string | null): string {
    if (onto) return mergePlan(plan, onto);
    const meta = metaOf(plan);
    const front = Object.keys(meta).length ? "---\n" + dump(meta, { lineWidth: -1 }) + "---\n\n" : "";
    const sections = PLAN_SECTIONS.map((section) => {
        const lines = plan.sections[section].map((line) => `- ${written(line)}`);
        return `## ${SECTION_TITLES[section]}\n${lines.join("\n")}`;
    });
    return `${front}${PREAMBLE}\n\n${sections.join("\n\n")}\n`;
}

function mergePlan(plan: Plan, onto: string): string {
    const before = parsePlan(onto);
    const { head, front, body } = splitFront(onto);
    const aligned = Object.fromEntries(PLAN_SECTIONS.map((section) => [section, alignLines(before.sections[section], plan.sections[section])])) as Record<PlanSection, ReturnType<typeof alignLines>>;
    const seen: Record<PlanSection, number> = { must: 0, how: 0, who: 0, never: 0 };
    const headingAt: Partial<Record<PlanSection, number>> = {};
    const out: string[] = [];
    let current: PlanSection | null = null;
    for (const raw of body.split("\n")) {
        const heading = HEADING.exec(raw);
        if (heading) {
            current = sectionOf(heading[1]);
            out.push(raw);
            if (current && headingAt[current] === undefined) headingAt[current] = out.length - 1;
            continue;
        }
        const item = ITEM.exec(raw);
        if (!item || !current || !item[2].trim()) {
            out.push(raw);
            continue;
        }
        const { kept, after } = aligned[current];
        const index = seen[current];
        seen[current] += 1;
        if (index === 0) out.push(...(after.get(-1) ?? []).map((line) => `- ${written(line)}`));
        if (kept[index]) out.push(raw);
        /* A replaced line keeps the bullet and indent it was written with. */
        out.push(...(after.get(index) ?? []).map((line, at) => `${at === 0 && !kept[index] ? item[1] : "- "}${written(line)}`));
    }
    /* Lines for a section that had none: under its heading when the file has
       one, else under a new heading at the end. Inserted from the bottom up,
       so earlier positions hold. */
    const empty = PLAN_SECTIONS.filter((section) => before.sections[section].length === 0 && (aligned[section].after.get(-1) ?? []).length > 0);
    const under = empty.filter((section) => headingAt[section] !== undefined).sort((a, b) => (headingAt[b] as number) - (headingAt[a] as number));
    for (const section of under) {
        out.splice((headingAt[section] as number) + 1, 0, ...(aligned[section].after.get(-1) ?? []).map((line) => `- ${written(line)}`));
    }
    for (const section of empty.filter((one) => headingAt[one] === undefined)) {
        while (out.length && !out[out.length - 1].trim()) out.pop();
        out.push("", `## ${SECTION_TITLES[section]}`, ...(aligned[section].after.get(-1) ?? []).map((line) => `- ${written(line)}`), "");
    }
    return mergedHead(plan, before, head, front) + out.join("\n");
}

export interface DraftInput {
    description?: string | null;
    schedules: readonly { title: string; trigger: string; active: boolean; internal: boolean }[];
    agents: readonly { label: string; blurb: string; front: boolean }[];
    skills: readonly string[];
}

function lowerFirst(value: string): string {
    return value ? value.charAt(0).toLowerCase() + value.slice(1) : value;
}

/** A first plan, read off what the pod already runs, for a pod with no plan
 *  file yet. Nothing invented: every line names something that exists. */
export function draftPlan(input: DraftInput): Plan {
    const plan = emptyPlan(input.description?.trim() ?? "");
    if (plan.outcome) plan.sections.must.push({ text: plan.outcome });
    for (const name of input.skills.slice(0, 6)) plan.sections.how.push({ text: `Follows the ${name} skill` });
    for (const job of input.schedules) {
        if (!job.active || job.internal) continue;
        plan.sections.who.push({ text: `${job.title} · ${lowerFirst(job.trigger)}` });
    }
    for (const agent of input.agents) {
        if (agent.front) continue;
        plan.sections.who.push({ text: agent.blurb ? `${agent.label}: ${lowerFirst(agent.blurb)}` : agent.label });
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

/** Whether an outcome is holding its bar: only for a weekly outcome whose
 *  plan names a target and whose measure answered. A finish line has no pace
 *  to compare against yet, so it says nothing rather than guess. */
export function standingOf(plan: Plan | null | undefined, value: number | null | undefined): "on" | "behind" | null {
    if (!plan || value === null || value === undefined || !plan.target || plan.kind === "date") return null;
    const target = Number.parseFloat(plan.target);
    if (!Number.isFinite(target)) return null;
    return value >= target ? "on" : "behind";
}

/** "Oct 14", the stamp a line is written with. */
export function stampOf(at: Date): string {
    return at.toLocaleDateString("en-US", { month: "short", day: "numeric" });
}

/* ── the queue ────────────────────────────────────────────────────────── */

export type QueueKind = "decide" | "answer" | "do";

export interface QueueItem {
    key: string;
    kind: QueueKind;
    podId: string;
    podName: string;
    title: string;
    detail: string | null;
    /** When it started waiting, epoch ms. */
    sinceMs: number;
    conversationId: string | null;
    runId: string | null;
    /** A contact's chat, waiting on a person. */
    contactId: string | null;
}

/** Someone outside a Lemma who wrote in and is waiting on a person: its
 *  agent is off for them, or asked for one. */
export interface ContactWaiting {
    podId: string;
    podName: string;
    channelId: string;
    name: string;
    reason: string | null;
    sinceMs: number;
}

/** Everything waiting on you, as one list, oldest first. Approvals and
 *  permissions are decisions; a question, or a contact waiting on a person,
 *  wants an answer; a workflow form is something to do. */
export function queueItems(asked: readonly AskedRow[], waits: readonly WaitingRow[], contacts: readonly ContactWaiting[] = []): QueueItem[] {
    const items: QueueItem[] = [];
    for (const row of asked) {
        items.push({
            key: "ask:" + row.conversationId,
            kind: row.kind === "question" ? "answer" : "decide",
            podId: row.podId,
            podName: row.podName,
            title: row.summary ?? row.title,
            detail: row.summary ? row.title : null,
            sinceMs: row.sinceMs,
            conversationId: row.conversationId,
            runId: null,
            contactId: null,
        });
    }
    for (const row of waits) {
        const since = row.wait.createdAt ? Date.parse(row.wait.createdAt) : Number.NaN;
        items.push({
            key: "wait:" + row.wait.id,
            kind: row.wait.type === "DECISION" ? "decide" : "do",
            podId: row.podId,
            podName: row.podName,
            title: row.wait.question ?? `Fill in the ${row.workflowName} form`,
            detail: row.workflowName,
            sinceMs: Number.isNaN(since) ? Date.now() : since,
            conversationId: row.wait.externalRef,
            runId: row.run.id,
            contactId: null,
        });
    }
    for (const row of contacts) {
        items.push({
            key: "contact:" + row.channelId,
            kind: "answer",
            podId: row.podId,
            podName: row.podName,
            title: `Reply to ${row.name}`,
            detail: row.reason ?? "Waiting on a person",
            sinceMs: row.sinceMs,
            conversationId: null,
            runId: null,
            contactId: row.channelId,
        });
    }
    return items.sort((a, b) => a.sinceMs - b.sinceMs);
}

/** "40 min", "26 h", "3 d". */
export function ageOf(sinceMs: number, now: number): string {
    const minutes = Math.max(0, Math.round((now - sinceMs) / 60_000));
    if (minutes < 60) return `${Math.max(1, minutes)} min`;
    const hours = Math.round(minutes / 60);
    if (hours < 48) return `${hours} h`;
    return `${Math.round(hours / 24)} d`;
}

/* ── chats ────────────────────────────────────────────────────────────── */

/** A chat with Lemma, someone's private line with it (WhatsApp, Telegram,
 *  what it sends them), or a chat shared with people. */
export type ChatKind = "chat" | "line" | "shared";

export interface ChatItem {
    key: string;
    open: OpenChat;
    podId: string;
    podName: string;
    kind: ChatKind;
    title: string;
    /** The last thing said, for a shared chat; where it came from, for a
     *  chat that started on a platform. Shown after the name, quieter. */
    said: string | null;
    /** Last active, epoch ms, or null when the server gave no time. */
    at: number | null;
    needsYou: boolean;
    /** New lines in a shared chat since the reader last read it. */
    unread: number;
    /** Lemma is working in it right now. */
    running: boolean;
    /** The platforms feeding it, for its mark. */
    platforms: string[];
    channel: Channel | null;
    conversation: ConversationRef | null;
}

/** Where a conversation came from, when it counts as a chat. A run that a
 *  schedule, a workflow, a notice or another outcome started is Lemma's own
 *  work, read as activity on its outcome; one about a doc lives on the doc.
 *  Any of them still waiting on you is listed, because that is where it is
 *  answered. */
const CHAT_ORIGINS: ReadonlySet<OriginKind> = new Set<OriginKind>(["chat", "channel"]);

export function isChat(conversation: Pick<ConversationRef, "origin" | "waitingOnYou" | "boundTo">): boolean {
    if (conversation.waitingOnYou) return true;
    if (conversation.boundTo) return false;
    return CHAT_ORIGINS.has(conversation.origin?.kind ?? "chat");
}

/** Someone's private line with a Lemma, named for what it is: theirs, on
 *  the platforms it comes through -- or the notices the Lemma sends them.
 *  Called just "Email" beside chats with names, it read as an inbox. */
export function lineTitle(platforms: string): string {
    return platforms === "Notices" ? "Notices" : `Your line on ${platforms}`;
}

function timeOf(iso: string | null | undefined): number | null {
    const at = iso ? Date.parse(iso) : Number.NaN;
    return Number.isNaN(at) ? null : at;
}

function newestFirst(a: ChatItem, b: ChatItem): number {
    if (a.at !== null && b.at !== null) return b.at - a.at || a.title.localeCompare(b.title);
    if (a.at !== b.at) return a.at === null ? 1 : -1;
    return a.title.localeCompare(b.title);
}

/** Every chat in the outcomes given, as one list in two groups: what is
 *  waiting on the reader, then everything else, newest first in each.
 *  `search` keeps the ones whose name or outcome contains it. */
export function chatItems(input: {
    pods: readonly { id: string; name: string }[];
    conversations: ReadonlyMap<string, readonly ConversationRef[]>;
    channels: ReadonlyMap<string, readonly Channel[]>;
    me: string | null;
    search?: string;
}): { needs: ChatItem[]; rest: ChatItem[] } {
    const items: ChatItem[] = [];
    for (const pod of input.pods) {
        for (const channel of input.channels.get(pod.id) ?? []) {
            if (!listedAsChat(channel)) continue;
            items.push({
                key: `g:${pod.id}:${channel.id}`,
                open: { kind: "shared", podId: pod.id, id: channel.id },
                podId: pod.id,
                podName: pod.name,
                kind: channel.kind === "DIRECT" ? "line" : "shared",
                title: channel.kind === "DIRECT" ? lineTitle(channelTitle(channel, pod.name)) : channelTitle(channel, pod.name),
                said: channel.lastLine || channel.topic ? previewOf(channel, input.me) : null,
                at: timeOf(channel.lastMessageAt ?? channel.createdAt),
                needsYou: channel.unread.waiting > 0,
                unread: channel.unread.lines,
                running: false,
                platforms: platformsOf(channel),
                channel,
                conversation: null,
            });
        }
        for (const conversation of input.conversations.get(pod.id) ?? []) {
            if (!isChat(conversation)) continue;
            const origin = conversation.origin;
            items.push({
                key: `c:${pod.id}:${conversation.id}`,
                open: { kind: "chat", podId: pod.id, id: conversation.id },
                podId: pod.id,
                podName: pod.name,
                kind: "chat",
                title: conversation.title,
                said: origin && origin.kind !== "chat" ? origin.label : null,
                at: timeOf(conversation.activityAt),
                needsYou: conversation.waitingOnYou === true,
                unread: 0,
                running: isRunning(conversation),
                platforms: origin?.platform ? [origin.platform.toUpperCase()] : [],
                channel: null,
                conversation,
            });
        }
    }
    const needle = input.search?.trim().toLowerCase() ?? "";
    const kept = needle
        ? items.filter((item) => item.title.toLowerCase().includes(needle) || item.podName.toLowerCase().includes(needle) || (item.said ?? "").toLowerCase().includes(needle))
        : items;
    kept.sort(newestFirst);
    return { needs: kept.filter((item) => item.needsYou), rest: kept.filter((item) => !item.needsYou) };
}

/* ── a Lemma's recent things, in the sidebar ─────────────────────────── */

/** A line in a Lemma's sidebar section: a chat, or something of its own
 *  work -- an automation that ran, a table that changed -- each opening
 *  where it is read. */
export type RowKind = ChatKind | "automation" | "table";

export interface SideRow {
    key: string;
    kind: RowKind;
    title: string;
    /** More about it, for its tooltip: the last thing said, where it came from. */
    said: string | null;
    at: number | null;
    view: View;
    needsYou: boolean;
    unread: number;
    running: boolean;
    platforms: string[];
    channel: Channel | null;
    /** An automation's runs lately, when it has run more than once. */
    runs?: number;
}

export function rowOfChat(item: ChatItem): SideRow {
    return {
        key: item.key,
        kind: item.kind,
        title: item.title,
        said: item.said,
        at: item.at,
        view: chatView(item.open),
        needsYou: item.needsYou,
        unread: item.unread,
        running: item.running,
        platforms: item.platforms,
        channel: item.channel,
    };
}

const SCHEDULE_LABEL = /^Schedule\s*·\s*/;
const WORKFLOW_TITLE = /^Workflow run:\s*/i;

/** A run's automation, named: a schedule by its schedule; a workflow run by
 *  the workflow its title names ("Workflow run: reviewer" is Reviewer's). */
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

/** A Lemma's own recent work as lines: one per automation -- its latest
 *  run, and how many it has run lately -- and the tables changed within the
 *  week. Runs waiting on the reader are not here: they are chats, listed
 *  with Needs you's sign. */
export function workRows(input: {
    podId: string;
    conversations: readonly ConversationRef[];
    tables: readonly { name: string; title: string; updated: string | null }[];
    now: number;
}): SideRow[] {
    const latest = new Map<string, { row: SideRow; runs: Set<string> }>();
    for (const conversation of input.conversations) {
        const origin = conversation.origin;
        if (!origin || conversation.waitingOnYou) continue;
        const automation = automationOf(conversation);
        if (!automation) continue;
        const run = origin.runId ?? conversation.id;
        const at = timeOf(conversation.activityAt);
        const row: SideRow = {
            key: "auto:" + automation.key,
            kind: "automation",
            title: automation.name,
            said: origin.kind === "schedule" ? "Runs on a schedule" : "A workflow",
            at,
            view: origin.runId ? { name: "run", podId: input.podId, runId: origin.runId } : chatView({ kind: "chat", podId: input.podId, id: conversation.id }),
            needsYou: false,
            unread: 0,
            running: isRunning(conversation),
            platforms: [],
            channel: null,
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
        return [{
            key: "table:" + table.name,
            kind: "table",
            title: table.title,
            said: "A table",
            at,
            view: { name: "table", podId: input.podId, table: table.name },
            needsYou: false,
            unread: 0,
            running: false,
            platforms: [],
            channel: null,
        }];
    });
    return [...automations, ...tables];
}

/** A section's lines: what waits on the reader first, then everything --
 *  chats, automations, tables -- newest first. */
export function sectionRows(chats: readonly ChatItem[], work: readonly SideRow[]): SideRow[] {
    const rows = [...chats.map(rowOfChat), ...work];
    const byTime = (a: SideRow, b: SideRow) => {
        if (a.at !== null && b.at !== null) return b.at - a.at || a.title.localeCompare(b.title);
        if (a.at !== b.at) return a.at === null ? 1 : -1;
        return a.title.localeCompare(b.title);
    };
    return [...rows.filter((row) => row.needsYou).sort(byTime), ...rows.filter((row) => !row.needsYou).sort(byTime)];
}

/** What a sidebar section shows before "Show more": the first `limit` lines,
 *  plus anything waiting on the reader and the line that is open, wherever
 *  they fall -- so neither hides behind the fold. Order is kept. */
export function foldSection<T extends { needsYou: boolean; view: View }>(rows: readonly T[], here: View, limit: number): { shown: T[]; hidden: number } {
    const at = writeView(here);
    const shown = rows.filter((row, index) => index < limit || row.needsYou || writeView(row.view) === at);
    return { shown, hidden: rows.length - shown.length };
}

/** Whether a chat earns a line in the sidebar. Someone's private line with
 *  Lemma -- WhatsApp, email, what it sends them -- is a way in, not a chat,
 *  so it is listed only while it has something new. */
export function inSidebar(item: { kind: string; unread: number; needsYou: boolean }): boolean {
    return item.kind !== "line" || item.unread > 0 || item.needsYou;
}

/** How long an outcome can go without a chat moving before its section
 *  folds itself away. */
export const QUIET_AFTER_MS = 7 * 86_400_000;

export interface SidebarSection<T, R> {
    pod: T;
    items: R[];
    /** When its newest chat last moved, epoch ms. */
    latest: number | null;
    /** Its chats waiting on the reader. */
    waiting: number;
    open: boolean;
}

/** The sidebar's outcomes: the one that moved most recently first, the ones
 *  that never moved last, by name. Each is open unless it has been quiet for
 *  a week -- and always when it is where the reader is, or has something
 *  waiting on them. What the reader folded or opened themselves wins. */
export function sidebarSections<T extends { id: string; name: string }, R extends { at: number | null; needsYou: boolean }>(
    sections: readonly { pod: T; items: readonly R[] }[],
    now: number,
    chosen: ReadonlyMap<string, boolean>,
    here: string | null,
): SidebarSection<T, R>[] {
    return sections
        .map(({ pod, items }) => {
            const times = items.map((item) => item.at).filter((at): at is number => at !== null);
            const latest = times.length > 0 ? Math.max(...times) : null;
            const waiting = items.filter((item) => item.needsYou).length;
            const lively = waiting > 0 || pod.id === here || (latest !== null && now - latest < QUIET_AFTER_MS);
            return { pod, items: [...items], latest, waiting, open: chosen.get(pod.id) ?? lively };
        })
        .sort((a, b) => {
            if (a.latest !== null && b.latest !== null && a.latest !== b.latest) return b.latest - a.latest;
            if ((a.latest === null) !== (b.latest === null)) return a.latest === null ? 1 : -1;
            return a.pod.name.localeCompare(b.pod.name);
        });
}

/** The chats in a list whose names repeat, which get their time beside them
 *  so two "Weekly check-in"s can be told apart. */
export function repeatedTitles(items: readonly Pick<ChatItem, "key" | "title">[]): Set<string> {
    const counts = new Map<string, number>();
    for (const item of items) counts.set(item.title, (counts.get(item.title) ?? 0) + 1);
    return new Set(items.filter((item) => (counts.get(item.title) ?? 0) > 1).map((item) => item.key));
}

/** What a Lemma owns, said as it should be read: only a plan's sentence
 *  counts. A description is what it is about, not a result it holds, and
 *  shown as one it read as a fake ("owns: do something cool"). */
export const NOTHING_OWNED = "Nothing it owns yet. Give it a bar or a finish line.";

/** Whether two opens show the same thing. */
export function sameOpen(a: ChatOpen, b: ChatOpen): boolean {
    if (a.kind !== b.kind) return false;
    if (a.kind === "none" || b.kind === "none") return true;
    return a.podId === b.podId && (a as { id: string | null }).id === (b as { id: string | null }).id;
}
