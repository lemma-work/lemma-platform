import assert from "node:assert/strict";
import { test } from "node:test";

import { QueryClient } from "@tanstack/react-query";
import { KEPT_FOR_MS, keptOnDevice, keptState, readable } from "../src/session/device-cache.ts";
import { channelsKey } from "../src/channels/queries.ts";

test("the device keeps lists that answered, and no change still on its way, not even a paused one", () => {
    const client = new QueryClient();
    client.setQueryData(["pods", "org-1"], [{ id: "pod-1" }]);
    client.setQueryData(["file", "pod-1", "/notes.md"], "what is inside");
    /* A message sent offline waits as a paused mutation, its words in it. */
    client.getMutationCache().build(client, { mutationKey: ["send"] }, {
        context: undefined, data: undefined, error: null, failureCount: 0, failureReason: null,
        isPaused: true, status: "pending", variables: { text: "a private message" }, submittedAt: 0,
    });
    const state = keptState(client);
    assert.deepEqual(state.queries.map((query) => query.queryKey), [["pods", "org-1"]]);
    assert.deepEqual(state.mutations, []);
});

test("the device keeps the frame's lists, and nothing that is inside a chat, a file or a table", () => {
    for (const kept of [
        ["orgs"],
        ["current-user"],
        ["pods", "org-1"],
        ["conversations", "pod-1", "outcome-activity"],
        channelsKey("pod-1", { archived: false }),
        ["conversation-asks", "pod-1"],
        ["workflow-waiting", "pod-1,pod-2"],
        ["workflows", "pod-1", "names"],
        ["outcome-apps", "pod-1"],
        ["outcome-tables", "pod-1"],
    ]) assert.equal(keptOnDevice(kept), true, JSON.stringify(kept));

    for (const left of [
        ["conversations", "pod-1"],
        ["channel", "pod-1", "lines", "ch-1"],
        ["channel", "pod-1", "detail", "ch-1"],
        ["file", "pod-1", "/notes.md"],
        ["outcome-plan", "pod-1"], // its answer carries the whole of plan.md
        ["outcome-measure", "pod-1", "select 1"],
        ["computer", "cwd", "pod-1", "c-1"],
        [42],
        [],
    ]) assert.equal(keptOnDevice(left), false, JSON.stringify(left));
});

test("what the device kept is read back only for the person, the API and the build it was kept for, within a week", () => {
    const now = Date.parse("2026-11-05T12:00:00Z");
    const record = { build: "b1", api: "https://api.example", owner: "u1", at: now - 60_000, state: { queries: [], mutations: [] } };
    const read = (one: unknown, who: string | null = "u1", at = now) => readable(one, at, who, "b1", "https://api.example");

    assert.equal(read(record), true);
    assert.equal(read(record, "u2"), false, "someone else signed in");
    assert.equal(read(record, null), false, "nobody signed in");
    assert.equal(read({ ...record, build: "b0" }), false, "an older build");
    assert.equal(read({ ...record, api: "http://localhost:8710" }), false, "another API");
    assert.equal(read({ ...record, at: now - KEPT_FOR_MS - 1 }), false, "more than a week old");
    assert.equal(read({ ...record, at: now + 60_000 }), false, "from a clock that ran ahead");
    assert.equal(read({ ...record, state: null }), false);
    assert.equal(read(undefined), false);
    assert.equal(read("frame"), false);
});
