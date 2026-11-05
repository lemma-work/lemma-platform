import { strict as assert } from "node:assert";
import test from "node:test";
import { askedHref, askedRows, byAge, byAskedLongest, sayAsked } from "@/thread/waiting-on-you";
import { owedFrom, sayHired, sayOwed } from "@/space/teammates";

/** A conversation paused on a person is owed, the same as a workflow form.
 *  These pin how the server's list of pauses becomes rows on the rail. */

const pod = { id: "p1", name: "Kit" };

test("each conversation waiting on you is one row, however many pauses it holds", () => {
    const rows = askedRows(pod, [
        { conversation_id: "c3", conversation_title: "", kind: "APPROVAL", summary: "Delete the drafts", created_at: "2026-10-01T06:00:00Z" },
        { conversation_id: "c1", conversation_title: "Tracker refresh", kind: "QUESTION", summary: "Which week?", created_at: "2026-10-01T08:00:00Z" },
        { conversation_id: "c3", conversation_title: "", kind: "QUESTION", created_at: "2026-10-01T09:00:00Z" },
    ]);
    assert.deepEqual(rows.map((row) => [row.conversationId, row.kind, row.title, row.summary]), [
        ["c3", "approval", "A conversation", "Delete the drafts"],
        ["c1", "question", "Tracker refresh", "Which week?"],
    ]);
    assert.deepEqual(byAskedLongest(rows).map((row) => row.conversationId), ["c3", "c1"]);
});

test("a permission request from an agent on your computer is its own kind of ask", () => {
    const [row] = askedRows(pod, [
        { conversation_id: "c9", conversation_title: "Local build", kind: "PERMISSION", summary: "Run npm test", created_at: "2026-10-01T09:00:00Z" },
    ]);
    assert.equal(row.kind, "permission");
    assert.equal(sayAsked(row), "Asks permission to go on");
    assert.equal(askedHref(row), "/t/p1/conversation/c9");
});

test("conversation asks badge the teammate the same way workflow forms do", () => {
    const owed = owedFrom(
        [{ podId: "p1", workflowName: "Refund review" }],
        [{ podId: "p1", title: "Tracker refresh" }, { podId: "p2", title: "Launch post" }],
    );
    assert.equal(owed.get("p1")?.count, 2);
    assert.equal(sayOwed(owed.get("p2")!), "Launch post is waiting on you");
});

test("tenure is a date and who hired it, never a count", () => {
    const now = new Date("2026-10-01T12:00:00Z");
    const members = [{ userId: "u1", name: "Priya" }, { userId: "me", name: "You" }];
    assert.match(sayHired({ hiredAt: "2026-03-12T09:00:00Z", hiredBy: "u1", members }, now), /^Hired (12 March|March 12) by Priya$/);
    assert.match(sayHired({ hiredAt: "2026-03-12T09:00:00Z", hiredBy: "me", members }, now), /by you$/);
    assert.match(sayHired({ hiredAt: "2025-03-12T09:00:00Z", hiredBy: "gone", members }, now), /^Hired (12 March 2025|March 12, 2025)$/);
    assert.equal(sayHired({ members }, now), "");
});

test("an ask left for over a week goes quiet: listed, but no longer counted", () => {
    const now = Date.parse("2026-10-01T12:00:00Z");
    const row = (id: string, daysAgo: number) => ({ podId: "p1", podName: "Kit", conversationId: id, title: id, kind: "question" as const, summary: null, sinceMs: now - daysAgo * 86_400_000 });
    const { fresh, quiet } = byAge([row("today", 0), row("six", 6), row("eight", 8), row("three-weeks", 21)], now);
    assert.deepEqual(fresh.map((one) => one.conversationId), ["today", "six"]);
    assert.deepEqual(quiet.map((one) => one.conversationId), ["eight", "three-weeks"]);
});
