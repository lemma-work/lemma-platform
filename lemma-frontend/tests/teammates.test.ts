import test from "node:test";
import assert from "node:assert/strict";
import { byNeed, needsYou, owedByPod, railLine, sayOwed } from "../src/space/teammates.ts";

/** The rail's badge, a card's line and the "Needs you" group all read one
 *  answer. These pin what that answer is. */

const QUEUE = [
    { podId: "kit", workflowName: "Publish approval" },
    { podId: "remy", workflowName: "Pricing sign-off" },
    { podId: "kit", workflowName: "Asset review" },
];

test("a teammate is waiting on you for the oldest thing first, and says how many", () => {
    const owed = owedByPod(QUEUE);

    assert.deepEqual(owed.get("kit"), { count: 2, first: "Publish approval" });
    assert.deepEqual(owed.get("remy"), { count: 1, first: "Pricing sign-off" });
    assert.equal(owed.has("june"), false);
});

test("one thing is named; several are counted", () => {
    assert.equal(sayOwed({ count: 1, first: "Publish approval" }), "Publish approval is waiting on you");
    assert.equal(sayOwed({ count: 3, first: "Publish approval" }), "3 things are waiting on you");
});

test("a hand-written waiting line counts only where somebody wrote one", () => {
    const owed = owedByPod([]);

    // The live source leaves `waiting` empty, so only the queue can say so there.
    assert.equal(needsYou({ id: "june", waiting: "" }, owed), false);
    assert.equal(needsYou({ id: "june", waiting: "  " }, owed), false);
    assert.equal(needsYou({ id: "kit", waiting: "2 assets ready · 1 decision" }, owed), true);
    assert.equal(needsYou({ id: "kit", waiting: "" }, owedByPod(QUEUE)), true);
});

test("the rail says what a teammate needs, or else what it is for", () => {
    const owed = owedByPod(QUEUE);

    assert.equal(railLine({ id: "remy", waiting: "", description: "Sales follow-through" }, owed), "Pricing sign-off is waiting on you");
    assert.equal(railLine({ id: "june", waiting: "", description: "Customer onboarding" }, owed), "Customer onboarding");
    assert.equal(railLine({ id: "scout", waiting: "" }, owed), "");
});

test("two groups and never more, each in the order it came", () => {
    const pods = [
        { id: "june", waiting: "" },
        { id: "kit", waiting: "" },
        { id: "scout", waiting: "1 finding to challenge" },
        { id: "remy", waiting: "" },
    ];
    const { needs, rest } = byNeed(pods, owedByPod(QUEUE));

    assert.deepEqual(needs.map((pod) => pod.id), ["kit", "scout", "remy"]);
    assert.deepEqual(rest.map((pod) => pod.id), ["june"]);
});
