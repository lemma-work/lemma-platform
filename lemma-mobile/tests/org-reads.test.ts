import { describe, expect, it, vi } from "vitest";
import { automationRow, dayOf, filterOf, hrefOfNeed, ownsOf, planOfOutcome, ranOnTheirOwn, workRows } from "../src/data/model";
import { needOf, readAutomation, recentChatOf } from "../src/data/wire";
import { sampleSource } from "../src/data/sample";

/** The organization's reads (#1045, #1046): what waits on you, what moved
 *  lately, its apps and when Home was last looked at, each one request; and
 *  what a Lemma owns, kept on the Lemma. */

const calls: { name: string; args: unknown[] }[] = [];
const said = (name: string, answer: unknown) => async (...args: unknown[]) => {
    calls.push({ name, args });
    return answer;
};
/* A server from before the organization's reads: each of them answers 404. */
let older = false;
const orgRead = (name: string, answer: unknown) => async (...args: unknown[]) => {
    calls.push({ name, args });
    if (older) throw Object.assign(new Error("Not Found"), { statusCode: 404 });
    return answer;
};

const RECENTS = {
    organization_id: "org",
    days: 14,
    since: "2026-10-28T00:00:00Z",
    timezone: "UTC",
    pods: [{
        pod_id: "p1",
        pod_name: "Support",
        chats: [
            { conversation_id: "c1", title: "Refunds", origin: "chat", last_activity_at: "2026-11-10T09:00:00Z", waiting_on_you: true, status: "WAITING" },
            { conversation_id: "c2", title: "Thread", origin: "channel", platform: "SLACK", channel_name: "support", last_activity_at: "2026-11-10T08:00:00Z", waiting_on_you: false },
        ],
        automations: [{ id: "w1", kind: "WORKFLOW", name: "triage_new_tickets", latest_at: "2026-11-10T09:30:00Z", latest_run_id: "r9", latest_status: "RUNNING", run_count: 28, workflow_run_id: "r9" }],
        activity: [{ day: "2026-11-09", by_agent: 4, by_people: 1 }, { day: "2026-11-10", by_agent: 3, by_people: 2 }],
    }],
};

const NEEDS = {
    items: [
        { key: "message:l1", kind: "MESSAGE", pod_id: "p1", pod_name: "Support", title: "Send the note?", since: "2026-11-10T09:00:00Z", channel_id: "ch1", message_id: "l1", choices: ["Send it", "Not yet"] },
        { key: "contact:ch2", kind: "CONTACT", pod_id: "p1", pod_name: "Support", title: "Maria Chen", detail: "Wants a person", since: "2026-11-10T08:00:00Z", channel_id: "ch2" },
        { key: "conversation:c1", kind: "APPROVAL", pod_id: "p1", pod_name: "Support", title: "Refund $240", detail: "Refunds", since: "2026-11-10T07:00:00Z", conversation_id: "c1", tool_call_id: "t1" },
        { key: "odd", kind: "SOMETHING_NEW", pod_id: "p1", pod_name: "Support", since: "2026-11-10T07:00:00Z" },
    ],
};

function fakeClient(podId?: string) {
    return {
        organizations: {
            recents: orgRead("recents", RECENTS),
            needsYou: orgRead("needsYou", NEEDS),
            apps: orgRead("apps", { items: [{ id: "a1", name: "ticket-queue", pod_id: "p1", pod_name: "Support", status: "ACTIVE", updated_at: "2026-11-09T00:00:00Z", visibility: "POD" }] }),
            homeSeen: { get: orgRead("homeSeen.get", { seen_at: "2026-11-09T22:00:00Z" }), mark: orgRead("homeSeen.mark", {}) },
        },
        conversations: {
            list: said(`conversations.list:${podId}`, { items: [{ id: "c7", title: "Old chat", last_activity_at: "2026-11-10T08:00:00Z", metadata: {} }] }),
            pendingInteractions: said(`pending:${podId}`, { items: [{ conversation_id: "c7", conversation_title: "Old chat", kind: "QUESTION", summary: "Which day?", created_at: "2026-11-10T08:00:00Z", tool_call_id: "t7" }] }),
        },
        workflows: { runs: { waitingAssignedToMe: said(`waits:${podId}`, { items: [] }) }, list: said(`workflows:${podId}`, { items: [] }) },
        apps: { list: said(`apps.list:${podId}`, { items: [{ id: "a9", name: "old-board" }] }) },
        channels: {
            list: said(`channels.list:${podId}`, { items: [] }),
            answer: said("channels.answer", {}),
        },
        tables: { list: said(`tables.list:${podId}`, { items: [{ name: "tickets", updated_at: "2026-11-10T00:00:00Z" }] }) },
        pods: {
            outcome: {
                standing: said("standing", {
                    outcome: { sentence: "Every customer hears back within a day", kind: "week", target: 90, unit: "%", measure: "select 1" },
                    value: 88,
                }),
            },
        },
    };
}

vi.mock("../src/session/client", () => ({ lemma: (_server: unknown, podId?: string) => fakeClient(podId) }));
vi.mock("../src/data/io", () => ({ readText: async () => null }));
const held = new Map<string, string>();
vi.mock("../src/session/store", () => ({ store: { homeSeen: async (org: string) => (held.has(org) ? Number(held.get(org)) : null), setHomeSeen: async (org: string, at: number) => void held.set(org, String(at)) } }));

const SERVER = { id: "s", api: "https://api.example", web: "https://example" } as never;
const LEMMAS = [{ id: "p1", name: "Support", iconUrl: null, description: null }];

describe("the organization's reads, live", () => {
    it("reads what moved lately once, and only chats with people and tables per Lemma", async () => {
        const { liveSource } = await import("../src/data/live");
        calls.length = 0;
        const [pulse] = await liveSource(SERVER).pulse("org", LEMMAS);
        expect(calls.map((call) => call.name).sort()).toEqual(["channels.list:p1", "recents", "tables.list:p1"]);
        expect(pulse.conversations.map((one) => [one.id, one.waitingOnYou])).toEqual([["c1", true], ["c2", false]]);
        expect(pulse.conversations[1].origin).toMatchObject({ kind: "channel", platform: "SLACK" });
        expect(pulse.automations).toEqual([expect.objectContaining({ id: "w1", runs: 28, running: true, workflowRunId: "r9" })]);
        expect(pulse.activity).toHaveLength(2);
    });

    it("reads what waits on you in one request, and leaves out a kind it does not know", async () => {
        const { liveSource } = await import("../src/data/live");
        const needs = await liveSource(SERVER).needs("org", LEMMAS);
        expect(needs.map((item) => item.kind)).toEqual(["MESSAGE", "CONTACT", "APPROVAL"]);
        expect(needs[1]).toMatchObject({ title: "Reply to Maria Chen", detail: "Wants a person", channelId: "ch2" });
    });

    it("answers a question asked in a chat with the choice picked, on its line", async () => {
        const { liveSource } = await import("../src/data/live");
        const source = liveSource(SERVER);
        const [message] = await source.needs("org", LEMMAS);
        const card = await source.card(message);
        expect(card.answer).toEqual({ kind: "choose", questionId: "choice", options: ["Send it", "Not yet"] });
        calls.length = 0;
        await source.answer(message, "APPROVE_ONCE", { choice: "Send it" });
        expect(calls).toEqual([{ name: "channels.answer", args: ["p1", "ch1", "l1", { choice: "Send it" }] }]);
    });

    it("says what a Lemma owns from its standing, and keeps Home's last look on the server", async () => {
        const { liveSource } = await import("../src/data/live");
        const source = liveSource(SERVER);
        const owns = await source.owns(LEMMAS[0]);
        expect(owns).toMatchObject({ sentence: "every customer hears back within a day", display: "88%", standing: "behind" });
        expect(await source.homeSeen("org")).toBe(Date.parse("2026-11-09T22:00:00Z"));
        calls.length = 0;
        await source.markHomeSeen("org", Date.parse("2026-11-10T10:00:00Z"));
        expect(calls).toEqual([{ name: "homeSeen.mark", args: ["org", "2026-11-10T10:00:00.000Z"] }]);
        const [app] = await source.apps("org", LEMMAS);
        expect(app).toMatchObject({ podId: "p1", podName: "Support", label: "Ticket Queue" });
    });
});

describe("a server from before the organization's reads", () => {
    it("reads each Lemma as it used to, and stops asking the organization once it has said 404", async () => {
        const { liveSource } = await import("../src/data/live");
        older = true;
        try {
            const source = liveSource(SERVER);
            calls.length = 0;
            const needs = await source.needs("org", LEMMAS);
            expect(needs).toEqual([expect.objectContaining({ kind: "QUESTION", title: "Which day?", conversationId: "c7", messageId: null, choices: [] })]);
            const [pulse] = await source.pulse("org", LEMMAS);
            expect(pulse).toMatchObject({ automations: null, activity: null });
            expect(pulse.conversations.map((one) => one.id)).toEqual(["c7"]);
            expect((await source.apps("org", LEMMAS)).map((app) => app.name)).toEqual(["old-board"]);
            await source.markHomeSeen("org", 1234);
            expect(await source.homeSeen("org")).toBe(1234);
            expect(calls.filter((call) => call.name === "needsYou" || call.name === "recents" || call.name.startsWith("homeSeen") || call.name === "apps")).toHaveLength(1);
        } finally {
            older = false;
        }
    });
});

describe("reading the organization's rows", () => {
    it("keeps a MESSAGE's line and choices, and files it under questions", () => {
        const item = needOf(NEEDS.items[0], 0);
        expect(item).toMatchObject({ kind: "MESSAGE", channelId: "ch1", messageId: "l1", choices: ["Send it", "Not yet"] });
        expect(filterOf("MESSAGE")).toBe("questions");
        expect(hrefOfNeed(item!)).toBe("/shared/p1/ch1");
    });

    it("draws a recent chat and an automation as the rows did", () => {
        expect(recentChatOf({ conversation_id: "c3", origin: "schedule", last_activity_at: "2026-11-10T00:00:00Z", waiting_on_you: false })?.origin.kind).toBe("schedule");
        expect(recentChatOf({ title: "no id" })).toBeNull();
        const automation = readAutomation({ id: "s1", kind: "SCHEDULE", name: "morning-digest", latest_at: "2026-11-10T06:00:00Z", latest_status: "COMPLETED", run_count: 1, conversation_id: "c9" })!;
        expect(automationRow("p1", automation)).toMatchObject({ title: "Morning Digest", said: "Runs on a schedule", href: "/chat/p1/c9", runs: 1, running: false });
        const rows = workRows({ podId: "p1", conversations: [], automations: [automation], tables: [], now: Date.parse("2026-11-10T12:00:00Z") });
        expect(rows.map((row) => row.key)).toEqual(["auto:s1"]);
    });

    it("counts what ran on its own from the day the reader last looked", () => {
        const days = [{ day: "2026-11-08", byAgent: 9, byPeople: 0 }, { day: "2026-11-09", byAgent: 4, byPeople: 1 }, { day: "2026-11-10", byAgent: 3, byPeople: 2 }];
        const lastNight = new Date(2026, 10, 9, 22).getTime();
        expect(dayOf(lastNight)).toBe("2026-11-09");
        expect(ranOnTheirOwn(days, lastNight)).toBe(7);
    });

    it("reads a finish line kept on the Lemma as the plan's fields", () => {
        const plan = planOfOutcome({ sentence: "The launch ships", kind: "date", target: 22, unit: "steps", due: "2026-11-28" });
        expect(plan).toEqual({ outcome: "The launch ships", kind: "date", target: "22", due: "2026-11-28", measure: null, unit: "steps" });
        expect(ownsOf(plan, 14, new Date(2026, 10, 10).getTime())).toMatchObject({ display: "14", note: "Aiming for 22, 18 days left." });
    });
});

describe("the sample's queue", () => {
    it("acts out a question asked in a chat, and drops it once answered", async () => {
        vi.useFakeTimers();
        const source = sampleSource();
        const needs = await source.needs("sample-acme", []);
        const message = needs.find((item) => item.kind === "MESSAGE");
        expect(message).toMatchObject({ channelId: "support-line", choices: ["Send it", "Not yet"] });
        const card = await source.card(message!);
        expect(card.answer.kind).toBe("choose");
        const answered = source.answer(message!, "APPROVE_ONCE", { choice: "Send it" });
        await vi.runAllTimersAsync();
        await answered;
        expect((await source.needs("sample-acme", [])).some((item) => item.key === message!.key)).toBe(false);
        vi.useRealTimers();
    });
});
