import test from "node:test";
import assert from "node:assert/strict";
import {
    NO_CHAT,
    ageOf,
    chatItems,
    chatView,
    dailyActivity,
    draftPlan,
    foldSection,
    sectionRows,
    workRows,
    inSidebar,
    lineTitle,
    repeatedTitles,
    sidebarSections,
    QUIET_AFTER_MS,
    isChat,
    outcomeView,
    parsePlan,
    queueItems,
    readMeasure,
    readView,
    sameOpen,
    serializePlan,
    standingOf,
    viewOfAddress,
    viewOfItem,
    workSince,
    writeView,
    type Plan,
    type View,
} from "../src/outcomes/model.ts";
import type { ConversationRef } from "../src/data/types.ts";
import type { Channel } from "../src/channels/model.ts";
import type { AskedRow } from "../src/thread/waiting-on-you.ts";
import type { WaitingRow } from "../src/workflow/waiting-inbox.tsx";

function conversation(id: string, activityAt: string | null, kind?: string): ConversationRef {
    return {
        id,
        title: id,
        at: "display only",
        activityAt,
        kind: "CHAT",
        ...(kind ? { origin: { kind: kind as NonNullable<ConversationRef["origin"]>["kind"], label: kind } } : {}),
    };
}

test("every view writes an address that reads back as itself", () => {
    const views: View[] = [
        { name: "today" },
        { name: "outcomes" },
        { name: "needs" },
        { name: "people" },
        { name: "hire" },
        { name: "chats", open: NO_CHAT },
        { name: "chats", open: { kind: "chat", podId: "pod one/2", id: "new" } },
        { name: "chats", open: { kind: "chat", podId: "p1", id: "c 9" } },
        { name: "chats", open: { kind: "shared", podId: "p1", id: "ch/1" } },
        { name: "chats", open: { kind: "contact", podId: "abc", id: "contact 7" } },
        outcomeView("abc", "contacts"),
        outcomeView("pod one/2"),
        outcomeView("abc", "xray"),
        outcomeView("abc", "apps"),
        outcomeView("abc", "plan"),
        outcomeView("abc", "settings"),
        outcomeView("abc", "settings", "schedules"),
        { name: "agent", podId: "abc", agent: "researcher" },
        { name: "record", podId: "abc", table: "leads", recordId: "r 9" },
        { name: "run", podId: "abc", runId: "r/1" },
        { name: "workflow", podId: "abc", workflow: "late-fees" },
        { name: "app", podId: "abc", app: "crm" },
        { name: "table", podId: "abc", table: "leads" },
        { name: "file", podId: "abc", path: "/reports/Q3 review.md" },
    ];
    for (const view of views) assert.deepEqual(readView(writeView(view)), view, writeView(view));
});

test("an address the frame cannot read lands somewhere, not nowhere", () => {
    assert.deepEqual(readView("/o/nonsense"), { name: "today" });
    assert.deepEqual(readView("/t/abc"), { name: "today" });
    assert.deepEqual(readView("/o/outcome/abc/whatever"), outcomeView("abc"));
    assert.deepEqual(readView("/o/outcome/%E0%A4%A"), { name: "today" });
    assert.deepEqual(readView("/o/outcome/abc/run"), outcomeView("abc"), "a run with no id is the outcome");
    assert.deepEqual(readView("/o/chats/p1/c/%E0%A4%A"), { name: "chats", open: NO_CHAT });
    assert.deepEqual(readView("/o/chats/p1/x/y"), { name: "chats", open: NO_CHAT }, "an unknown kind of chat opens none");
});

test("a link to an outcome's old Chats tab opens the chat it named", () => {
    assert.deepEqual(readView("/o/outcome/p1/chats/c/c1"), chatView({ kind: "chat", podId: "p1", id: "c1" }));
    assert.deepEqual(readView("/o/outcome/p1/chats/g/g1"), chatView({ kind: "shared", podId: "p1", id: "g1" }));
    assert.deepEqual(readView("/o/outcome/p1/chats/customers"), outcomeView("p1"));
    assert.deepEqual(readView("/o/chats/p1/customers"), outcomeView("p1", "contacts"), "the old customers queue is the Contacts tab");
    assert.deepEqual(readView("/o/chats/p1/customers/c9"), chatView({ kind: "contact", podId: "p1", id: "c9" }));
    assert.deepEqual(readView("/o/outcome/p1/chats"), outcomeView("p1"), "with no chat named, the outcome itself");
});

test("what waits on you opens its chat, or the run whose form it is", () => {
    assert.deepEqual(viewOfItem({ podId: "p1", conversationId: "c1", runId: null, contactId: null }), { name: "chats", open: { kind: "chat", podId: "p1", id: "c1" } });
    assert.deepEqual(viewOfItem({ podId: "p1", conversationId: null, runId: "r1", contactId: null }), { name: "run", podId: "p1", runId: "r1" });
    assert.deepEqual(viewOfItem({ podId: "p1", conversationId: null, runId: null, contactId: null }), outcomeView("p1"));
});

test("a workspace link followed inside the frame stays in the frame where it can", () => {
    const address = (tabId: string | null, conversationId: string | null = null) => ({ podId: "p1", tabId, conversationId });
    assert.deepEqual(viewOfAddress(address("conversation", "c1")), chatView({ kind: "chat", podId: "p1", id: "c1" }));
    assert.deepEqual(viewOfAddress(address("channel:g1")), chatView({ kind: "shared", podId: "p1", id: "g1" }));
    assert.deepEqual(viewOfAddress(address("run:r1")), { name: "run", podId: "p1", runId: "r1" });
    assert.deepEqual(viewOfAddress(address("workflow:late-fees")), { name: "workflow", podId: "p1", workflow: "late-fees" });
    assert.deepEqual(viewOfAddress(address("file:/me/notes.md")), { name: "file", podId: "p1", path: "/me/notes.md" });
    assert.deepEqual(viewOfAddress(address("space:contacts")), outcomeView("p1", "contacts"));
    assert.deepEqual(viewOfAddress(address("space:chats")), outcomeView("p1"), "an outcome's chats are under its name in the sidebar");
    assert.deepEqual(viewOfAddress(address(null)), outcomeView("p1"));
    assert.deepEqual(viewOfAddress({ podId: "p1", tabId: "space:about", conversationId: null, section: "schedules" }), outcomeView("p1", "settings", "schedules"));
    assert.deepEqual(viewOfAddress(address("bot:researcher")), { name: "agent", podId: "p1", agent: "researcher" });
    assert.deepEqual(viewOfAddress(address("record:leads:42")), { name: "record", podId: "p1", table: "leads", recordId: "42" });
    assert.deepEqual(viewOfAddress(address("space:workflows")), outcomeView("p1", "xray"));
    assert.equal(viewOfAddress(address("computer")), null, "what the frame does not show goes to the workspace");
    assert.equal(viewOfAddress({ podId: null, tabId: null, conversationId: null }), null);
});

test("work is counted per local day, split by who started it", () => {
    const now = new Date(2026, 9, 26, 12, 0, 0);
    const hourAgo = new Date(now.getTime() - 3_600_000).toISOString();
    const threeDaysAgo = new Date(2026, 9, 23, 10, 0, 0).toISOString();
    const days = dailyActivity([
        conversation("a", hourAgo, "schedule"),
        conversation("b", hourAgo, "chat"),
        conversation("c", hourAgo),
        conversation("d", threeDaysAgo, "workflow"),
        conversation("e", new Date(2026, 9, 1).toISOString(), "schedule"),
        conversation("f", null, "schedule"),
    ], now);
    assert.equal(days.length, 14);
    assert.equal(days.at(-1)?.day, "2026-10-26");
    assert.deepEqual(days.at(-1), { day: "2026-10-26", lemma: 1, people: 2 });
    assert.deepEqual(days.at(-4), { day: "2026-10-23", lemma: 1, people: 0 });
    assert.equal(days.reduce((sum, day) => sum + day.lemma + day.people, 0), 4, "outside the window, or with no server time, counts nowhere");
});

test("work since a moment ignores what came before it", () => {
    const since = Date.parse("2026-10-26T08:00:00Z");
    const counts = workSince([
        conversation("a", "2026-10-26T09:00:00Z", "task"),
        conversation("b", "2026-10-26T07:00:00Z", "task"),
        conversation("c", "2026-10-26T10:00:00Z", "channel"),
    ], since);
    assert.deepEqual(counts, { lemma: 1, people: 1 });
});

const WRITTEN = `---
outcome: Every customer hears back within a day
kind: week
target: 90%
unit: "%"
measure: SELECT 70 AS share
---

## What must be true
- Every customer hears back within a day — Priya, Oct 9

## How we work
- Refunds over $200 need Dev's approval — Priya, Oct 14
- Replies are two lines

## Who holds what
* Lemma drafts every reply waiting over 20 hours — Lemma, Oct 13

## A heading nobody planned
- this line belongs to no section

## Never
- Never promise a refund date — Priya, Oct 14
`;

test("a plan file reads into its outcome, measure and lines, with who added each", () => {
    const plan = parsePlan(WRITTEN);
    assert.equal(plan.outcome, "Every customer hears back within a day");
    assert.equal(plan.kind, "week");
    assert.equal(plan.target, "90%");
    assert.equal(plan.unit, "%");
    assert.equal(plan.measure, "SELECT 70 AS share");
    assert.deepEqual(plan.sections.how, [
        { text: "Refunds over $200 need Dev's approval", by: "Priya", on: "Oct 14" },
        { text: "Replies are two lines" },
    ]);
    assert.equal(plan.sections.who[0].by, "Lemma");
    assert.equal(plan.sections.never.length, 1);
    assert.equal(plan.sections.must.length, 1);
});

test("a plan survives being written and read back", () => {
    const plan = parsePlan(WRITTEN);
    assert.deepEqual(parsePlan(serializePlan(plan)), plan);
});

test("saving an unchanged plan onto its file writes the file back as it was", () => {
    assert.equal(serializePlan(parsePlan(WRITTEN), WRITTEN), WRITTEN);
});

test("editing a line keeps what the parser does not read, and changes only that line", () => {
    const plan = parsePlan(WRITTEN);
    const how = [...plan.sections.how];
    how[1] = { ...how[1], text: "Replies are three lines at most" };
    const saved = serializePlan({ ...plan, sections: { ...plan.sections, how } }, WRITTEN);
    assert.equal(saved, WRITTEN.replace("- Replies are two lines", "- Replies are three lines at most"));
});

test("adding and removing lines leaves prose, other headings, nested bullets and extra keys in place", () => {
    const file = `---
outcome: "Two posts go out every week"
owner: priya
kind: week
---

# Content

Some words about why this matters.

## How we work
- Drafts go to Priya first — Priya, Oct 2
  - Unless it is a reshare
- Posts go out on Tuesdays

## Notes
- not a plan line
`;
    const plan = parsePlan(file);
    assert.equal(plan.sections.how.length, 3);
    const how = plan.sections.how.filter((line) => line.text !== "Posts go out on Tuesdays");
    how.push({ text: "Every post has an image", by: "Dev", on: "Oct 20" });
    const saved = serializePlan({ ...plan, sections: { ...plan.sections, how, never: [{ text: "Never post on weekends" }] } }, file);
    assert.equal(saved, `---
outcome: "Two posts go out every week"
owner: priya
kind: week
---

# Content

Some words about why this matters.

## How we work
- Drafts go to Priya first — Priya, Oct 2
  - Unless it is a reshare
- Every post has an image — Dev, Oct 20

## Notes
- not a plan line

## Never
- Never post on weekends
`);
});

test("a line added to an empty section goes under its heading", () => {
    const file = "## What must be true\n- One thing\n\n## Never\n\n## Later\nprose\n";
    const plan = parsePlan(file);
    const saved = serializePlan({ ...plan, sections: { ...plan.sections, never: [{ text: "Never two" }] } }, file);
    assert.equal(saved, "## What must be true\n- One thing\n\n## Never\n- Never two\n\n## Later\nprose\n");
});

test("changing the measure rewrites only the plan's own front matter keys", () => {
    const file = "---\noutcome: Ship it\nkind: date\ndue: 2026-10-30\nowner: priya\n---\n\n## Never\n- Never skip review\n";
    const plan = parsePlan(file);
    assert.equal(plan.due, "2026-10-30");
    const saved = serializePlan({ ...plan, target: "22 steps", measure: "SELECT count(*) FROM steps" }, file);
    const again = parsePlan(saved);
    assert.equal(again.due, "2026-10-30");
    assert.equal(again.target, "22 steps");
    assert.equal(again.measure, "SELECT count(*) FROM steps");
    assert.match(saved, /^owner: priya$/m);
    assert.match(saved, /\n---\n\n## Never\n- Never skip review\n$/);
});

test("front matter that will not parse is kept on a line edit, and refuses a measure edit", () => {
    const file = "---\noutcome: [unclosed\n---\n## Never\n- Never email anyone twice\n";
    const plan = parsePlan(file);
    const saved = serializePlan({ ...plan, sections: { ...plan.sections, never: [{ text: "Never email anyone three times" }] } }, file);
    assert.equal(saved, "---\noutcome: [unclosed\n---\n## Never\n- Never email anyone three times\n");
    assert.throws(() => serializePlan({ ...plan, target: "90%" }, file), /front matter/);
});

test("front matter that will not parse leaves the lines readable", () => {
    const plan = parsePlan("---\noutcome: [unclosed\n---\n## Never\n- Never email anyone twice\n");
    assert.equal(plan.outcome, "");
    assert.deepEqual(plan.sections.never, [{ text: "Never email anyone twice" }]);
});

test("a plan with no outcome line takes its outcome from what must be true", () => {
    assert.equal(parsePlan("## What must be true\n- Two posts go out every week\n").outcome, "Two posts go out every week");
});

test("a draft names only what the pod already runs", () => {
    const plan: Plan = draftPlan({
        description: "Every lead gets a follow-up within two days",
        schedules: [
            { title: "Drafts follow-ups for quiet leads", trigger: "Weekdays at 08:30", active: true, internal: false },
            { title: "Workflow timeout", trigger: "In 2 hours", active: true, internal: true },
            { title: "Old digest", trigger: "Mondays", active: false, internal: false },
        ],
        agents: [
            { label: "Lemma", blurb: "Answers here", front: true },
            { label: "Researcher", blurb: "Finds who a lead is", front: false },
        ],
        skills: ["follow-up-style"],
    });
    assert.deepEqual(plan.sections.must, [{ text: "Every lead gets a follow-up within two days" }]);
    assert.deepEqual(plan.sections.how, [{ text: "Follows the follow-up-style skill" }]);
    assert.deepEqual(plan.sections.who.map((line) => line.text), [
        "Drafts follow-ups for quiet leads · weekdays at 08:30",
        "Researcher: finds who a lead is",
    ]);
    assert.deepEqual(plan.sections.never, []);
});

test("a measure answers with the first number in its first row", () => {
    assert.equal(readMeasure([{ label: "share", value: "72.5" }]), 72.5);
    assert.equal(readMeasure([{ n: 31 }, { n: 99 }]), 31);
    assert.equal(readMeasure([{ label: "none" }]), null);
    assert.equal(readMeasure([]), null);
});

test("only a weekly outcome with a target and a number says whether it is on track", () => {
    const plan = parsePlan(WRITTEN);
    assert.equal(standingOf(plan, 92), "on");
    assert.equal(standingOf(plan, 70), "behind");
    assert.equal(standingOf(plan, null), null);
    assert.equal(standingOf({ ...plan, kind: "date" }, 70), null);
    assert.equal(standingOf({ ...plan, target: "most of them" }, 70), null);
    assert.equal(standingOf(null, 70), null);
});

test("the queue is one list, oldest first, sorted into decide, answer and do", () => {
    const asked: AskedRow[] = [
        { podId: "p1", podName: "Support", conversationId: "c1", title: "Refund for Mara", kind: "approval", summary: "Approve a $240 refund", sinceMs: 3_000 },
        { podId: "p1", podName: "Support", conversationId: "c2", title: "Holiday hours", kind: "question", summary: null, sinceMs: 1_000 },
    ];
    const waits = [{
        podId: "p2",
        podName: "Books",
        workflowName: "late-fees",
        wait: { id: "w1", runId: "r1", workflowId: null, podId: "p2", nodeId: "n", type: "HUMAN", schema: null, uiSchema: null, createdAt: new Date(2_000).toISOString(), externalRef: null, agentName: null, question: null },
        run: { id: "r1", workflowId: null, podId: "p2", status: "WAITING", failedNodeId: null, currentNodeId: null, error: null, startedAt: null, completedAt: null, createdAt: null },
    }] as unknown as WaitingRow[];
    const items = queueItems(asked, waits);
    assert.deepEqual(items.map((item) => [item.kind, item.title]), [
        ["answer", "Holiday hours"],
        ["do", "Fill in the late-fees form"],
        ["decide", "Approve a $240 refund"],
    ]);
    assert.equal(items[2].detail, "Refund for Mara");
    assert.equal(items[1].runId, "r1");
});

test("a contact waiting on a person is something to answer, opened in their chat", () => {
    const items = queueItems([], [], [
        { podId: "p1", podName: "Support", channelId: "ch1", name: "Dana Ruiz", reason: "Asked for a refund above what it may give", sinceMs: 5_000 },
        { podId: "p1", podName: "Support", channelId: "ch2", name: "A website visitor", reason: null, sinceMs: 1_000 },
    ]);
    assert.deepEqual(items.map((item) => [item.kind, item.title, item.detail]), [
        ["answer", "Reply to A website visitor", "Waiting on a person"],
        ["answer", "Reply to Dana Ruiz", "Asked for a refund above what it may give"],
    ]);
    assert.deepEqual(viewOfItem(items[1]), chatView({ kind: "contact", podId: "p1", id: "ch1" }));
});

test("how long something has waited reads the way a person says it", () => {
    const now = 100 * 86_400_000;
    assert.equal(ageOf(now - 20_000, now), "1 min");
    assert.equal(ageOf(now - 40 * 60_000, now), "40 min");
    assert.equal(ageOf(now - 26 * 3_600_000, now), "26 h");
    assert.equal(ageOf(now - 3 * 86_400_000, now), "3 d");
});

function channel(id: string, kind: Channel["kind"], extra: Partial<Channel> = {}): Channel {
    return {
        id,
        kind,
        title: kind === "GROUP" ? id : null,
        topic: null,
        person: null,
        visibility: "PUBLIC",
        answersShared: true,
        sources: [],
        assignee: null,
        agentsAnswer: true,
        handBackAfterHours: 24,
        status: null,
        attention: null,
        archived: false,
        joined: true,
        lastLine: null,
        lastMessageAt: null,
        lastSeq: 0,
        createdAt: "2026-10-01T00:00:00Z",
        unread: { lines: 0, mentions: 0, waiting: 0, readSeq: 0 },
        ...extra,
    };
}

function chat(id: string, activityAt: string | null, extra: Partial<ConversationRef> = {}): ConversationRef {
    return { id, title: id, at: "display only", activityAt, kind: "CHAT", ...extra };
}

test("a chat is what a person started; Lemma's own runs are not, unless they wait on you", () => {
    assert.equal(isChat(chat("a", null)), true);
    assert.equal(isChat(chat("b", null, { origin: { kind: "channel", label: "Slack · #ops", platform: "SLACK" } })), true);
    assert.equal(isChat(chat("c", null, { origin: { kind: "schedule", label: "Schedule · digest" } })), false);
    assert.equal(isChat(chat("d", null, { origin: { kind: "schedule", label: "Schedule · digest" }, waitingOnYou: true })), true);
    assert.equal(isChat(chat("e", null, { boundTo: "file:/plan.md" })), false);
});

test("chats from every outcome make one list: waiting on you first, then the rest, newest first", () => {
    const { needs, rest } = chatItems({
        pods: [{ id: "p1", name: "Support" }, { id: "p2", name: "Renewals" }],
        conversations: new Map([
            ["p1", [
                chat("Refund question", "2026-10-20T10:00:00Z"),
                chat("Morning digest", "2026-10-21T09:00:00Z", { origin: { kind: "schedule", label: "Schedule · digest" } }),
                chat("Approve a refund", "2026-10-19T09:00:00Z", { waitingOnYou: true, origin: { kind: "workflow", label: "Workflow", runId: "r1" } }),
            ]],
            ["p2", [chat("Renewal list", "2026-10-21T12:00:00Z", { status: "RUNNING" })]],
        ]),
        channels: new Map([
            ["p1", [
                channel("Ops", "GROUP", { lastMessageAt: "2026-10-21T11:00:00Z", unread: { lines: 3, mentions: 0, waiting: 0, readSeq: 1 } }),
                channel("mine-empty", "DIRECT"),
                channel("mine", "DIRECT", { sources: [{ platform: "WHATSAPP", title: null }], lastMessageAt: "2026-10-18T08:00:00Z" }),
                channel("customer", "CONTACT", { lastMessageAt: "2026-10-21T13:00:00Z" }),
                channel("Escalations", "GROUP", { lastMessageAt: "2026-10-17T08:00:00Z", unread: { lines: 1, mentions: 0, waiting: 1, readSeq: 0 } }),
            ]],
        ]),
        me: "u1",
    });
    assert.deepEqual(needs.map((item) => item.title), ["Approve a refund", "Escalations"]);
    assert.deepEqual(rest.map((item) => item.title), ["Renewal list", "Ops", "Refund question", "Your line on WhatsApp"]);
    const ops = rest[1];
    assert.equal(ops.kind, "shared");
    assert.equal(ops.unread, 3);
    assert.deepEqual(ops.open, { kind: "shared", podId: "p1", id: "Ops" });
    assert.equal(rest[0].running, true);
    assert.equal(rest[0].podName, "Renewals");
    assert.equal(rest[3].kind, "line", "someone's own line is listed once anything is in it");
});

test("finding a chat matches its name, its outcome, or what was last said", () => {
    const input = {
        pods: [{ id: "p1", name: "Support" }, { id: "p2", name: "Renewals" }],
        conversations: new Map([["p1", [chat("Refund question", null)]], ["p2", [chat("Call list", null)]]]),
        channels: new Map([["p1", [channel("Ops", "GROUP", { lastLine: { sender: { kind: "MEMBER", id: "u2", name: "Priya" }, text: "Thursday works", createdAt: "2026-10-21T11:00:00Z" } })]]]),
        me: "u1",
    } as Parameters<typeof chatItems>[0];
    const titles = (search: string) => chatItems({ ...input, search }).rest.map((item) => item.title);
    assert.deepEqual(titles("refund"), ["Refund question"]);
    assert.deepEqual(titles("renewals"), ["Call list"]);
    assert.deepEqual(titles("thursday"), ["Ops"]);
    assert.deepEqual(titles("nothing like it"), []);
});

test("two opens are the same when they show the same thing", () => {
    assert.equal(sameOpen({ kind: "chat", podId: "p", id: "1" }, { kind: "chat", podId: "p", id: "1" }), true);
    assert.equal(sameOpen({ kind: "chat", podId: "p", id: "1" }, { kind: "shared", podId: "p", id: "1" }), false);
    assert.equal(sameOpen({ kind: "contact", podId: "p", id: "c" }, { kind: "contact", podId: "p", id: "c" }), true);
    assert.equal(sameOpen({ kind: "contact", podId: "p", id: "c" }, { kind: "contact", podId: "p", id: "d" }), false);
    assert.equal(sameOpen(NO_CHAT, NO_CHAT), true);
});

test("a sidebar section folds past a handful, but never hides what waits on you or what is open", () => {
    const { needs, rest } = chatItems({
        pods: [{ id: "p1", name: "Support" }],
        conversations: new Map([["p1", [
            chat("one", "2026-10-21T10:00:00Z"),
            chat("two", "2026-10-21T09:00:00Z"),
            chat("three", "2026-10-21T08:00:00Z"),
            chat("four", "2026-10-21T07:00:00Z"),
            chat("asks", "2026-10-01T07:00:00Z", { waitingOnYou: true }),
        ]]]),
        channels: new Map(),
        me: null,
    });
    const items = sectionRows([...needs, ...rest], []);
    assert.deepEqual(items.map((item) => item.title), ["asks", "one", "two", "three", "four"]);
    const folded = foldSection(items, { name: "today" }, 2);
    assert.deepEqual(folded.shown.map((item) => item.title), ["asks", "one"]);
    assert.equal(folded.hidden, 3);
    const open = foldSection(items, chatView({ kind: "chat", podId: "p1", id: "four" }), 2);
    assert.deepEqual(open.shown.map((item) => item.title), ["asks", "one", "four"], "the open chat stays in view, in its place");
    assert.equal(open.hidden, 2);
    assert.equal(foldSection(items, { name: "today" }, 10).hidden, 0);
});

test("a Lemma's own work joins its chats: each automation's latest run, and tables changed this week", () => {
    const now = Date.parse("2026-10-23T12:00:00Z");
    const work = workRows({
        podId: "p1",
        conversations: [
            chat("pulse 1", "2026-10-23T08:00:00Z", { origin: { kind: "schedule", label: "Schedule · Daily pulse" } }),
            chat("pulse 0", "2026-10-22T08:00:00Z", { origin: { kind: "schedule", label: "Schedule · Daily pulse" } }),
            chat("Collect the request", "2026-10-23T09:00:00Z", { origin: { kind: "workflow", label: "Workflow run", runId: "r1" }, status: "RUNNING" }),
            chat("Approve it", "2026-10-23T10:00:00Z", { origin: { kind: "workflow", label: "Workflow run", runId: "r2" }, waitingOnYou: true }),
            chat("a chat", "2026-10-23T11:00:00Z"),
        ],
        tables: [
            { name: "leads", title: "Leads", updated: "2026-10-22T12:00:00Z" },
            { name: "old_log", title: "Old Log", updated: "2026-09-01T12:00:00Z" },
        ],
        now,
    });
    assert.deepEqual(work.map((row) => [row.kind, row.title]).sort(), [
        ["automation", "Collect the request"],
        ["automation", "Daily pulse"],
        ["table", "Leads"],
    ], "one line per schedule, its newest run; a run waiting on you is a chat; an old table is not recent");
    const pulse = work.find((row) => row.title === "Daily pulse");
    assert.deepEqual(pulse?.view, chatView({ kind: "chat", podId: "p1", id: "pulse 1" }));
    assert.deepEqual(work.find((row) => row.title === "Collect the request")?.view, { name: "run", podId: "p1", runId: "r1" });
    assert.equal(work.find((row) => row.title === "Collect the request")?.running, true);
    assert.deepEqual(work.find((row) => row.kind === "table")?.view, { name: "table", podId: "p1", table: "leads" });

    const { needs, rest } = chatItems({ pods: [{ id: "p1", name: "Support" }], conversations: new Map([["p1", [chat("a chat", "2026-10-23T11:00:00Z"), chat("Approve it", "2026-10-23T10:00:00Z", { waitingOnYou: true })]]]), channels: new Map(), me: null });
    assert.deepEqual(sectionRows([...needs, ...rest], work).map((row) => row.title), ["Approve it", "a chat", "Collect the request", "Daily pulse", "Leads"]);
});

test("someone's own line is listed only while it has something new", () => {
    const base = { kind: "line" as const, unread: 0, needsYou: false };
    assert.equal(inSidebar(base), false);
    assert.equal(inSidebar({ ...base, unread: 2 }), true);
    assert.equal(inSidebar({ ...base, needsYou: true }), true);
    assert.equal(inSidebar({ ...base, kind: "shared" }), true);
    assert.equal(inSidebar({ ...base, kind: "chat" }), true);
});

test("the sidebar puts the busiest outcome first and folds the ones gone quiet", () => {
    const now = Date.parse("2026-10-22T12:00:00Z");
    const items = (pod: string, at: string | null, extra: Partial<ConversationRef> = {}) =>
        chatItems({ pods: [{ id: pod, name: pod }], conversations: new Map([[pod, [chat(pod + "-chat", at, extra)]]]), channels: new Map(), me: null });
    const all = (pod: string, at: string | null, extra: Partial<ConversationRef> = {}) => {
        const { needs, rest } = items(pod, at, extra);
        return { pod: { id: pod, name: pod }, items: [...needs, ...rest] };
    };
    const sections = sidebarSections([
        all("quiet", "2026-10-01T12:00:00Z"),
        all("busy", "2026-10-22T11:00:00Z"),
        { pod: { id: "empty", name: "Empty" }, items: [] },
        all("asks", "2026-09-01T12:00:00Z", { waitingOnYou: true }),
        all("lately", "2026-10-20T12:00:00Z"),
    ], now, new Map(), null);
    assert.deepEqual(sections.map((one) => one.pod.id), ["busy", "lately", "quiet", "asks", "empty"]);
    assert.deepEqual(sections.map((one) => one.open), [true, true, false, true, false], "quiet for a week folds, unless something waits on you");
    assert.equal(sections[3].waiting, 1);

    const here = sidebarSections([all("quiet", "2026-10-01T12:00:00Z")], now, new Map(), "quiet");
    assert.equal(here[0].open, true, "the outcome the reader is in stays open");
    const chosen = sidebarSections([all("busy", "2026-10-22T11:00:00Z"), all("quiet", "2026-10-01T12:00:00Z")], now, new Map([["busy", false], ["quiet", true]]), null);
    assert.deepEqual(chosen.map((one) => one.open), [false, true], "what the reader folded or opened wins");
    assert.ok(QUIET_AFTER_MS > 0);
});

test("a private line is named for what it is, and repeated names get told apart", () => {
    assert.equal(lineTitle("Email"), "Your line on Email");
    assert.equal(lineTitle("WhatsApp and Email"), "Your line on WhatsApp and Email");
    assert.equal(lineTitle("Notices"), "Notices");
    const repeated = repeatedTitles([
        { key: "a", title: "LLM Lab tutor" },
        { key: "b", title: "Casual greeting" },
        { key: "c", title: "LLM Lab tutor" },
    ]);
    assert.deepEqual([...repeated].sort(), ["a", "c"]);
});

test("an automation that ran many times is one line, with how many runs and its newest", () => {
    const run = (id: string, at: string, runId: string, extra: Partial<ConversationRef> = {}) =>
        chat(id, at, { title: "Workflow run: reviewer", origin: { kind: "workflow", label: "Workflow run", runId }, ...extra });
    const work = workRows({
        podId: "p1",
        conversations: [
            run("a", "2026-10-23T09:00:00Z", "r1"),
            run("b", "2026-10-23T10:00:00Z", "r2"),
            run("b2", "2026-10-23T10:05:00Z", "r2"),
            run("c", "2026-10-23T08:00:00Z", "r3", { status: "RUNNING" }),
            chat("u", "2026-10-23T07:00:00Z", { title: "Workflow run: updater", origin: { kind: "workflow", label: "Workflow run", runId: "r9" } }),
        ],
        tables: [],
        now: Date.parse("2026-10-23T12:00:00Z"),
    });
    assert.deepEqual(work.map((row) => [row.title, row.runs]).sort(), [["Reviewer", 3], ["Updater", 1]]);
    const reviewer = work.find((row) => row.title === "Reviewer");
    assert.deepEqual(reviewer?.view, { name: "run", podId: "p1", runId: "r2" }, "it opens its newest run");
    assert.equal(reviewer?.running, true, "one of its runs is still going");
});
