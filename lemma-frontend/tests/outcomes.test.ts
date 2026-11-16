import test from "node:test";
import assert from "node:assert/strict";
import {
    NO_CHAT,
    agentKey,
    agentView,
    ageOf,
    answersHere,
    chatItems,
    chatView,
    dailyActivity,
    foldSection,
    sectionRows,
    workRows,
    inSidebar,
    isFramePath,
    repeatedTitles,
    sidebarSections,
    QUIET_AFTER_MS,
    isChat,
    outcomeView,
    queueItems,
    readSideSettings,
    readTrialView,
    readView,
    refreshKept,
    sameOpen,
    viewOfAddress,
    verbOf,
    viewOfItem,
    workSince,
    writeView,
    type View,
} from "../src/outcomes/model.ts";
import type { ConversationRef } from "../src/data/types.ts";
import { chatTitle } from "../src/channels/chat-list.ts";
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

test("every view writes an address that reads back as itself, and is told apart from a workspace address", () => {
    const views: View[] = [
        { name: "today" },
        { name: "outcomes" },
        { name: "needs" },
        { name: "apps" },
        { name: "connectors" },
        { name: "people" },
        { name: "hire" },
        { name: "chats", open: NO_CHAT },
        { name: "chats", open: { kind: "chat", podId: "pod one/2", id: "new" } },
        { name: "chats", open: { kind: "chat", podId: "p1", id: "c 9" } },
        { name: "chats", open: { kind: "shared", podId: "p1", id: "ch/1" } },
        { name: "chats", open: { kind: "contact", podId: "abc", id: "contact 7" } },
        outcomeView("abc", "contacts"),
        outcomeView("pod one/2"),
        outcomeView("abc", "automations"),
        outcomeView("abc", "automations", "schedules"),
        outcomeView("abc", "apps"),
        outcomeView("abc", "settings"),
        outcomeView("abc", "settings", "schedules"),
        { name: "agent", podId: "abc", agent: "researcher" },
        { name: "record", podId: "abc", table: "leads", recordId: "r 9" },
        { name: "run", podId: "abc", runId: "r/1" },
        { name: "workflow", podId: "abc", workflow: "late-fees" },
        { name: "app", podId: "abc", app: "crm" },
        { name: "table", podId: "abc", table: "leads" },
        { name: "file", podId: "abc", path: "/reports/Q3 review.md" },
        { name: "computer", podId: "abc", conversationId: null },
        { name: "computer", podId: "abc", conversationId: "c1" },
        outcomeView("abc", "chats", "channels"),
    ];
    for (const view of views) {
        assert.deepEqual(readView(writeView(view)), view, writeView(view));
        /* A path whose first word is not one of the frame's own would be read
           as a Lemma's workspace address and opened as its summary. */
        assert.ok(isFramePath(writeView(view)), writeView(view) + " is the frame's own");
    }
});

test("an address that is not the app's own is left for another reader, not guessed at", () => {
    for (const path of ["/", "/blog", "/t", "/t/outcome/abc", "/o/outcome/abc", "/l/abc/whatever", "/l/abc/apps/a/b", "/l/%E0%A4%A", "/l/abc/files/%E0%A4%A", "/l/new/x", "/home/x"]) {
        assert.equal(readView(path), null, path);
    }
    /* Words every object answers to are not places. */
    for (const path of ["/constructor", "/toString", "/__proto__", "/hasOwnProperty"]) {
        assert.equal(readView(path), null, path);
    }
    /* A word the workspace used under `/l` is the workspace's to read. */
    for (const path of ["/l/p1/conversation/c1", "/l/p1/file/me/notes.md", "/l/p1/about/connections", "/l/p1/table/leads", "/l/p1/app/crm", "/l/p1/run/r1"]) {
        assert.equal(readView(path), null, path);
    }
    assert.deepEqual(readView("/l/teammates"), { name: "outcomes" }, "the workspace's every teammate at once is the list of Lemmas");
    assert.deepEqual(readView("/l/p1/automations/nonsense"), null, "a view it does not have is not one");
});

test("where a word is the workspace's and the app's, it names the same place", () => {
    assert.deepEqual(readView("/l/p1"), outcomeView("p1"));
    assert.deepEqual(readView("/l/p1/chats"), outcomeView("p1", "chats"));
    assert.deepEqual(readView("/l/p1/apps"), outcomeView("p1", "apps"));
    assert.deepEqual(readView("/l/p1/files"), outcomeView("p1", "files"));
    assert.deepEqual(readView("/l/p1/tables"), outcomeView("p1", "tables"));
    assert.deepEqual(readView("/l/p1/pages"), outcomeView("p1", "pages"));
    assert.deepEqual(readView("/l/p1/people"), outcomeView("p1", "people"));
    assert.deepEqual(readView("/l/p1/contacts"), outcomeView("p1", "contacts"));
    assert.deepEqual(readView("/l/p1/workflows"), outcomeView("p1", "automations", "workflows"));
    assert.deepEqual(readView("/l/p1/groups"), outcomeView("p1", "chats", "channels"));
    assert.deepEqual(readView("/l/p1/settings"), outcomeView("p1", "settings"));
    assert.deepEqual(readView("/l/p1/computer"), { name: "computer", podId: "p1", conversationId: null });
});

test("the frame's first addresses still name the place they named", () => {
    assert.deepEqual(readTrialView("/t"), { name: "today" });
    assert.equal(readTrialView("/t/abc"), null, "a workspace address is the workspace's to read");
    assert.equal(readTrialView("/l/p1"), null);
    assert.deepEqual(readTrialView("/t/outcome/abc/whatever"), outcomeView("abc"));
    assert.deepEqual(readTrialView("/t/outcome/%E0%A4%A"), { name: "today" });
    assert.deepEqual(readTrialView("/t/outcome/abc/run"), outcomeView("abc"), "a run with no id is the outcome");
    assert.deepEqual(readTrialView("/t/outcome/abc/xray"), outcomeView("abc", "automations", "xray"), "X-ray was a tab; it is a view of Automations now");
    assert.deepEqual(readTrialView("/t/outcome/abc/automations/nonsense"), outcomeView("abc", "automations"), "a view it does not have is its first");
    assert.deepEqual(readTrialView("/t/chats/p1/c/%E0%A4%A"), { name: "chats", open: NO_CHAT });
    assert.deepEqual(readTrialView("/t/chats/p1/x/y"), { name: "chats", open: NO_CHAT }, "an unknown kind of chat opens none");
    assert.deepEqual(readTrialView("/o/outcome/p1/apps"), outcomeView("p1", "apps"), "a link from while the app was tried at /o");
    assert.deepEqual(readTrialView("/t/outcome/p1/chats/c/c1"), chatView({ kind: "chat", podId: "p1", id: "c1" }));
    assert.deepEqual(readTrialView("/t/outcome/p1/chats/g/g1"), chatView({ kind: "shared", podId: "p1", id: "g1" }));
    assert.deepEqual(readTrialView("/t/outcome/p1/chats/customers"), outcomeView("p1", "chats"));
    assert.deepEqual(readTrialView("/t/chats/p1/customers"), outcomeView("p1", "contacts"), "the old customers queue is the Contacts tab");
    assert.deepEqual(readTrialView("/t/chats/p1/customers/c9"), chatView({ kind: "contact", podId: "p1", id: "c9" }));
    assert.deepEqual(readTrialView("/t/outcome/p1/chats"), outcomeView("p1", "chats"), "with no chat named, its every chat");
    assert.deepEqual(readTrialView("/t/outcome/p1/chats/channels"), outcomeView("p1", "chats", "channels"), "on the shared ones");
});

test("the app's own paths are told apart from the rest of the site", () => {
    for (const path of ["/home", "/needs", "/apps", "/connectors", "/people", "/chats", "/l", "/l/new", "/l/p1/chats/c1", "/l/p1/conversation/c1"]) {
        assert.ok(isFramePath(path), path);
    }
    for (const path of ["/", "/blog", "/auth", "/t/outcome/p1", "/o/p1", "/s/x", "/llms.txt"]) {
        assert.ok(!isFramePath(path), path + " is not the app's own");
    }
    assert.deepEqual(viewOfAddress({ podId: null, tabId: null, conversationId: null, team: true }), { name: "outcomes" }, "every teammate at once is the list of Lemmas");
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
    assert.deepEqual(viewOfAddress(address("space:chats")), outcomeView("p1", "chats"), "a Lemma's every chat");
    assert.deepEqual(viewOfAddress(address("space:channels")), { name: "outcome", podId: "p1", tab: "chats", section: "channels" }, "the shared ones");
    assert.deepEqual(viewOfAddress(address(null)), outcomeView("p1"));
    assert.deepEqual(viewOfAddress({ podId: "p1", tabId: "space:about", conversationId: null, section: "schedules" }), outcomeView("p1", "automations", "schedules"), "schedules are a view of Automations here");
    assert.deepEqual(viewOfAddress({ podId: "p1", tabId: "space:about", conversationId: null, section: "people" }), outcomeView("p1", "settings", "people"));
    assert.deepEqual(viewOfAddress(address("bot:researcher")), { name: "agent", podId: "p1", agent: "researcher" });
    assert.deepEqual(viewOfAddress(address("record:leads:42")), { name: "record", podId: "p1", table: "leads", recordId: "42" });
    assert.deepEqual(viewOfAddress(address("space:workflows")), outcomeView("p1", "automations"));
    assert.deepEqual(viewOfAddress(address("computer")), { name: "computer", podId: "p1", conversationId: null }, "the machine it works on");
    assert.deepEqual(viewOfAddress(address("space:all")), outcomeView("p1", "files"), "everything it holds is its files");
    assert.deepEqual(viewOfAddress(address("space:pages")), outcomeView("p1", "pages"));
    assert.deepEqual(viewOfAddress(address("space:tables")), outcomeView("p1", "tables"));
    assert.equal(viewOfAddress(address("nothing-this-build-knows:1")), null, "what the frame does not show goes to the classic workspace");
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

test("a question, an approval or a form is answered in place; a reply or a decision opens where it is", () => {
    const asked: AskedRow[] = [
        { podId: "p1", podName: "Support", conversationId: "c1", title: "Refund for Mara", kind: "approval", summary: "Approve a $240 refund", sinceMs: 1_000 },
        { podId: "p1", podName: "Support", conversationId: "c2", title: "Holiday hours", kind: "question", summary: null, sinceMs: 2_000 },
    ];
    const wait = (id: string, type: string) => ({
        podId: "p2",
        podName: "Books",
        workflowName: "late-fees",
        wait: { id, runId: "r-" + id, workflowId: null, podId: "p2", nodeId: "n", type, schema: null, uiSchema: null, createdAt: new Date(3_000).toISOString(), externalRef: null, agentName: null, question: null },
        run: { id: "r-" + id, workflowId: null, podId: "p2", status: "WAITING", failedNodeId: null, currentNodeId: null, error: null, startedAt: null, completedAt: null, createdAt: null },
    });
    const waits = [wait("w1", "HUMAN"), wait("w2", "DECISION")] as unknown as WaitingRow[];
    const contacts = [{ podId: "p1", podName: "Support", channelId: "ch1", name: "Dana Ruiz", reason: null, sinceMs: 5_000 }];
    const said = queueItems(asked, waits, contacts).map((item) => [item.title, answersHere(item), verbOf(item)]);
    assert.deepEqual(said, [
        ["Approve a $240 refund", "ask", "Decide"],
        ["Holiday hours", "ask", "Answer"],
        ["Fill in the late-fees form", "form", "Fill in"],
        ["Fill in the late-fees form", null, "Open"],
        ["Reply to Dana Ruiz", null, "Reply"],
    ]);
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
    assert.deepEqual(rest.map((item) => item.title), ["Renewal list", "Ops", "Refund question", "Your chat on WhatsApp"]);
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

test("the sidebar puts the busiest Lemma first, each folded to its name until the reader opens it", () => {
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
    ], new Map(), null);
    assert.deepEqual(sections.map((one) => one.pod.id), ["busy", "lately", "quiet", "asks", "empty"]);
    assert.deepEqual(sections.map((one) => one.open), [false, false, false, false, false], "a list of Lemmas, not of every Lemma's chats");
    assert.equal(sections[3].waiting, 1, "a folded Lemma still counts what waits on the reader, for its dot");

    const here = sidebarSections([all("quiet", "2026-10-01T12:00:00Z")], new Map(), "quiet");
    assert.equal(here[0].open, true, "the Lemma the reader is in opens");
    const chosen = sidebarSections([all("busy", "2026-10-22T11:00:00Z"), all("quiet", "2026-10-01T12:00:00Z")], new Map([["busy", false], ["quiet", true]]), "busy");
    assert.deepEqual(chosen.map((one) => one.open), [false, true], "what the reader folded or opened wins, even where they are");
    assert.ok(QUIET_AFTER_MS > 0);
});

test("a person's own chat is named for what it is, and repeated names get told apart", () => {
    assert.equal(chatTitle(channel("mine", "DIRECT", { sources: [{ platform: "RESEND", title: null }] }), "Support"), "Your chat on Email");
    assert.equal(chatTitle(channel("mine", "DIRECT", { sources: [{ platform: "WHATSAPP", title: null }, { platform: "RESEND", title: null }] }), "Support"), "Your chat on WhatsApp and Email");
    assert.equal(chatTitle(channel("mine", "DIRECT"), "Support"), "Notices");
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

test("how the sidebar reads is the reader's, and anything a browser kept that it does not recognise is the default", () => {
    assert.deepEqual(readSideSettings(null), { showing: "chats", grouping: "lemma" });
    assert.deepEqual(readSideSettings({ showing: "automations", grouping: "together" }), { showing: "automations", grouping: "together" });
    assert.deepEqual(readSideSettings({ showing: "everything" }), { showing: "everything", grouping: "lemma" });
    assert.deepEqual(readSideSettings({ showing: "workflows", grouping: "by-date" }), { showing: "chats", grouping: "lemma" });
    assert.deepEqual(readSideSettings("together"), { showing: "chats", grouping: "lemma" });
});

test("every sidebar line knows its Lemma, so one list of all of them can show whose each is", () => {
    const { rest } = chatItems({ pods: [{ id: "p1", name: "Support" }], conversations: new Map([["p1", [chat("c1", "2026-10-23T09:00:00Z")]]]), channels: new Map(), me: null });
    const work = workRows({
        podId: "p2",
        conversations: [chat("r1", "2026-10-23T10:00:00Z", { title: "Workflow run: reviewer", origin: { kind: "workflow", label: "Workflow run", runId: "run-1" } })],
        tables: [{ name: "leads", title: "Leads", updated: "2026-10-23T08:00:00Z" }],
        now: Date.parse("2026-10-23T12:00:00Z"),
    });
    const rows = sectionRows(rest, work);
    assert.deepEqual(rows.map((row) => [row.kind, row.podId]), [["automation", "p2"], ["chat", "p1"], ["table", "p2"]]);
});

test("an agent row that arrived without a name opens nothing, and keys apart from its twin", () => {
    assert.deepEqual(agentView("p1", { name: "billing", broken: false }), { name: "agent", podId: "p1", agent: "billing" });
    assert.equal(agentView("p1", { name: "", broken: true }), null, "its address would end at agent/ and open the settings instead");
    assert.notEqual(agentKey({ id: "" }, 0), agentKey({ id: "" }, 1), "two nameless rows share an empty id");
    assert.equal(agentKey({ id: "a1" }, 3), "a1");
});

test("a kept app outlives a Lemma list that leaves its Lemma out, until its own organization's list does", () => {
    const desk = { id: "p1", name: "Desk" };
    const kept = [{ podId: "p1", app: "board", orgId: "o1", pod: null }];
    const read = refreshKept(kept, [desk], "o1");
    assert.equal(read[0].pod, desk, "it takes its Lemma once the list has it");
    assert.equal(refreshKept(read, [desk], "o1"), read, "the same list changes nothing");
    assert.equal(refreshKept(read, [{ id: "p9", name: "Elsewhere" }], "o2"), read, "another organization's list leaves it mounted");
    assert.equal(refreshKept(read, [], "o2")[0].pod, desk, "with the Lemma it was last drawn with");
    assert.deepEqual(refreshKept(read, [], "o1"), [], "its own organization, read without it, lets it go");
});
