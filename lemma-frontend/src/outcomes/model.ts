/** The outcome frame's pure parts: its address grammar (`address.ts`,
 *  re-exported here), a day-by-day count of work, the plan file, and how the
 *  queue groups what is waiting on you.
 *  Nothing here reaches the network or the page, so all of it is tested
 *  directly (`tests/outcomes.test.ts`). */

import type { ConversationRef } from "@/data/types";
import type { ChatItem, ChatKind } from "@/channels/chat-list";
import type { Channel } from "@/channels/model";
import { readableName } from "@/library/reading";
import type { OriginKind } from "@/thread/conversation-origin";
import type { AskedRow } from "@/thread/waiting-on-you";
import type { WaitingRow } from "@/workflow/waiting-inbox";

export * from "./address";
/* Which paths are the frame's own, rather than workspace addresses that
   `readAddress` and `viewOfAddress` read, is in `./paths`. */
export { isFramePath } from "./paths";
import { chatView, outcomeView, writeView, type ChatOpen, type View } from "./address";

/** Where something waiting on you opens, inside the frame: its conversation,
 *  or the workflow run whose form it is. */
export function viewOfItem(item: Pick<QueueItem, "podId" | "conversationId" | "runId" | "contactId"> & { line?: QueueItem["line"] }): View {
    if (item.line) return chatView({ kind: "shared", podId: item.podId, id: item.line.channelId });
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
export function viewOfAddress(address: { podId: string | null; tabId: string | null; conversationId: string | null; section?: string | null; agentName?: string | null; team?: boolean }): View | null {
    const { podId, tabId } = address;
    /* The workspace's every-teammate page is the list of Lemmas here. */
    if (address.team) return { name: "outcomes" };
    if (!podId) return null;
    switch (tabId) {
        case null:
        case "space:home":
            return outcomeView(podId);
        case "space:chats":
        case "history":
            return outcomeView(podId, "chats");
        case "space:channels":
            return outcomeView(podId, "chats", "channels");
        case "space:about":
            /* Schedules are a part of About in the workspace and a view of
               Automations here. */
            if (address.section === "schedules") return outcomeView(podId, "automations", "schedules");
            /* One part of it is that part of Settings; the page itself is About. */
            return address.section ? outcomeView(podId, "settings", address.section) : outcomeView(podId, "about");
        case "profile":
            return address.agentName ? { name: "agent", podId, agent: address.agentName } : outcomeView(podId, "settings");
        case "conversation":
            return address.conversationId ? chatView({ kind: "chat", podId, id: address.conversationId }) : outcomeView(podId);
        case "space:contacts":
            return outcomeView(podId, "contacts");
        case "space:apps":
        case "apps":
            return outcomeView(podId, "apps");
        case "space:pages":
            return outcomeView(podId, "pages");
        case "space:tables":
            return outcomeView(podId, "tables");
        case "space:all":
        case "space:files":
        case "library":
            return outcomeView(podId, "files");
        case "space:workflows":
            return outcomeView(podId, "automations");
        case "computer":
            return { name: "computer", podId, conversationId: null };
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
    /** Files that go with the first message, as the box they were attached
     *  in would have sent them. */
    files?: File[];
    /** A folder on this computer chosen in that box, by the id it was
     *  parked under (desktop app only), for the new chat to work in. */
    folder?: string;
}

/** Words put in a chat's box -- what an app or a widget asked for, or ⌘K
 *  without a live session. Nothing sends them but the reader. */
export interface Fill {
    text: string;
    id: number;
    podId: string;
    /** Files attached with the words, held in the box with them. */
    files?: File[];
}

export interface GoOptions {
    /** Rewrite this history entry rather than add one: a new chat taking
     *  its id is the same place, not a step to go back through. */
    replace?: boolean;
    handoff?: Handoff;
    fill?: Fill;
}

export type Go = (view: View, options?: GoOptions) => void;

/** What goes with words asked of a Lemma: files from the box they were
 *  typed in, and what the new chat is made with -- the model picked there,
 *  or the agent it is with. */
export interface AskWith {
    files?: File[];
    createWith?: Record<string, unknown>;
    /** The id a folder chosen in the box was parked under. */
    folder?: string;
}

/** Start a chat with a Lemma with these words, sent, and open it. */
export type Ask = (podId: string, text: string, extra?: AskWith) => void;

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
    /** The workflow wait itself, when the row is one: it carries its form. */
    wait: WaitingRow["wait"] | null;
    /** A question a Lemma asked in your chat with it, answered by a choice
     *  it offered or in words. */
    /** `prompt` is the whole question when the title is only its first line. */
    line: { channelId: string; messageId: string; choices: string[]; prompt: string | null } | null;
}

/** Where a row is answered: in place, from its own card ("ask") or its
 *  form ("form"), or only where it came from (null). A question or an
 *  approval is answered with the card the chat would dock, and a form wait
 *  with the form the run would show. A contact wants a written reply, and
 *  a workflow decision has no form to submit, so both open where they are. */
export function answersHere(item: Pick<QueueItem, "contactId" | "wait" | "conversationId" | "runId"> & { line?: QueueItem["line"] }): "ask" | "form" | "line" | null {
    if (item.line) return "line";
    if (item.contactId) return null;
    if (item.wait) return item.wait.type === "HUMAN" ? "form" : null;
    if (item.runId) return null;
    return item.conversationId ? "ask" : null;
}

/** The row's button: what doing it is called. */
export function verbOf(item: Pick<QueueItem, "kind" | "contactId" | "wait" | "conversationId" | "runId"> & { line?: QueueItem["line"] }): string {
    const here = answersHere(item);
    if (here === "line") return "Answer";
    if (here === "form") return "Fill in";
    if (here === "ask") return item.kind === "decide" ? "Decide" : "Answer";
    return item.contactId ? "Reply" : "Open";
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
            wait: null,
            line: null,
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
            wait: row.wait,
            line: null,
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
            wait: null,
            line: null,
        });
    }
    return items.sort((a, b) => a.sinceMs - b.sinceMs);
}

/** One thing waiting on you as the organization's overview lists it
 *  (`GET /organizations/{id}/needs-you`), in this frame's names. */
export interface NeedsItem {
    key: string;
    kind: "QUESTION" | "APPROVAL" | "PERMISSION" | "FORM" | "DECISION" | "CONTACT" | "MESSAGE";
    podId: string;
    podName: string;
    title: string | null;
    detail: string | null;
    /** ISO time it started waiting. */
    since: string;
    conversationId: string | null;
    workflowRunId: string | null;
    channelId: string | null;
    messageId: string | null;
    choices: readonly string[];
}

/** Everything waiting on you, from the organization's one list, oldest
 *  first. A workflow form is matched to its wait, which carries the form to
 *  fill in here; one whose wait has not been read yet opens its run. */
export function queueFromNeeds(needs: readonly NeedsItem[], waits: readonly WaitingRow[]): QueueItem[] {
    const byRun = new Map(waits.map((row) => [row.run.id, row]));
    const items: QueueItem[] = [];
    for (const need of needs) {
        const since = Date.parse(need.since);
        const sinceMs = Number.isNaN(since) ? Date.now() : since;
        const base = { podId: need.podId, podName: need.podName, sinceMs, conversationId: null, runId: null, contactId: null, wait: null, line: null };
        switch (need.kind) {
            case "QUESTION":
            case "APPROVAL":
            case "PERMISSION":
                if (!need.conversationId) break;
                items.push({ ...base, key: "ask:" + need.conversationId, kind: need.kind === "QUESTION" ? "answer" : "decide", title: need.title ?? "A question for you", detail: need.detail, conversationId: need.conversationId });
                break;
            case "FORM": {
                const row = need.workflowRunId ? byRun.get(need.workflowRunId) : undefined;
                if (row) items.push({ ...base, key: "wait:" + row.wait.id, kind: "do", title: row.wait.question ?? need.title ?? `Fill in the ${row.workflowName} form`, detail: row.workflowName, conversationId: row.wait.externalRef, runId: row.run.id, wait: row.wait });
                else if (need.workflowRunId) items.push({ ...base, key: need.key, kind: "do", title: need.title ?? "A form to fill in", detail: need.detail, runId: need.workflowRunId });
                break;
            }
            case "DECISION":
                if (need.workflowRunId) items.push({ ...base, key: need.key, kind: "decide", title: need.title ?? "A decision for you", detail: need.detail, runId: need.workflowRunId });
                break;
            case "CONTACT":
                if (need.channelId) items.push({ ...base, key: "contact:" + need.channelId, kind: "answer", title: `Reply to ${need.title ?? "someone waiting"}`, detail: need.detail ?? "Waiting on a person", contactId: need.channelId });
                break;
            case "MESSAGE":
                if (need.channelId && need.messageId) items.push({ ...base, key: need.key, kind: "answer", title: need.title ?? "A question for you", detail: null, line: { channelId: need.channelId, messageId: need.messageId, choices: [...need.choices], prompt: need.detail } });
                break;
        }
    }
    return items.sort((a, b) => a.sinceMs - b.sinceMs);
}

/* ── templates ───────────────────────────────────────────────────────── */

/** A template as the server lists it (`GET /pods/bundle/templates`): a kit
 *  of tables, skills and automations a Lemma can be given. */
export interface ShelfCard {
    id: string;
    name: string;
    /** The kind of work it does: support, sales, engineering… */
    work: string | null;
    face: string | null;
    /** Where its work usually happens, as a label. */
    place: string | null;
    brings: readonly string[];
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

/* What a chat is, its name, its order and its two groups are the one chat
   list model every surface draws (`@/channels/chat-list`); the frame adds
   only how a row sits in its sidebar, below. */
export { chatItems, isChat, type ChatItem, type ChatKind } from "@/channels/chat-list";

function timeOf(iso: string | null | undefined): number | null {
    const at = iso ? Date.parse(iso) : Number.NaN;
    return Number.isNaN(at) ? null : at;
}

/* ── a Lemma's recent things, in the sidebar ─────────────────────────── */

/** A line in a Lemma's sidebar section: a chat, or something of its own
 *  work -- an automation that ran, a table that changed -- each opening
 *  where it is read; or an app the reader pinned. */
export type RowKind = ChatKind | "automation" | "table" | "app";

export interface SideRow {
    key: string;
    kind: RowKind;
    /** The Lemma it is in, for a list that mixes them. */
    podId: string;
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
        podId: item.podId,
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
    const latest = new Map<string, { row: SideRow; runs: Set<string>; counted: number }>();
    for (const conversation of input.conversations) {
        const origin = conversation.origin;
        if (!origin || conversation.waitingOnYou) continue;
        const automation = automationOf(conversation);
        if (!automation) continue;
        const run = origin.runId ?? conversation.id;
        const at = timeOf(conversation.activityAt);
        const row: SideRow = {
            /* Its Lemma in its key: a list that mixes Lemmas can hold two
               automations of one name. */
            key: "auto:" + input.podId + ":" + automation.key,
            kind: "automation",
            podId: input.podId,
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
            latest.set(row.key, { row, runs: new Set([run]), counted: conversation.runs ?? 0 });
            continue;
        }
        held.runs.add(run);
        held.counted = Math.max(held.counted, conversation.runs ?? 0);
        if ((row.at ?? 0) > (held.row.at ?? 0)) held.row = { ...row, running: row.running || held.row.running };
        else if (row.running) held.row = { ...held.row, running: true };
    }
    /* Runs listed one by one are counted here; a source that counted them
       itself (the organization's overview) says how many there were. */
    const automations = [...latest.values()].map(({ row, runs, counted }) => ({ ...row, runs: Math.max(runs.size, counted) }));
    const tables = input.tables.flatMap((table): SideRow[] => {
        const at = timeOf(table.updated);
        if (at === null || input.now - at > QUIET_AFTER_MS) return [];
        return [{
            key: "table:" + input.podId + ":" + table.name,
            kind: "table",
            podId: input.podId,
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

/** What the sidebar lists under each Lemma, as the reader asked: its chats;
 *  with its automations' latest runs; or with the tables changed lately as
 *  well. */
export type SideShowing = "chats" | "automations" | "everything";

/** How the sidebar lists them: under each Lemma, or all together, newest
 *  first. */
export type SideGrouping = "lemma" | "together";

export interface SideSettings {
    showing: SideShowing;
    grouping: SideGrouping;
}

export const SIDE_DEFAULTS: SideSettings = { showing: "chats", grouping: "lemma" };

/** What a browser kept of the reader's choice, read safely: anything it
 *  does not recognise is the default. */
export function readSideSettings(raw: unknown): SideSettings {
    const kept = raw && typeof raw === "object" ? raw as Partial<SideSettings> : {};
    return {
        showing: kept.showing === "automations" || kept.showing === "everything" ? kept.showing : "chats",
        grouping: kept.grouping === "together" ? "together" : "lemma",
    };
}

/** How long a table can go unchanged before it stops counting as recent. */
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

/** The sidebar's Lemmas: the one that moved most recently first, the ones
 *  that never moved last, by name. Each is folded to its name -- a list of
 *  Lemmas, the way a list of projects reads -- and a dot on a folded one says
 *  something in it waits on the reader. One opens while the reader is in it,
 *  and stays open once they open it themselves; what they folded or opened
 *  wins. */
export function sidebarSections<T extends { id: string; name: string }, R extends { at: number | null; needsYou: boolean }>(
    sections: readonly { pod: T; items: readonly R[] }[],
    chosen: ReadonlyMap<string, boolean>,
    here: string | null,
): SidebarSection<T, R>[] {
    return sections
        .map(({ pod, items }) => {
            const times = items.map((item) => item.at).filter((at): at is number => at !== null);
            const latest = times.length > 0 ? Math.max(...times) : null;
            const waiting = items.filter((item) => item.needsYou).length;
            return { pod, items: [...items], latest, waiting, open: chosen.get(pod.id) ?? pod.id === here };
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

/** Whether two opens show the same thing. */
export function sameOpen(a: ChatOpen, b: ChatOpen): boolean {
    if (a.kind !== b.kind) return false;
    if (a.kind === "none" || b.kind === "none") return true;
    return a.podId === b.podId && (a as { id: string | null }).id === (b as { id: string | null }).id;
}

/** An agent row's page, or none for a row that arrived without a name: its
 *  address would end at `agent/` and land on the Lemma's settings, while the
 *  row itself says there is nothing to open. */
export function agentView(podId: string, agent: { name: string; broken: boolean }): View | null {
    return agent.broken ? null : { name: "agent", podId, agent: agent.name };
}

/** A key for an agent row. Two nameless rows share an empty id, so the
 *  position keys them apart. */
export function agentKey(agent: { id: string }, at: number): string {
    return agent.id || `unnamed-${at}`;
}

/** An app kept mounted this visit, with the organization it was opened in
 *  and its Lemma as last read. */
export type KeptApp<P extends { id: string }> = { podId: string; app: string; orgId: string | null; pod: P | null };

/** The kept apps against a Lemma list just read for `orgId`. Each takes its
 *  Lemma as now listed. One whose own organization's list no longer has its
 *  Lemma goes. One from another organization stays as it was, so switching
 *  organization never unmounts it. The same array when nothing changed. */
export function refreshKept<P extends { id: string }>(kept: readonly KeptApp<P>[], pods: readonly P[], orgId: string | null): readonly KeptApp<P>[] {
    let changed = false;
    const next = kept.flatMap((one) => {
        const now = pods.find((candidate) => candidate.id === one.podId);
        if (now) {
            if (now !== one.pod) changed = true;
            return [{ ...one, pod: now }];
        }
        if (one.orgId !== orgId) return [one];
        changed = true;
        return [];
    });
    return changed ? next : kept;
}

/** Where a pasted link to something shared opens here: a GitHub repository
 *  opens its import, and a link this deployment made -- a share, a remix, an
 *  import, from one of `ownOrigins` -- opens at its own path. Null for
 *  anything else, including another site's link that happens to have a path
 *  like one of these. */
export function sharedLinkPath(text: string, ownOrigins: readonly string[]): string | null {
    const said = text.trim();
    if (!said) return null;
    let url: URL;
    try {
        url = new URL(/^[a-z][a-z0-9+.-]*:\/\//i.test(said) ? said : "https://" + said);
    } catch {
        return null;
    }
    const parts = url.pathname.split("/").filter(Boolean).map(decodedSegment);
    if (parts.some((part) => part === null)) return null;
    const segments = parts as string[];
    if (url.hostname === "github.com" || url.hostname === "www.github.com") {
        const [owner, repo] = segments;
        return owner && repo ? `/import/github/${encodeURIComponent(owner)}/${encodeURIComponent(repo.replace(/\.git$/, ""))}` : null;
    }
    const [head] = segments;
    if (!ownOrigins.includes(url.origin) || !head || !SHARED_HEADS.has(head)) return null;
    /* Rebuilt from its parts, each encoded, rather than passed on as typed: a
       link is somebody else's text, and what this navigates to is only ever
       one of this app's own paths. */
    const path = "/" + segments.map(encodeURIComponent).join("/");
    const query = [...url.searchParams].map(([name, value]) => `${encodeURIComponent(name)}=${encodeURIComponent(value)}`).join("&");
    return query ? `${path}?${query}` : path;
}

/** The first words of the paths a shared link of this app's opens at. */
const SHARED_HEADS: ReadonlySet<string> = new Set(["s", "d", "remix", "import"]);

/** One path segment as written, or null for one that is not valid percent-encoding. */
function decodedSegment(segment: string): string | null {
    try {
        return decodeURIComponent(segment);
    } catch {
        return null;
    }
}

/** What one step of a template's import adds, said as a person would count
 *  it: a skill is its SKILL.md, and a folder made on the way is nothing. */
function broughtKind(step: { kind: string; name: string }): [string, string] | null {
    const kind = step.kind.toUpperCase();
    if (kind === "TABLE") return ["table", "tables"];
    if (kind === "SCHEDULE" || kind === "WORKFLOW") return ["automation", "automations"];
    if (kind === "AGENT") return ["agent", "agents"];
    if (kind === "APP") return ["app", "apps"];
    if (kind === "FILE") {
        const last = step.name.split("/").pop() ?? "";
        if (last === "SKILL.md") return ["skill", "skills"];
        return last.includes(".") ? ["file", "files"] : null;
    }
    return null;
}

/** "1 table, 2 automations and 2 skills": what a template's import brings,
 *  counted by kind, in the order it brings them. */
export function broughtSaid(steps: readonly { kind: string; name: string }[]): string {
    const counts = new Map<string, { one: string; many: string; count: number }>();
    for (const step of steps) {
        const said = broughtKind(step);
        if (!said) continue;
        const now = counts.get(said[1]) ?? { one: said[0], many: said[1], count: 0 };
        counts.set(said[1], { ...now, count: now.count + 1 });
    }
    const parts = [...counts.values()].map(({ one, many, count }) => `${count} ${count === 1 ? one : many}`);
    return parts.length <= 1 ? parts.join("") : `${parts.slice(0, -1).join(", ")} and ${parts[parts.length - 1]}`;
}

/* ── a Lemma as a space ──────────────────────────────────────────────────── */

/** "Notion", "Notion and Airtable", "Notion, Google Drive and Airtable". */
function listed(names: readonly string[]): string {
    return names.length <= 1 ? names.join("") : `${names.slice(0, -1).join(", ")} and ${names[names.length - 1]}`;
}

/** What "Bring it in" sends: the places the person picked, in their words.
 *  The assistant's bring-in skill takes it from there. */
export function bringInRequest(places: readonly string[]): string {
    return `Bring in ${listed(places)}.`;
}

/** What a Lemma is asked once a template it was given has landed in it. */
export function templateSetupRequest(name: string): string {
    return `Set up the ${name} template I just added. Follow its set-up skill.`;
}

/** Who can find a Lemma, as its header says it; nothing until it is known. */
export function reachSaid(reach: "invited" | "org" | "anyone" | null, orgName: string): string {
    if (reach === null) return "";
    if (reach === "anyone") return "Anyone with a Lemma account can join";
    if (reach === "org") return `Everyone at ${orgName.trim() || "this organization"} can find it`;
    return "Only the people in it can find it";
}

/** What a Lemma says at the top of its pages, in its own voice: what it is
 *  doing, then whether it needs the reader. It counts only what the frame
 *  can show -- chats running now, things waiting on the reader -- so it
 *  never claims work nobody can open. */
export function lemmaSays(now: { empty: boolean; running: number; needs: number }): string {
    if (now.empty) return "I’m new here. I don’t have anything to work with yet.";
    const doing = now.running > 0
        ? `I’m working on ${now.running} ${now.running === 1 ? "thing" : "things"} right now.`
        : "All quiet.";
    const needs = now.needs > 0
        ? `${now.needs} ${now.needs === 1 ? "thing needs" : "things need"} you.`
        : "Nothing needs you.";
    return doing + " " + needs;
}

/* ── history ──────────────────────────────────────────────────────────── */

/** What moved in one Lemma lately, as the organization's overview answers it
 *  (`org.recents.get`): the reader's chats there, and the schedules and
 *  workflows that ran. Only the fields History reads. */
export interface RecentsPod {
    pod_id: string;
    pod_name: string;
    chats: readonly {
        conversation_id: string;
        title?: string | null;
        origin: string;
        status?: string | null;
        agent_id?: string | null;
        last_activity_at: string;
        waiting_on_you: boolean;
        platform?: string | null;
        channel_name?: string | null;
    }[];
    automations: readonly {
        kind: string;
        id: string;
        name?: string | null;
        run_count: number;
        latest_status: string;
        latest_at: string;
        workflow_run_id?: string | null;
        conversation_id?: string | null;
    }[];
    /** One count per day of the window, oldest first. */
    activity?: readonly { day: string; by_agent: number; by_people: number }[];
}

const ORIGIN_KINDS: ReadonlySet<string> = new Set(["chat", "channel", "notification", "schedule", "workflow", "resource", "task", "ask"]);

/** Every Lemma's recent chats and automation runs from the organization's
 *  one overview read, in the shape the frame reads a Lemma's own list in, so
 *  the sidebar, Home and a Lemma's page draw the same rows from one request
 *  that they drew from one request per Lemma.
 *
 *  A chat is itself. A schedule or a workflow that ran is one entry -- its
 *  newest run, carrying how many times it ran -- for the line `workRows`
 *  gives each automation. One that started nothing has nothing to open and
 *  is left out; one whose newest run is waiting on the reader is already
 *  listed as that chat, and is not listed twice. `stamp` says a time the way
 *  the frame's lists do. */
export function activityOfRecents(pods: readonly RecentsPod[], stamp: (iso: string) => string): Map<string, ConversationRef[]> {
    const byPod = new Map<string, ConversationRef[]>();
    for (const pod of pods) {
        const waiting = new Set(pod.chats.filter((chat) => chat.waiting_on_you).map((chat) => chat.conversation_id));
        const chats = pod.chats.map((chat): ConversationRef => {
            const kind = ORIGIN_KINDS.has(chat.origin) ? chat.origin as OriginKind : "chat";
            return {
                id: chat.conversation_id,
                title: chat.title?.trim() || "Untitled",
                at: stamp(chat.last_activity_at),
                activityAt: chat.last_activity_at,
                kind: "CHAT",
                /* The overview does not say which thing a chat about a page
                   or a table is bound to, only that it is: enough to keep it
                   out of the chats, where its own list never put it. */
                boundTo: kind === "resource" ? "resource" : null,
                origin: kind === "chat" ? undefined : { kind, label: chat.channel_name?.trim() || STARTED_BY[kind] || "", platform: chat.platform ?? undefined },
                agentId: chat.agent_id ?? null,
                status: chat.status ?? null,
                waitingOnYou: chat.waiting_on_you,
            };
        });
        const ran = pod.automations.flatMap((each): ConversationRef[] => {
            const opens = each.conversation_id ?? each.workflow_run_id;
            if (!opens || (each.conversation_id && waiting.has(each.conversation_id))) return [];
            const workflow = each.kind.toUpperCase() === "WORKFLOW";
            const name = each.name?.trim() ? readableName(each.name.trim()) : workflow ? "A workflow" : "A schedule";
            return [{
                id: opens,
                title: name,
                at: stamp(each.latest_at),
                activityAt: each.latest_at,
                kind: "CHAT",
                boundTo: null,
                origin: { kind: workflow ? "workflow" : "schedule", label: (workflow ? "Workflow · " : "Schedule · ") + name, runId: each.workflow_run_id ?? undefined },
                agentId: null,
                status: ranSaid(each.latest_status).tone === "live" ? "RUNNING" : null,
                waitingOnYou: false,
                runs: Math.max(1, each.run_count),
            }];
        });
        byPod.set(pod.pod_id, [...chats, ...ran]);
    }
    return byPod;
}

/** Each Lemma's work a day, from the overview's own count: every
 *  conversation of the reader's, not only the latest few it lists. */
export function daysOfRecents(pods: readonly RecentsPod[]): Map<string, DayCount[]> {
    return new Map(pods.map((pod) => [pod.pod_id, (pod.activity ?? []).map((day) => ({ day: day.day, lemma: day.by_agent, people: day.by_people }))]));
}

export type HistoryTone = "live" | "wait" | "bad" | "done" | null;

/** One row of History: a chat, or everything one schedule or workflow did
 *  in the window, said once with a count. */
export interface HistoryRow {
    key: string;
    podId: string;
    podName: string;
    kind: "chat" | "schedule" | "workflow";
    title: string;
    /** What kind of thing it is, as a person would say it. */
    what: string;
    /** How it ended, or how it stands; empty when there is nothing to say. */
    said: string;
    tone: HistoryTone;
    /** How many times it ran in the window; 1 for a chat. */
    count: number;
    /** When it last moved, epoch ms. */
    at: number;
    view: View;
}

/** A run's status as the server names it, in a person's words. A schedule's
 *  fire that its filter turned away is not a failure: it looked, and there
 *  was nothing to do. */
export function ranSaid(status: string): { said: string; tone: HistoryTone } {
    const state = status.toUpperCase();
    if (state === "COMPLETED") return { said: "Done", tone: "done" };
    if (state === "WAITING") return { said: "Waiting on someone", tone: "wait" };
    if (state.includes("FAIL") || state === "ERROR") return { said: "Went wrong", tone: "bad" };
    if (state === "CANCELLED") return { said: "Stopped", tone: null };
    if (state === "FILTERED") return { said: "Nothing to do", tone: null };
    if (["PENDING", "RUNNING", "RECEIVED", "FILTERING", "PROCESSING", "DISPATCHED"].includes(state)) return { said: "Running", tone: "live" };
    return { said: state ? state.charAt(0) + state.slice(1).toLowerCase().replace(/_/g, " ") : "", tone: null };
}

/** What started a chat, for a reader who was not there. */
const STARTED_BY: Readonly<Record<string, string>> = {
    chat: "Chat",
    channel: "Shared chat",
    notification: "It told you something",
    schedule: "Started on a schedule",
    workflow: "Started by a workflow",
    resource: "About something it holds",
    task: "A task it was given",
    ask: "Asked by another Lemma",
};

/** Everything that moved, across the Lemmas given, newest first. A chat opens
 *  itself; a workflow opens its newest run; a schedule opens what its newest
 *  fire started, or the list of schedules when it started nothing. */
export function historyRows(pods: readonly RecentsPod[]): HistoryRow[] {
    const rows: HistoryRow[] = [];
    for (const pod of pods) {
        for (const chat of pod.chats) {
            const running = (chat.status ?? "").toUpperCase() === "RUNNING";
            rows.push({
                key: "c:" + chat.conversation_id,
                podId: pod.pod_id,
                podName: pod.pod_name,
                kind: "chat",
                title: chat.title?.trim() || "A chat with no name",
                what: STARTED_BY[chat.origin] ?? "Chat",
                said: chat.waiting_on_you ? "Waiting on you" : running ? "Working now" : "",
                tone: chat.waiting_on_you ? "wait" : running ? "live" : null,
                count: 1,
                at: Date.parse(chat.last_activity_at) || 0,
                view: chatView({ kind: "chat", podId: pod.pod_id, id: chat.conversation_id }),
            });
        }
        for (const ran of pod.automations) {
            const workflow = ran.kind.toUpperCase() === "WORKFLOW";
            const view: View = ran.workflow_run_id ? { name: "run", podId: pod.pod_id, runId: ran.workflow_run_id }
                : ran.conversation_id ? chatView({ kind: "chat", podId: pod.pod_id, id: ran.conversation_id })
                : outcomeView(pod.pod_id, "automations", workflow ? "workflows" : "schedules");
            rows.push({
                key: (workflow ? "w:" : "s:") + ran.id,
                podId: pod.pod_id,
                podName: pod.pod_name,
                kind: workflow ? "workflow" : "schedule",
                /* The server answers with the name it stores (`friday_number`);
                   a person reads it the way the rest of the app writes it. */
                title: ran.name?.trim() ? readableName(ran.name.trim()) : workflow ? "A workflow" : "A schedule",
                what: workflow ? "Workflow" : "On a schedule",
                ...ranSaid(ran.latest_status),
                count: Math.max(1, ran.run_count),
                at: Date.parse(ran.latest_at) || 0,
                view,
            });
        }
    }
    return rows.sort((a, b) => b.at - a.at);
}

/** History's rows under the day each last moved: Today, Yesterday, then the
 *  day by name. Rows keep their order, so days come newest first. */
export function historyDays(rows: readonly HistoryRow[], now: number): { label: string; rows: HistoryRow[] }[] {
    const startOf = (at: number) => { const day = new Date(at); day.setHours(0, 0, 0, 0); return day.getTime(); };
    const today = startOf(now);
    const days: { key: number; label: string; rows: HistoryRow[] }[] = [];
    for (const row of rows) {
        const key = startOf(row.at);
        let day = days.find((one) => one.key === key);
        if (!day) {
            const ago = Math.round((today - key) / 86_400_000);
            const label = ago <= 0 ? "Today" : ago === 1 ? "Yesterday" : new Date(key).toLocaleDateString("en-US", { weekday: "long", month: "short", day: "numeric" });
            days.push(day = { key, label, rows: [] });
        }
        day.rows.push(row);
    }
    return days.map(({ label, rows: held }) => ({ label, rows: held }));
}

/** History in one sentence: how much moved, and whether anything went wrong. */
export function historySaid(rows: readonly HistoryRow[], days: number): string {
    if (rows.length === 0) return `Nothing has moved in the last ${days} days.`;
    const chats = rows.filter((row) => row.kind === "chat").length;
    const ran = rows.length - chats;
    const wrong = rows.filter((row) => row.tone === "bad").length;
    const parts = [
        chats ? `${chats} ${chats === 1 ? "chat" : "chats"}` : "",
        ran ? `${ran} ${ran === 1 ? "thing" : "things"} that ran by ${ran === 1 ? "itself" : "themselves"}` : "",
    ].filter(Boolean).join(" and ");
    return `${parts} in the last ${days} days.` + (wrong ? ` ${wrong} went wrong.` : "");
}

/** Whether the reader is new to a Lemma somebody else made: in it, and not
 *  yet in a chat with it. That is who it introduces itself to, once. Its
 *  maker met it by making it, and anyone with a chat has met it already. */
export function isNewcomer(who: { madeBy: string | null; me: string | null; chats: number }): boolean {
    return Boolean(who.me) && Boolean(who.madeBy) && who.madeBy !== who.me && who.chats === 0;
}

/** A Lemma's first words to someone new to it, in its own voice. */
export function welcomeSaid(pod: { name: string; description?: string | null }, reader: string, people: number): string {
    /* By their first name when they have given one. Someone with no profile
       yet goes by the start of their address, which is not a name to say. */
    const named = reader && reader !== "You" ? reader.split(" ")[0] : "";
    const first = /^\p{L}+$/u.test(named) ? named : "";
    const others = people - 1;
    return [
        first ? `Hi ${first}. I’m ${pod.name}.` : `Hi. I’m ${pod.name}.`,
        pod.description?.trim() ? `What I’m for: ${pod.description.trim().replace(/[.\s]+$/, "")}.` : "",
        others > 0 ? `${others} other ${others === 1 ? "person works" : "people work"} with me.` : "",
        "Anything waiting for you is listed below, and you can ask me anything in the box at the bottom.",
    ].filter(Boolean).join(" ");
}

/** A page's title: its file's name without the extension. */
export function pageTitle(name: string): string {
    return name.replace(/\.md$/i, "");
}

/** What a Lemma's root holds before anyone puts anything there: dot-names,
 *  the plan file Lemmas started before memory held what they were taught,
 *  its skills, and each person's own folder (`me`), made for them. */
const KEPT_FOR_ITSELF = new Set(["plan.md", "skills", "system", "me"]);

/** Whether a Lemma's root, by name, holds nothing a person would call theirs. */
export function holdsNothing(rootNames: readonly string[]): boolean {
    return rootNames.every((name) => name.startsWith(".") || KEPT_FOR_ITSELF.has(name));
}
