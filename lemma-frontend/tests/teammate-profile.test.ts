import test, { type TestContext } from "node:test";
import assert from "node:assert/strict";
import { lemma } from "../src/session/client.ts";
import { liveSource } from "../src/data/live.ts";
import { HIRES, profileFor } from "../src/data/hires.ts";

function profileClient(context: TestContext, id: string) {
    const client = lemma(id);
    context.mock.method(client.pods, "get", async () => ({ id, name: "Research", description: "Watches competitors" }));
    context.mock.method(client.agents, "list", async () => ({ items: [{ name: "pod_default", kind: "POD_DEFAULT" }] }));
    context.mock.method(client.agents, "get", async () => ({ instruction: "Read the sources", toolsets: [] }));
    for (const resource of [client.schedules, client.apps, client.tables, client.functions, client.workflows]) {
        context.mock.method(resource, "list", async () => ({ items: [] }));
    }
    return client;
}

test("failed profile sections remain unknown while successful empty sections stay empty", async context => {
    const id = "profile-partial-failure";
    const client = profileClient(context, id);
    context.mock.method(client.apps, "list", async () => { throw new Error("offline"); });
    context.mock.method(client.tables, "list", async () => { throw new Error("forbidden"); });
    context.mock.method(client.agents, "get", async () => { throw new Error("offline"); });
    const profile = await liveSource.getProfile(id);
    assert.deepEqual(profile.unavailable?.sort(), ["apps", "tables", "tools"]);
    assert.deepEqual(profile.commitments, []);
    assert.equal(profile.counts.workflows, 0);
    assert.equal(profile.name, "Research");
});

test("retrying a failed profile read clears unavailable states", async context => {
    const id = "profile-retry";
    const client = profileClient(context, id);
    let failed = true;
    context.mock.method(client.apps, "list", async () => {
        if (failed) throw new Error("offline");
        return { items: [{ id: "app-1", name: "research", status: "ACTIVE" }] };
    });
    assert.deepEqual((await liveSource.getProfile(id)).unavailable, ["apps"]);
    failed = false;
    const profile = await liveSource.getProfile(id);
    assert.deepEqual(profile.unavailable, []);
    assert.equal(profile.projects.length, 1);
    assert.equal(profile.projects[0].tabId, "app:research");
});

test("a failed teammate read rejects rather than inventing an empty profile", async context => {
    const id = "profile-missing-teammate";
    const client = profileClient(context, id);
    context.mock.method(client.pods, "get", async () => { throw new Error("missing teammate"); });
    await assert.rejects(liveSource.getProfile(id), /missing teammate/);
});

test("candidate profiles describe proposals rather than active schedules or installed apps", () => {
    for (const hire of HIRES) {
        const profile = profileFor(hire);
        assert.ok(profile.commitments.every(schedule => !schedule.active));
        assert.ok(profile.projects.every(app => app.status === "suggested"));
        assert.deepEqual(profile.counts, { tables: 0, functions: 0, workflows: 0 });
    }
});

test("pod labels are readable while resource identifiers stay unchanged", async context => {
    context.mock.method(lemma().pods, "listByOrganization", async () => ({ items: [
        { id: "sales_ops", name: "  sales_ops  " },
        { id: "daily-watch", name: "dailyWatch" },
        { id: "ai-research", name: "AI_research" },
    ] }));
    const pods = await liveSource.listPods("label-test");
    assert.deepEqual(pods.map(pod => [pod.id, pod.name, pod.teammate.name]), [
        ["sales_ops", "Sales ops", "Sales ops"],
        ["daily-watch", "Daily Watch", "Daily Watch"],
        ["ai-research", "AI research", "AI research"],
    ]);
});
