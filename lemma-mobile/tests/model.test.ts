import { describe, expect, it } from "vitest";
import {
    ageOf, chatRows, foldSection, freshQueue, homeCards, needGroups, needItems, ownsOf, parsePlan, sectionRows, sidebarSections, workRows,
} from "../src/data/model";
import { conversationRefOf } from "../src/data/wire";
import { samplePulse, SAMPLE_LEMMAS } from "../src/data/sample";
import { originOf } from "../src/data/origin";
import type { Channel, ConversationRef, NeedItem } from "../src/data/types";

const NOW = new Date(2026, 9, 23, 15, 0, 0).getTime();
const MIN = 60_000;
const HOUR = 60 * MIN;
const DAY = 24 * HOUR;

function conversation(id: string, extra: Partial<ConversationRef> = {}): ConversationRef {
    return { id, title: id, activityAt: new Date(NOW - HOUR).toISOString(), kind: "CHAT", boundTo: null, origin: { kind: "chat", label: "Chat" }, agentId: null, status: "IDLE", waitingOnYou: false, ...extra };
}

describe("a conversation that has never run", () => {
    it("is not waiting on anyone, whatever its status says", () => {
        const fresh = conversationRefOf({ id: "c1", status: "WAITING", has_pending_interaction: true, last_activity_at: "2026-10-23T10:00:00Z" });
        expect(fresh.waitingOnYou).toBe(false);
    });

    it("waits on the reader once a run has paused it", () => {
        const paused = conversationRefOf({ id: "c1", status: "WAITING", has_pending_interaction: true, last_run_status: "COMPLETED", last_run_finished_at: "2026-10-23T10:00:00Z" });
        expect(paused.waitingOnYou).toBe(true);
    });

    it("is not waiting just because its status is WAITING", () => {
        const idle = conversationRefOf({ id: "c1", status: "WAITING", has_pending_interaction: false, last_run_status: "COMPLETED" });
        expect(idle.waitingOnYou).toBe(false);
    });
});

describe("the queue", () => {
    const lemma = { id: "p1", name: "Support" };

    it("is one item per conversation, aged by its oldest pause", () => {
        const items = needItems(lemma, [
            { conversation_id: "c1", kind: "APPROVAL", summary: "Refund $240", created_at: new Date(NOW - 2 * HOUR).toISOString(), tool_call_id: "t1" },
            { conversation_id: "c1", kind: "QUESTION", summary: "Later one", created_at: new Date(NOW - HOUR).toISOString(), tool_call_id: "t2" },
        ], [], [], NOW);
        expect(items).toHaveLength(1);
        expect(items[0]).toMatchObject({ kind: "APPROVAL", title: "Refund $240", toolCallId: "t1", sinceMs: NOW - 2 * HOUR });
    });

    it("says a contact waiting on a person as a reply to them", () => {
        const contact: Channel = {
            id: "ch1", kind: "CONTACT", title: null, topic: null, person: { kind: "CONTACT", id: "x", name: "Maria Chen" }, sources: [], assignee: null,
            agentsAnswer: false, status: "open", attention: null, archived: false, lastLine: null, lastMessageAt: new Date(NOW - HOUR).toISOString(),
            createdAt: new Date(NOW - DAY).toISOString(), unread: { lines: 0, mentions: 0, waiting: 0 },
        };
        const [item] = needItems(lemma, [], [], [contact], NOW);
        expect(item).toMatchObject({ kind: "CONTACT", title: "Reply to Maria Chen", channelId: "ch1" });
        const assigned = needItems(lemma, [], [], [{ ...contact, assignee: { kind: "MEMBER", id: "m", name: "Sam" } }], NOW);
        expect(assigned).toHaveLength(0);
    });

    it("drops what has waited more than a week", () => {
        const item = (key: string, ago: number): NeedItem => ({ key, kind: "QUESTION", podId: "p", podName: "P", title: key, detail: null, sinceMs: NOW - ago, conversationId: key, toolCallId: null, workflowRunId: null, channelId: null });
        expect(freshQueue([item("old", 8 * DAY), item("new", HOUR)], NOW).map((one) => one.key)).toEqual(["new"]);
    });

    it("puts what is behind first on Home, then the newest", () => {
        const item = (key: string, podId: string, ago: number): NeedItem => ({ key, kind: "QUESTION", podId, podName: podId, title: key, detail: null, sinceMs: NOW - ago, conversationId: key, toolCallId: null, workflowRunId: null, channelId: null });
        const cards = homeCards([item("a", "p", 12 * MIN), item("b", "p", 2 * HOUR), item("c", "p", 3 * HOUR), item("late", "behind", DAY)], new Set(["behind"]));
        expect(cards.map((one) => one.key)).toEqual(["a", "b", "late"]);
    });

    it("groups the list behind, today, earlier", () => {
        const item = (key: string, podId: string, ago: number): NeedItem => ({ key, kind: "QUESTION", podId, podName: podId, title: key, detail: null, sinceMs: NOW - ago, conversationId: key, toolCallId: null, workflowRunId: null, channelId: null });
        const groups = needGroups([item("today", "p", HOUR), item("earlier", "p", DAY), item("late", "behind", 2 * DAY)], new Set(["behind"]), NOW);
        expect(groups.behind.map((one) => one.key)).toEqual(["late"]);
        expect(groups.today.map((one) => one.key)).toEqual(["today"]);
        expect(groups.earlier.map((one) => one.key)).toEqual(["earlier"]);
    });
});

describe("a Lemma's rows", () => {
    it("counts an automation's runs on one row, latest first", () => {
        const meta = { source: "WORKFLOW_RUN", workflow_run_id: "r" };
        const runs = [0, 1, 2].map((index) => conversation("run" + index, {
            title: "Workflow run: chase_unanswered_tickets",
            origin: { ...originOf(meta, "TASK"), runId: "r" + index },
            activityAt: new Date(NOW - (index + 1) * HOUR).toISOString(),
            status: index === 0 ? "RUNNING" : "IDLE",
        }));
        const rows = workRows({ podId: "p", conversations: runs, tables: [], now: NOW });
        expect(rows).toHaveLength(1);
        expect(rows[0]).toMatchObject({ title: "Chase Unanswered Tickets", runs: 3, running: true, kind: "automation" });
    });

    it("lists a person's own line only while something in it is new", () => {
        const line: Channel = {
            id: "l", kind: "DIRECT", title: null, topic: null, person: null, sources: [{ platform: "WHATSAPP", title: null }], assignee: null, agentsAnswer: true,
            status: null, attention: null, archived: false, lastLine: null, lastMessageAt: new Date(NOW - HOUR).toISOString(), createdAt: new Date(NOW - DAY).toISOString(),
            unread: { lines: 0, mentions: 0, waiting: 0 },
        };
        expect(sectionRows(chatRows("p", [], [line]), [])).toHaveLength(0);
        const rows = sectionRows(chatRows("p", [], [{ ...line, unread: { lines: 2, mentions: 0, waiting: 0 } }]), []);
        expect(rows[0]).toMatchObject({ title: "WhatsApp", kind: "line", unread: 2 });
    });

    it("keeps what waits on the reader above the fold", () => {
        const rows = [1, 2, 3, 4, 5].map((n) => ({ needsYou: n === 5, n }));
        const { shown, hidden } = foldSection(rows, 3);
        expect(shown.map((row) => row.n)).toEqual([1, 2, 3, 5]);
        expect(hidden).toBe(1);
    });

    it("folds a Lemma quiet for a week", () => {
        const sections = sidebarSections([
            { lemma: { id: "a", name: "A" }, rows: [{ at: NOW - HOUR, needsYou: false }] },
            { lemma: { id: "b", name: "B" }, rows: [{ at: NOW - 9 * DAY, needsYou: false }] },
        ], NOW, new Map());
        expect(sections.map((one) => [one.lemma.id, one.open])).toEqual([["a", true], ["b", false]]);
    });
});

describe("the plan", () => {
    it("reads front matter", () => {
        const plan = parsePlan("---\noutcome: Every customer hears back within a day\nkind: week\ntarget: 90%\nunit: \"%\"\nmeasure: SELECT 1\n---\n\n## What must be true\n- Something\n");
        expect(plan).toMatchObject({ outcome: "Every customer hears back within a day", kind: "week", target: "90%", unit: "%", measure: "SELECT 1" });
    });

    it("keeps an unquoted due date as the text it was written as", () => {
        const plan = parsePlan("---\noutcome: The launch ships\nkind: date\ndue: 2026-10-28\ntarget: 22\n---\n");
        expect(plan.due).toBe("2026-10-28");
    });

    it("falls back to the first line that must be true", () => {
        const plan = parsePlan("<!-- preamble -->\n\n## What must be true\n- Every renewal is confirmed a week ahead — Lemma, Oct 14\n\n## Never\n- Discount without asking\n");
        expect(plan.outcome).toBe("Every renewal is confirmed a week ahead");
    });

    it("says nothing is owned when there is no plan", () => {
        expect(ownsOf(null, null, NOW).sentence).toBeNull();
    });

    it("says behind when a weekly bar is missed", () => {
        const owns = ownsOf(parsePlan("---\noutcome: Invoices are paid within 30 days\nkind: week\ntarget: 90%\nunit: \"%\"\nmeasure: SELECT 88\n---\n"), 88, NOW);
        expect(owns).toMatchObject({ sentence: "invoices are paid within 30 days", display: "88%", standing: "behind" });
        expect(owns.fill).toBeCloseTo(0.88);
        expect(owns.mark).toBeCloseTo(0.9);
    });
});

describe("ages", () => {
    it("reads short, as the canvas does", () => {
        expect(ageOf(NOW - 12 * MIN, NOW)).toBe("12m");
        expect(ageOf(NOW - 2 * HOUR, NOW)).toBe("2h");
        expect(ageOf(NOW - 26 * HOUR, NOW)).toBe("1d");
    });
});

describe("sample data", () => {
    it("is shaped like the canvas: fourteen things need you", () => {
        const pulse = samplePulse(NOW);
        const needs = freshQueue(pulse.flatMap((one) => needItems(one.lemma, one.asks, one.waits, one.contacts, NOW)), NOW);
        expect(needs).toHaveLength(14);
        const kinds = needs.reduce<Record<string, number>>((counts, item) => ({ ...counts, [item.kind]: (counts[item.kind] ?? 0) + 1 }), {});
        expect(kinds).toEqual({ APPROVAL: 4, QUESTION: 5, CONTACT: 3, FORM: 2 });
        expect(SAMPLE_LEMMAS.map((one) => one.name)).toEqual(["Support", "Launch", "Renewals", "Hiring", "Books"]);
    });

    it("folds Hiring and Books as quiet", () => {
        const pulse = samplePulse(NOW);
        const sections = sidebarSections(pulse.map((one) => ({
            lemma: one.lemma,
            rows: sectionRows(chatRows(one.lemma.id, one.conversations, one.channels), workRows({ podId: one.lemma.id, conversations: one.conversations, tables: one.tables, now: NOW })),
        })), NOW, new Map());
        const quiet = sections.filter((one) => !one.open).map((one) => one.lemma.name);
        expect(quiet).toEqual(["Books"]);
        const support = sections.find((one) => one.lemma.name === "Support");
        expect(foldSection(support?.rows ?? [], 3).shown.map((row) => row.title).slice(0, 3)).toEqual(["Refund for a broken order", "WhatsApp", "Chase Unanswered Tickets"]);
    });
});
