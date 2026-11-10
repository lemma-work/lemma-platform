/** Reading the API's rows into the app's shapes. Pure: no client, no
 *  native module, so the tests read it directly. */

import { originOf } from "./origin";
import type { Automation, Channel, ConversationRef, DayCount, NeedItem, NeedKind, PersonRef } from "./types";

export type Wire = Record<string, unknown>;

export function record(value: unknown): Wire {
    return value && typeof value === "object" && !Array.isArray(value) ? (value as Wire) : {};
}

export function rows(value: unknown): unknown[] {
    return Array.isArray(value) ? value : [];
}

export function items(listed: unknown): unknown[] {
    return rows(record(listed).items);
}

export function words(value: unknown): string | null {
    return typeof value === "string" && value.trim() ? value.trim() : null;
}

export function count(value: unknown): number {
    return typeof value === "number" && Number.isFinite(value) && value > 0 ? value : 0;
}

/** One conversation as the lists draw it (`conversationRefOf`).
 *
 *  `has_pending_interaction` is what says it waits on the reader; the
 *  status alone cannot, since a conversation that has never run reports
 *  WAITING exactly as one paused on an ask does. So a conversation with no
 *  run behind it (`last_run_status` and `last_run_finished_at` both empty)
 *  never counts as waiting, whatever its flag or status say. */
export function conversationRefOf(raw: unknown): ConversationRef {
    const row = record(raw);
    const metadata = record(row.metadata);
    const at = words(row.last_activity_at) ?? words(row.updated_at) ?? words(row.created_at);
    const bound = metadata.lemma_resource;
    const hasRun = Boolean(words(row.last_run_status) || words(row.last_run_finished_at)) || words(row.status)?.toUpperCase() === "RUNNING";
    return {
        id: String(row.id),
        title: words(row.title) ?? "Untitled",
        activityAt: at,
        kind: words(row.type) ?? "CHAT",
        boundTo: typeof bound === "string" ? bound : null,
        origin: originOf(metadata, words(row.type)),
        agentId: words(row.agent_id),
        status: words(row.status),
        waitingOnYou: hasRun && row.has_pending_interaction === true,
    };
}

export function readPerson(raw: unknown): PersonRef | null {
    if (!raw || typeof raw !== "object") return null;
    const row = record(raw);
    const kind = row.kind === "CONTACT" || row.kind === "AGENT" ? row.kind : "MEMBER";
    return { kind, id: words(row.id) ?? "", name: words(row.name) };
}

export const SOMEBODY: PersonRef = { kind: "MEMBER", id: "", name: null };

/** `readChannel` from `channels/model.ts`, the parts the lists read. */
export function readChannel(raw: unknown): Channel | null {
    const row = record(raw);
    const id = words(row.id);
    if (!id) return null;
    const attention = row.attention && typeof row.attention === "object" ? record(row.attention) : null;
    const last = row.last_line && typeof row.last_line === "object" ? record(row.last_line) : null;
    const unread = record(row.unread);
    const kind = row.kind === "DIRECT" || row.kind === "CONTACT" ? row.kind : "GROUP";
    const status = row.status === "open" || row.status === "waiting_on_contact" || row.status === "closed" ? row.status : null;
    return {
        id,
        kind,
        title: words(row.title),
        topic: words(row.topic),
        person: readPerson(row.person),
        sources: rows(row.sources).flatMap((entry) => {
            const platform = words(record(entry).platform);
            return platform ? [{ platform: platform.toUpperCase(), title: words(record(entry).title) }] : [];
        }),
        assignee: readPerson(row.assignee),
        agentsAnswer: row.agents_answer !== false,
        status,
        attention: attention ? { since: String(attention.since ?? ""), reason: words(attention.reason) } : null,
        archived: row.archived === true,
        lastLine: last ? { sender: readPerson(last.sender) ?? SOMEBODY, text: typeof last.text === "string" ? last.text : "", createdAt: String(last.created_at ?? "") } : null,
        lastMessageAt: words(row.last_line_at),
        createdAt: String(row.created_at ?? ""),
        unread: { lines: count(unread.lines), mentions: count(unread.mentions), waiting: count(unread.waiting) },
    };
}


/** An ask_user call's arguments, flat. A real call is stored flat
 *  (`{questions}`: pydantic-ai flattens a single model parameter); older and
 *  scripted ones wrap it (`{request: {questions}}`). The backend accepts both
 *  (`ask_user_request_dict`), so the app does too. */
export function flatAskArgs(args: unknown): unknown {
    const outer = record(args);
    const inner = outer.request;
    return inner && typeof inner === "object" && !Array.isArray(inner) && !Array.isArray(outer.questions) ? inner : args;
}

/* ── the organization's reads ─────────────────────────────────────────── */

/** What started a recent chat (`ListedOrigin`), as the conversation's own
 *  metadata would say it, so `originOf` names it the same everywhere. */
function listedMetadata(origin: string | null, platform: string | null, channel: string | null): Wire {
    switch (origin) {
        case "channel": return { source: "agent_surfaces", surface_platform: platform, channel_name: channel };
        case "notification": return { source: "notification", surface_platform: platform };
        case "schedule": return { source: "SCHEDULE" };
        case "workflow": return { source: "WORKFLOW_RUN" };
        case "ask": return { source: "pod_ask" };
        case "resource": return { lemma_resource: "resource" };
        default: return {};
    }
}

/** One of `recents`' chats as the lists draw it. Its flag is the server's
 *  own word that it waits on the reader. */
export function recentChatOf(raw: unknown): ConversationRef | null {
    const row = record(raw);
    const id = words(row.conversation_id);
    if (!id) return null;
    const origin = words(row.origin);
    const type = origin === "task" ? "TASK" : "CHAT";
    return {
        id,
        title: words(row.title) ?? "Untitled",
        activityAt: words(row.last_activity_at),
        kind: type,
        boundTo: origin === "resource" ? "resource" : null,
        origin: originOf(listedMetadata(origin, words(row.platform), words(row.channel_name)), type),
        agentId: words(row.agent_id),
        status: words(row.status),
        waitingOnYou: row.waiting_on_you === true,
    };
}

/** One of `recents`' automations. */
export function readAutomation(raw: unknown): Automation | null {
    const row = record(raw);
    const id = words(row.id);
    const latestAt = words(row.latest_at);
    if (!id || !latestAt) return null;
    return {
        id,
        kind: row.kind === "SCHEDULE" ? "SCHEDULE" : "WORKFLOW",
        name: words(row.name),
        latestAt,
        running: words(row.latest_status)?.toUpperCase() === "RUNNING",
        runs: Math.max(1, count(row.run_count)),
        workflowRunId: words(row.workflow_run_id),
        conversationId: words(row.conversation_id),
    };
}

export function readDayCount(raw: unknown): DayCount | null {
    const row = record(raw);
    const day = words(row.day);
    return day ? { day, byAgent: count(row.by_agent), byPeople: count(row.by_people) } : null;
}

const NEED_KINDS = new Set<NeedKind>(["QUESTION", "APPROVAL", "PERMISSION", "FORM", "DECISION", "CONTACT", "MESSAGE"]);

/** One item of `needsYou`, in the queue's words. */
export function needOf(raw: unknown, now: number): NeedItem | null {
    const row = record(raw);
    const key = words(row.key);
    const podId = words(row.pod_id);
    const kind = words(row.kind) as NeedKind | null;
    if (!key || !podId || !kind || !NEED_KINDS.has(kind)) return null;
    const since = Date.parse(words(row.since) ?? "");
    const said = words(row.title);
    const detail = words(row.detail);
    /* A contact's title is their name; the queue says what to do. */
    const title = kind === "CONTACT" ? `Reply to ${said ?? "a contact"}` : said ?? detail ?? "A question for you";
    return {
        key,
        kind,
        podId,
        podName: words(row.pod_name) ?? "A Lemma",
        title,
        detail: kind === "CONTACT" ? detail ?? "Waiting on a person" : said ? detail : null,
        sinceMs: Number.isNaN(since) ? now : since,
        conversationId: words(row.conversation_id),
        toolCallId: words(row.tool_call_id),
        workflowRunId: words(row.workflow_run_id),
        channelId: words(row.channel_id),
        messageId: words(row.message_id),
        choices: rows(row.choices).flatMap((choice) => (words(choice) ? [words(choice) as string] : [])),
    };
}
