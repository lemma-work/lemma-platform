import test from "node:test";
import assert from "node:assert/strict";
import { automationOf, runsForOf, schedulesFor, turnOnOf, turnOnRequest } from "../src/workflow/turn-on.ts";
import { readSchedule } from "../src/schedule/schedules.ts";

const job = (over: Record<string, unknown>) => readSchedule({
    id: "s" + Math.random(), name: "x", schedule_type: "TIME", workflow_name: "digest", workflow_id: "w1",
    config: { cron: "0 9 * * 1" }, visibility: "PERSONAL", is_active: true, user_id: "me", ...over,
});

test("a workflow's start says what turning it on means", () => {
    assert.deepEqual(automationOf({ type: "MANUAL" }), { kind: "manual" });
    assert.deepEqual(automationOf({ type: "SCHEDULED", config: { schedule_type: "CRON" } }), { kind: "time" });
    assert.deepEqual(automationOf({ type: "EVENT", config: { connector_id: "xero", connector_trigger_id: "period_closed" } }), { kind: "event", connectorId: "xero", triggerId: "period_closed" });
    assert.deepEqual(automationOf({ type: "DATASTORE_EVENT", config: { table_name: "leads", operations: ["INSERT"] } }), { kind: "rows", table: "leads", operations: ["INSERT"] });
    assert.deepEqual(automationOf(null), { kind: "manual" });
});

test("per person, only your own schedule turns it on for you", () => {
    const time = { kind: "time" } as const;
    const theirs = job({ user_id: "priya", visibility: "POD" });
    assert.deepEqual(turnOnOf(time, true, [theirs], "me"), { status: "off", forYou: true });
    const mine = job({});
    const on = turnOnOf(time, true, [theirs, mine], "me");
    assert.equal(on.status, "on");
    assert.equal(on.status === "on" && on.byYou, true);
});

test("space-wide, anyone's live schedule is the answer; a paused one is stopped", () => {
    const time = { kind: "time" } as const;
    const theirs = job({ user_id: "priya", visibility: "POD" });
    const on = turnOnOf(time, false, [theirs], "me");
    assert.equal(on.status, "on");
    assert.equal(on.status === "on" && on.byYou, false);
    const paused = turnOnOf(time, false, [job({ is_active: false })], "me");
    assert.equal(paused.status, "stopped");
    assert.deepEqual(turnOnOf(time, false, [], "me"), { status: "off", forYou: false });
    assert.deepEqual(turnOnOf({ kind: "manual" }, true, [theirs], "me"), { status: "manual" });
});

test("a webhook without its account is not live", () => {
    const event = { kind: "event", connectorId: "xero", triggerId: "t" } as const;
    const unwired = job({ schedule_type: "WEBHOOK", account_id: null, connector_trigger_id: null });
    assert.equal(turnOnOf(event, true, [unwired], "me").status, "stopped");
});

test("the request is personal per person, space-wide otherwise, and refuses what it lacks", () => {
    assert.deepEqual(turnOnRequest("digest", { kind: "time" }, true, { cron: "0 9 * * 1" }), {
        workflow_name: "digest", visibility: "PERSONAL", schedule_type: "TIME", config: { cron: "0 9 * * 1" },
    });
    assert.equal(turnOnRequest("digest", { kind: "time" }, true, {}), null);
    assert.deepEqual(turnOnRequest("audit", { kind: "event", connectorId: "xero", triggerId: "t" }, false, { accountId: "a1" }), {
        workflow_name: "audit", visibility: "POD", schedule_type: "WEBHOOK", config: {}, account_id: "a1",
    });
    assert.equal(turnOnRequest("audit", { kind: "event", connectorId: "xero", triggerId: "t" }, false, {}), null);
    assert.deepEqual(turnOnRequest("leads", { kind: "rows", table: "leads", operations: [] }, true, {}), {
        workflow_name: "leads", visibility: "POD", schedule_type: "DATASTORE", config: { table_name: "leads", operations: ["INSERT"] },
    });
});

test("schedules are matched to their workflow by name, and who it runs for is a sentence", () => {
    assert.equal(schedulesFor([job({}), job({ workflow_name: "other" })], "digest").length, 1);
    assert.match(runsForOf({ kind: "rows", table: "x", operations: [] }, true), /row/);
    assert.match(runsForOf({ kind: "time" }, true), /Each person/);
});

import { copyNeedOf, copyRequest } from "../src/schedule/schedules.ts";

test("a copy is personal, keeps the trigger and target, and asks for an account only on a webhook", () => {
    const time = readSchedule({ id: "t", name: "digest", schedule_type: "TIME", agent_name: "researcher", agent_id: "a", config: { cron: "0 9 * * 1", timezone: "Europe/Berlin" }, instruction: "Summarise", visibility: "POD", user_id: "priya" });
    assert.equal(copyNeedOf(time), "none");
    assert.deepEqual(copyRequest(time), {
        schedule_type: "TIME", config: { cron: "0 9 * * 1", timezone: "Europe/Berlin" }, visibility: "PERSONAL",
        agent_name: "researcher", instruction: "Summarise",
    });
    const hook = readSchedule({ id: "w", name: "press", schedule_type: "WEBHOOK", agent_name: "researcher", agent_id: "a", config: { channel: "C1" }, account_id: "theirs", connector_trigger_id: "slack_message_posted", visibility: "POD", user_id: "priya" });
    assert.equal(copyNeedOf(hook), "account");
    assert.equal(copyRequest(hook), null);
    const mine = copyRequest(hook, "mine")!;
    assert.equal(mine.account_id, "mine");
    assert.equal(mine.connector_trigger_id, "slack_message_posted");
    const rows = readSchedule({ id: "d", name: "greet", schedule_type: "DATASTORE", agent_name: "POD_DEFAULT", config: { table_name: "leads" }, visibility: "POD" });
    assert.equal(copyNeedOf(rows), "not-needed");
    assert.equal(copyRequest(rows), null);
});
