import assert from "node:assert/strict";
import { test } from "node:test";

import { hydrate, QueryClient, QueryObserver } from "@tanstack/react-query";
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

test("a list whose last read failed is kept as the answer it holds, and one that never answered is not", async () => {
    const client = new QueryClient();
    const offline = () => Promise.reject(new Error("the API is failing"));
    client.setQueryData(["pods", "org-1"], [{ id: "pod-1" }]);
    await client.fetchQuery({ queryKey: ["pods", "org-1"], queryFn: offline, retry: false, staleTime: 0 }).catch(() => undefined);
    await client.fetchQuery({ queryKey: ["orgs"], queryFn: offline, retry: false }).catch(() => undefined);
    assert.equal(client.getQueryState(["pods", "org-1"])?.status, "error");

    const state = keptState(client);
    assert.deepEqual(state.queries.map((query) => query.queryKey), [["pods", "org-1"]]);

    const next = new QueryClient();
    hydrate(next, JSON.parse(JSON.stringify(state)));
    assert.deepEqual(next.getQueryData(["pods", "org-1"]), [{ id: "pod-1" }]);
    assert.equal(next.getQueryState(["pods", "org-1"])?.status, "success");
    assert.equal(next.getQueryState(["pods", "org-1"])?.error, null);
});

test("a kept list is read again once the frame shows it, however recently it was kept", async () => {
    const client = new QueryClient();
    client.setQueryData(["pods", "org-1"], [{ id: "pod-1" }]);
    const keptAt = client.getQueryState(["pods", "org-1"])?.dataUpdatedAt ?? 0;

    /* Read back moments later: well inside the list's ten minutes. */
    const next = new QueryClient();
    hydrate(next, JSON.parse(JSON.stringify(keptState(client))));
    let reads = 0;
    const observer = new QueryObserver(next, {
        queryKey: ["pods", "org-1"],
        queryFn: async () => {
            reads += 1;
            return [{ id: "pod-2" }];
        },
        staleTime: 10 * 60_000,
    });
    /* Drawn at once from what was kept, and stale, so it is read again. */
    assert.deepEqual(observer.getCurrentResult().data, [{ id: "pod-1" }]);
    assert.equal(observer.getCurrentResult().isStale, true);
    assert.equal(next.getQueryState(["pods", "org-1"])?.dataUpdatedAt, keptAt);

    const stop = observer.subscribe(() => undefined);
    await new Promise((resolve) => setTimeout(resolve, 20));
    stop();
    assert.equal(reads, 1);
    assert.deepEqual(next.getQueryData(["pods", "org-1"]), [{ id: "pod-2" }]);
});

test("the device keeps the frame's lists, and nothing that is inside a chat, a file or a table", () => {
    for (const kept of [
        ["orgs"],
        ["current-user"],
        ["pods", "org-1"],
        ["conversations", "pod-1", "outcome-activity"],
        channelsKey("pod-1", { archived: false }),
        ["conversation-asks", "pod-1"],
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
        ["workflow-waiting", "pod-1,pod-2"], // each form's schema, with values from its run
        ["workflow-waiting", "board", "pod-1"],
        ["outcome-schedules", "pod-1"], // each schedule's instruction, and the row as it arrived
        ["outcome-measure", "pod-1", "select 1"],
        ["computer", "cwd", "pod-1", "c-1"],
        [42],
        [],
    ]) assert.equal(keptOnDevice(left), false, JSON.stringify(left));
});

test("the device keeps names and titles, and none of the words said in a chat", () => {
    const client = new QueryClient();
    client.setQueryData(["conversation-asks", "organization", "org-1"], [
        {
            key: "message:m-1",
            kind: "MESSAGE",
            title: "Refund the March invoice?",
            detail: "Refund the March invoice?\nThe customer says the second charge was a mistake, on the card ending 4242.",
            choices: ["Refund it", "Ask them first"],
        },
        { key: "contact:ch-1", kind: "CONTACT", title: "Maria Chen", detail: "Asked to speak to a person about a refund", choices: [] },
    ]);
    client.setQueryData(channelsKey("pod-1", { archived: false }), {
        pageParams: [null],
        pages: [{
            nextBefore: null,
            items: [{
                id: "ch-1",
                kind: "CONTACT",
                title: "Maria Chen",
                topic: null,
                lastLine: { sender: { kind: "CONTACT", id: "p-1", name: "Maria Chen" }, text: "Where is my parcel? It was due on Monday.", createdAt: "2026-11-24T10:00:00Z" },
                attention: { since: "2026-11-24T10:00:00Z", reason: "Wants a person about the refund" },
            }],
        }],
    });
    client.setQueryData(["pods", "org-1"], [{ id: "pod-1", name: "Support", description: "Answers every customer question" }]);

    const state = keptState(client);
    const onDisk = JSON.stringify(state);
    for (const said of ["second charge", "card ending 4242", "Refund it", "Ask them first", "speak to a person", "Where is my parcel", "Wants a person"]) {
        assert.equal(onDisk.includes(said), false, said);
    }
    for (const named of ["Refund the March invoice?", "Maria Chen", "Support", "Answers every customer question"]) {
        assert.equal(onDisk.includes(named), true, named);
    }

    /* Read back, the lists draw the same, with the words left out. */
    const next = new QueryClient();
    hydrate(next, JSON.parse(onDisk));
    const [question] = next.getQueryData<{ title: string; detail: unknown; choices: unknown }[]>(["conversation-asks", "organization", "org-1"]) ?? [];
    assert.deepEqual([question?.title, question?.detail, question?.choices], ["Refund the March invoice?", null, []]);
    const chats = next.getQueryData<{ pages: { items: { lastLine: { text: string; createdAt: string }; attention: { since: string; reason: unknown } }[] }[] }>(channelsKey("pod-1", { archived: false }));
    const chat = chats?.pages[0]?.items[0];
    assert.deepEqual([chat?.lastLine.text, chat?.lastLine.createdAt], ["", "2026-11-24T10:00:00Z"]);
    assert.deepEqual(chat?.attention, { since: "2026-11-24T10:00:00Z", reason: null });
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
