import test from "node:test";
import assert from "node:assert/strict";
import { isWakeFor, wakeRequest } from "../src/docpages/comments/wake.ts";
import { readSchedule } from "../src/schedule/schedules.ts";

test("an older wake-up keyed with `in` is still recognised", () => {
    const old = readSchedule({ id: "s0", name: "x", schedule_type: "DATASTORE", agent_name: "butler", config: { table_name: "doc_comments", operations: ["INSERT"], when: { mentioned_agent: { in: ["butler"] } } } });
    assert.equal(isWakeFor(old, "butler"), true);
});

test("a bot's comment wake-up is the DATASTORE schedule on the comments table naming it", () => {
    const body = wakeRequest("butler", "Butler");
    const job = readSchedule({ id: "s1", ...body, user_id: "me" });
    assert.equal(isWakeFor(job, "butler"), true);
    assert.equal(isWakeFor(job, "Butler"), true);
    assert.equal(isWakeFor(job, "researcher"), false);
    const other = readSchedule({ id: "s2", name: "x", schedule_type: "DATASTORE", agent_name: "butler", config: { table_name: "leads", operations: ["INSERT"] } });
    assert.equal(isWakeFor(other, "butler"), false);
});

test("the request fires on a new or re-asked mention, and tells the bot how to reply without waking itself", () => {
    const body = wakeRequest("POD_DEFAULT", "Lem") as { config: { operations: string[]; when: unknown }; instruction: string; agent_name: string };
    assert.deepEqual(body.config.operations, ["INSERT", "UPDATE"]);
    assert.deepEqual(body.config.when, { mentioned_agent: { to: "POD_DEFAULT" } });
    assert.equal(body.agent_name, "POD_DEFAULT");
    assert.match(body.instruction, /mentioned_agent left empty/);
});
