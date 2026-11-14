import test from "node:test";
import assert from "node:assert/strict";
import { activityOfRecents, daysOfRecents, historyDays, historyRows, historySaid, isChat, ranSaid, workRows, type RecentsPod } from "../src/outcomes/model.ts";
import { chatView, outcomeView, readView, writeView } from "../src/outcomes/address.ts";

const at = (day: number, hour: number) => new Date(2026, 10, day, hour, 0, 0).toISOString();

const SUPPORT: RecentsPod = {
    pod_id: "p1",
    pod_name: "Support",
    chats: [
        { conversation_id: "c1", title: "Refund for Kestrel Labs", origin: "chat", status: "RUNNING", last_activity_at: at(12, 9), waiting_on_you: false },
        { conversation_id: "c2", title: null, origin: "schedule", status: "WAITING", last_activity_at: at(11, 16), waiting_on_you: true },
    ],
    automations: [
        { kind: "SCHEDULE", id: "s1", name: "friday_drafts", run_count: 6, latest_status: "COMPLETED", latest_at: at(12, 11), conversation_id: "c9" },
        { kind: "WORKFLOW", id: "w1", name: "Escalate", run_count: 1, latest_status: "FAILED", latest_at: at(10, 8), workflow_run_id: "r7" },
        { kind: "SCHEDULE", id: "s2", name: null, run_count: 0, latest_status: "FILTERED", latest_at: at(12, 7) },
    ],
};

test("a run's status is said in a person's words", () => {
    assert.deepEqual(ranSaid("COMPLETED"), { said: "Done", tone: "done" });
    assert.deepEqual(ranSaid("TARGET_FAILED"), { said: "Went wrong", tone: "bad" });
    assert.deepEqual(ranSaid("FAILED"), { said: "Went wrong", tone: "bad" });
    assert.deepEqual(ranSaid("WAITING"), { said: "Waiting on someone", tone: "wait" });
    assert.deepEqual(ranSaid("DISPATCHED"), { said: "Running", tone: "live" });
    assert.deepEqual(ranSaid("FILTERED"), { said: "Nothing to do", tone: null }, "a filter turning an event away is not a failure");
    assert.deepEqual(ranSaid("CANCELLED"), { said: "Stopped", tone: null });
    assert.deepEqual(ranSaid("TIMED_OUT"), { said: "Timed out", tone: null }, "a status this does not know is still readable");
});

test("history lists chats and what ran, newest first, each opening the right place", () => {
    const rows = historyRows([SUPPORT]);
    assert.deepEqual(rows.map((row) => row.key), ["s:s1", "c:c1", "s:s2", "c:c2", "w:w1"]);
    const [drafts, refund, quiet, asked, escalate] = rows;
    assert.deepEqual([drafts.title, drafts.what, drafts.said, drafts.count], ["Friday Drafts", "On a schedule", "Done", 6], "a stored name is read the way the app writes names");
    assert.deepEqual(drafts.view, chatView({ kind: "chat", podId: "p1", id: "c9" }), "a schedule opens what its newest fire started");
    assert.deepEqual([refund.what, refund.said, refund.tone], ["Chat", "Working now", "live"]);
    assert.deepEqual(refund.view, chatView({ kind: "chat", podId: "p1", id: "c1" }));
    assert.deepEqual([quiet.title, quiet.count], ["A schedule", 1], "a schedule with no name still has one, and a count of at least one");
    assert.deepEqual(quiet.view, outcomeView("p1", "automations", "schedules"), "with nothing started, it opens the schedules");
    assert.deepEqual([asked.title, asked.what, asked.said, asked.tone], ["A chat with no name", "Started on a schedule", "Waiting on you", "wait"]);
    assert.deepEqual([escalate.said, escalate.tone], ["Went wrong", "bad"]);
    assert.deepEqual(escalate.view, { name: "run", podId: "p1", runId: "r7" });
});

test("history is read a day at a time: today, yesterday, then the day by name", () => {
    const days = historyDays(historyRows([SUPPORT]), new Date(2026, 10, 12, 12, 0, 0).getTime());
    assert.deepEqual(days.map((day) => [day.label, day.rows.length]), [["Today", 3], ["Yesterday", 1], ["Tuesday, Nov 10", 1]]);
    assert.deepEqual(historyDays([], Date.now()), []);
});

test("history says in one sentence how much moved and whether anything went wrong", () => {
    assert.equal(historySaid(historyRows([SUPPORT]), 14), "2 chats and 3 things that ran by themselves in the last 14 days. 1 went wrong.");
    assert.equal(historySaid(historyRows([{ ...SUPPORT, automations: [] }]), 14), "2 chats in the last 14 days.");
    assert.equal(historySaid(historyRows([{ ...SUPPORT, chats: [], automations: SUPPORT.automations.slice(0, 1) }]), 14), "1 thing that ran by itself in the last 14 days.");
    assert.equal(historySaid([], 14), "Nothing has moved in the last 14 days.");
});

test("History has an address for every Lemma and one for a single Lemma", () => {
    for (const view of [{ name: "history", podId: null }, { name: "history", podId: "p1" }] as const) {
        assert.deepEqual(readView(writeView(view)), view);
    }
    assert.equal(writeView({ name: "history", podId: "p1" }), "/history/p1");
    assert.equal(readView("/history/p1/more"), null);
});

test("the overview's chats and runs read as a Lemma's own list does, so one request draws what one per Lemma drew", () => {
    const byPod = activityOfRecents([SUPPORT], () => "now");
    const rows = byPod.get("p1") ?? [];
    assert.deepEqual(rows.map((row) => row.id), ["c1", "c2", "c9", "r7"], "a schedule that started nothing has nothing to open, and is left out");
    const [refund, asked, drafts, escalate] = rows;
    assert.equal(refund.origin, undefined, "a plain chat has no origin, as in a Lemma's own list");
    assert.equal(isChat(refund), true);
    assert.equal(asked.origin?.kind, "schedule");
    assert.equal(isChat(asked), true, "whatever started it, one waiting on the reader is a chat: that is where it is answered");
    assert.deepEqual([drafts.title, drafts.origin?.kind, drafts.origin?.label, drafts.runs], ["Friday Drafts", "schedule", "Schedule · Friday Drafts", 6]);
    assert.equal(isChat(drafts), false, "an automation's run is the Lemma's work, not a chat");
    assert.deepEqual([escalate.origin?.kind, escalate.origin?.runId, escalate.status], ["workflow", "r7", null]);
});

test("an automation whose newest run waits on the reader is listed once, as that chat", () => {
    const pod: RecentsPod = {
        ...SUPPORT,
        chats: [{ conversation_id: "c9", title: "Drafts need a yes", origin: "schedule", status: "WAITING", last_activity_at: at(12, 11), waiting_on_you: true }],
        automations: SUPPORT.automations.slice(0, 1),
    };
    assert.deepEqual((activityOfRecents([pod], () => "").get("p1") ?? []).map((row) => [row.id, row.waitingOnYou]), [["c9", true]]);
});

test("a chat about a page or a table stays out of the chats, as its own list kept it", () => {
    const pod: RecentsPod = { ...SUPPORT, automations: [], chats: [{ conversation_id: "c5", title: "About the price list", origin: "resource", last_activity_at: at(12, 9), waiting_on_you: false }] };
    assert.equal(isChat((activityOfRecents([pod], () => "").get("p1") ?? [])[0]), false);
});

test("the sidebar's line for an automation carries the count the overview made", () => {
    const rows = workRows({ podId: "p1", conversations: activityOfRecents([SUPPORT], () => "").get("p1") ?? [], tables: [], now: Date.parse(at(12, 12)) });
    assert.deepEqual(rows.map((row) => [row.title, row.runs]), [["Friday Drafts", 6], ["Escalate", 1]]);
    assert.deepEqual(rows[0].view, chatView({ kind: "chat", podId: "p1", id: "c9" }));
    assert.deepEqual(rows[1].view, { name: "run", podId: "p1", runId: "r7" });
});

test("the board's bars are the overview's own count per day", () => {
    const pod: RecentsPod = { ...SUPPORT, activity: [{ day: "2026-11-11", by_agent: 4, by_people: 1 }, { day: "2026-11-12", by_agent: 0, by_people: 2 }] };
    assert.deepEqual(daysOfRecents([pod]).get("p1"), [{ day: "2026-11-11", lemma: 4, people: 1 }, { day: "2026-11-12", lemma: 0, people: 2 }]);
    assert.deepEqual(daysOfRecents([SUPPORT]).get("p1"), [], "a Lemma the overview gave no days for has none");
});
