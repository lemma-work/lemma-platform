import { strict as assert } from "node:assert";
import test from "node:test";
import { askedRows, byAge, byAskedLongest } from "@/thread/waiting-on-you";
import { owedFrom, sayHired, sayOwed } from "@/space/teammates";

/** A conversation paused on a person is owed, the same as a workflow form.
 *  These pin which WAITING conversations count and how they reach the rail. */

const pod = { id: "p1", name: "Kit" };

test("a waiting conversation is owed only when a question or an approval is pending in it", () => {
    const rows = askedRows(pod, [
        { id: "c1", title: "Tracker refresh" },
        { id: "c2", title: "Sleeping until Friday" },
        { id: "c3", title: "" },
    ], new Map([
        ["c1", [{ tool_name: "ask_user", created_at: "2026-10-01T08:00:00Z" }]],
        ["c2", []],
        ["c3", [{ tool_name: "request_approval", created_at: "2026-10-01T06:00:00Z" }]],
    ]));
    assert.deepEqual(rows.map((row) => [row.conversationId, row.kind, row.title]), [
        ["c1", "question", "Tracker refresh"],
        ["c3", "approval", "A conversation"],
    ]);
    assert.deepEqual(byAskedLongest(rows).map((row) => row.conversationId), ["c3", "c1"]);
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
    const row = (id: string, daysAgo: number) => ({ podId: "p1", podName: "Kit", conversationId: id, title: id, kind: "question" as const, sinceMs: now - daysAgo * 86_400_000 });
    const { fresh, quiet } = byAge([row("today", 0), row("six", 6), row("eight", 8), row("three-weeks", 21)], now);
    assert.deepEqual(fresh.map((one) => one.conversationId), ["today", "six"]);
    assert.deepEqual(quiet.map((one) => one.conversationId), ["eight", "three-weeks"]);
});
