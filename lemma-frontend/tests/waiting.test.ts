import { test } from "node:test";
import assert from "node:assert/strict";
import {
    badgeCount,
    choicesOf,
    formTarget,
    moveFor,
    readInbox,
    waitingSchema,
    whereAsked,
} from "../src/channels/waiting.ts";

const inbox = readInbox({
    totals: { channels: 1, mentions: 0, waiting: 3 },
    waiting: [
        {
            channel: { id: "c1", kind: "GROUP", title: "launch" },
            message: {
                id: "m1",
                sender: { kind: "AGENT", id: "a", name: "Lem" },
                text: "Which day?",
                created_at: "2026-10-18T10:00:00Z",
                question: {
                    kind: "ASK",
                    question: { questions: [{ header: "day", options: [{ label: "Thu" }, { label: "Fri" }] }] },
                },
            },
        },
        {
            channel: { id: "c2", kind: "DIRECT", title: null },
            message: {
                id: "m2",
                sender: { kind: "AGENT", id: null, name: null },
                text: "May I send it?",
                created_at: "2026-10-18T10:00:00Z",
                question: { kind: "APPROVAL", question: {} },
            },
        },
        {
            channel: { id: "c2", kind: "DIRECT", title: null },
            message: {
                id: "m3",
                sender: { kind: "AGENT", id: null, name: "Lem" },
                text: "A form",
                created_at: "2026-10-18T10:00:00Z",
                question: { kind: "FORM", question: { run_id: "r", node_id: "n" } },
            },
        },
        { channel: {}, message: { id: "skipped" } },
    ],
});

test("an inbox reads every question and skips what is not one", () => {
    assert.deepEqual(inbox.items.map((one) => one.id), ["m1", "m2", "m3"]);
    assert.equal(inbox.waiting, 3);
    assert.equal(inbox.items[1].from, "An agent");
});

test("each question is answered its own way", () => {
    assert.deepEqual(inbox.items.map(moveFor), ["choose", "approve", "form"]);
    assert.deepEqual(choicesOf(inbox.items[0]), ["Thu", "Fri"]);
    assert.deepEqual(formTarget(inbox.items[2]), { runId: "r", nodeId: "n" });
});

test("a form with no run to submit against is answered in words", () => {
    const loose = { ...inbox.items[2], question: {} };
    assert.equal(moveFor(loose), "write");
});

test("where it was asked is said plainly", () => {
    assert.equal(whereAsked(inbox.items[0]), "In launch");
    assert.equal(whereAsked(inbox.items[1]), "Sent to you");
});

test("the badge says nothing, a count, or 99+", () => {
    assert.equal(badgeCount(0), null);
    assert.equal(badgeCount(4), "4");
    assert.equal(badgeCount(140), "99+");
});

test("a form is drawn only while the run still waits on its node", () => {
    const run = { active_wait: { node_id: "n", payload: { input_schema: { type: "object" } } } };
    assert.deepEqual(waitingSchema(run, "n"), { type: "object" });
    assert.equal(waitingSchema(run, "other"), null);
});
