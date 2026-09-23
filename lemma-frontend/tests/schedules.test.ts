import test from "node:test";
import assert from "node:assert/strict";
import {
    CADENCES, MINIMUM_MINUTES, RETRYABLE, SCHEDULE_EDIT,
    agoOf, blankDraft, canRetry, createRequest, describeCron, draftProblems, healthOf, may,
    readRun, readRuns, readSchedule, readSchedules, triggerOf,
} from "../src/schedule/schedules.ts";

/* A schedule as `ScheduleDetailResponse` serialises one. Every test below
   starts from this and changes the one field it is about. */
const wire = {
    id: "s1",
    user_id: "u1",
    pod_id: "p1",
    name: "daily_tracker_refresh",
    schedule_type: "TIME",
    agent_id: "a1",
    workflow_id: null,
    agent_name: "researcher",
    workflow_name: null,
    config: { cron: "0 9 * * 1-5", timezone: "Europe/Berlin" },
    instruction: "Re-read the five competitors.",
    account_id: null,
    connector_trigger_id: null,
    filter_instruction: null,
    filter_output_schema: null,
    visibility: "POD",
    is_active: true,
    is_internal: false,
    paused_by_failures: false,
    last_fired_at: "2026-09-19T09:00:00Z",
    last_run_id: "r9",
    last_fire_status: "TRIGGERED",
    last_error: null,
    consecutive_failures: 0,
    created_at: "2026-04-20T09:00:00Z",
    updated_at: "2026-09-19T09:00:00Z",
    allowed_actions: ["schedule.read", "schedule.update"],
};

test("a schedule says what fires it and what it runs", () => {
    const job = readSchedule(wire);

    assert.equal(job.title, "Daily tracker refresh");
    assert.equal(job.trigger, "Every weekday at 09:00 Europe/Berlin");
    // The sentence is a reading; the cron is the fact, and both are kept.
    assert.equal(job.triggerLiteral, "0 9 * * 1-5");
    assert.deepEqual(job.target, { kind: "agent", name: "researcher", label: "Researcher" });
    assert.equal(job.broken, false);
});

test("the pod's own assistant is Lem, never `POD_DEFAULT`", () => {
    // The one name this product deliberately never shows a person. Reading it
    // raw here would put "Pod default" on a profile.
    const job = readSchedule({ ...wire, agent_name: "POD_DEFAULT" });
    assert.equal(job.target.label, "Lem");
    assert.equal(job.target.name, "POD_DEFAULT");
});

test("a workflow target is named as one, so agent and workflow are tellable apart", () => {
    const job = readSchedule({ ...wire, agent_name: null, agent_id: null, workflow_name: "press_triage", workflow_id: "w1" });
    assert.deepEqual(job.target, { kind: "workflow", name: "press_triage", label: "Press triage" });
});

test("a target you may not read is still a target; a missing one is a fault", () => {
    const hidden = readSchedule({ ...wire, agent_name: null });
    assert.equal(hidden.target.kind, "agent");
    assert.equal(hidden.target.label, "an agent");

    const wiredToNothing = readSchedule({ ...wire, agent_name: null, agent_id: null });
    assert.equal(wiredToNothing.target.kind, "none");
    assert.equal(healthOf(wiredToNothing).tone, "bad");
});

test("nothing in a payload can make a row throw", () => {
    for (const nonsense of [null, undefined, "a schedule", 42, [], { config: null }]) {
        const job = readSchedule(nonsense);
        assert.equal(job.broken, true);
        assert.equal(job.title, "Standing work");
        // A broken row offers no controls: there is no id to aim a call at.
        assert.equal(may(job, SCHEDULE_EDIT), false);
        assert.equal(healthOf(job).tone, "bad");
    }
});

test("a schedule type this build does not know still draws", () => {
    const job = readSchedule({ ...wire, schedule_type: "CARRIER_PIGEON", config: {} });
    assert.equal(job.kind, "UNKNOWN");
    assert.equal(job.broken, false);
    assert.match(job.trigger, /does not know/);
});

test("a webhook says its source; a datastore says its table and operations", () => {
    assert.equal(
        triggerOf("WEBHOOK", { source: "slack" }, "slack_message_posted").trigger,
        "When slack sends something",
    );
    assert.equal(
        triggerOf("WEBHOOK", { source: "slack" }, "slack_message_posted").literal,
        "slack_message_posted",
    );
    const rows = triggerOf("DATASTORE", { table_name: "contacts", operations: ["INSERT", "UPDATE"], when: { status: { eq: "New" } } }, "");
    assert.equal(rows.trigger, "When a row in contacts insert or update");
    assert.equal(rows.literal, "only when status matches");
});

test("a cron is only paraphrased where the paraphrase is true", () => {
    assert.equal(describeCron("0 9 * * 1-5"), "Every weekday at 09:00");
    assert.equal(describeCron("0 7 * * *"), "Every day at 07:00");
    assert.equal(describeCron("30 6 * * 1"), "On Mondays at 06:30");
    assert.equal(describeCron("*/30 * * * *"), "Every 30 minutes");
    assert.equal(describeCron("0 * * * *"), "Every hour");
    assert.equal(describeCron("15 * * * *"), "Every hour at 15 past");
    // Five fields is what `CronSchedule.parse` takes. A six-field expression is
    // not a cron this platform accepts, so it is not read as one either.
    assert.equal(describeCron("0 0 9 * * 1-5"), "0 0 9 * * 1-5");
    // A day-of-month or month restriction is not covered by the sentence
    // vocabulary, so the expression stands rather than a wrong sentence.
    assert.equal(describeCron("0 3 1 1,4,7,10 *"), "0 3 1 1,4,7,10 *");
    assert.equal(describeCron("nonsense"), "nonsense");
});

test("a cron with no sentence for it is not printed twice", () => {
    // It drew `0 3 1 1,4,7,10 * UTC   0 3 1 1,4,7,10 *` on the row: the
    // sentence falls back to the expression, and the expression was already
    // beside it as the literal.
    const quarterly = readSchedule({ ...wire, config: { cron: "0 3 1 1,4,7,10 *" } });
    assert.equal(quarterly.trigger, "On a cron, read in UTC");
    assert.equal(quarterly.triggerLiteral, "0 3 1 1,4,7,10 *");
});

test("an absent timezone is UTC, and the row says so", () => {
    // `resolve_zone` reads a missing key as UTC. Leaving the zone off the
    // sentence makes "every weekday at 09:00" mean a different instant
    // depending on where the reader is.
    assert.equal(readSchedule({ ...wire, config: { cron: "0 9 * * *" } }).trigger, "Every day at 09:00 UTC");
});

/* ── health ─────────────────────────────────────────────────────────── */

test("a failing schedule does not read like a healthy one", () => {
    const healthy = healthOf(readSchedule(wire));
    assert.equal(healthy.tone, "ok");
    assert.equal(healthy.line, "");

    const failing = healthOf(readSchedule({ ...wire, consecutive_failures: 4, last_fire_status: "ERROR" }));
    assert.equal(failing.tone, "bad");
    assert.match(failing.line, /4 times in a row/);
});

test("the breaker's pause and a person's pause are different sentences", () => {
    const breaker = healthOf(readSchedule({
        ...wire, is_active: false, paused_by_failures: true, consecutive_failures: 5,
    }));
    assert.equal(breaker.tone, "bad");
    assert.match(breaker.line, /Stopped by itself after 5 failures/);
    // Resuming clears the count server-side, and the line says so rather than
    // leaving somebody to wonder whether it starts at five again.
    assert.match(breaker.line, /clears the count/);

    const byHand = healthOf(readSchedule({ ...wire, is_active: false }));
    assert.equal(byHand.tone, "off");
    assert.match(byHand.line, /will not fire until somebody resumes it/);
    // The word "paused" is on the row already, as a tag. Repeating it here put
    // it on screen twice in two lines.
    assert.doesNotMatch(byHand.line, /Paused/);
});

test("a filtered fire is not a failure, and a never-fired schedule is not healthy", () => {
    assert.equal(healthOf(readSchedule({ ...wire, last_fire_status: "FILTERED" })).tone, "warn");
    assert.equal(healthOf(readSchedule({ ...wire, last_fired_at: null, last_fire_status: null })).tone, "warn");
});

test("a row that omits `is_active` is running, not paused", () => {
    const { is_active: _dropped, ...without } = wire;
    assert.equal(readSchedule(without).active, true);
});

/* ── the list ───────────────────────────────────────────────────────── */

test("workflow execution's own timers stay off a teammate's profile", () => {
    // These are the waits and timeouts a running workflow creates for itself.
    // The API's own list already excludes them; a deployment that stopped
    // would otherwise put "30 minute wait" on somebody's CV.
    const listed = readSchedules({
        items: [wire, { ...wire, id: "s2", is_internal: true }],
        limit: 100,
    });
    assert.deepEqual(listed.map((job) => job.id), ["s1"]);
});

test("a list can arrive as a bare array, or as nothing at all", () => {
    assert.equal(readSchedules([wire]).length, 1);
    assert.deepEqual(readSchedules(null), []);
    assert.deepEqual(readSchedules({ items: "not a list" }), []);
});

/* ── runs ───────────────────────────────────────────────────────────── */

const run = {
    id: "r1",
    schedule_id: "s1",
    user_id: "u1",
    source_event_id: "e1",
    status: "COMPLETED",
    attempts: 1,
    target_kind: "AGENT",
    target_run_id: "ar1",
    redrive_of_run_id: null,
    redriven_by_user_id: null,
    payload: {},
    metadata: {},
    llm_output: {},
    error_type: null,
    error_code: null,
    source_occurred_at: "2026-09-19T09:00:00Z",
    started_at: "2026-09-19T09:00:01Z",
    completed_at: "2026-09-19T09:01:30Z",
    created_at: "2026-09-19T09:00:00Z",
    updated_at: "2026-09-19T09:01:30Z",
};

test("only the three statuses a redrive accepts offer a retry", () => {
    // `create_redrive` tests `target_outcome or status` against exactly this
    // set and answers nothing for the rest, which reaches a client as a refusal
    // about a run that was never retryable.
    assert.deepEqual(RETRYABLE, ["FAILED", "DEAD_LETTERED", "TARGET_FAILED"]);
    for (const status of RETRYABLE) {
        assert.equal(canRetry(readRun({ ...run, status })), true, status + " should be retryable");
    }
    for (const status of ["COMPLETED", "FILTERED", "CANCELLED", "RECEIVED", "PROCESSING", "DISPATCHED"]) {
        assert.equal(canRetry(readRun({ ...run, status })), false, status + " should not be retryable");
    }
    assert.equal(canRetry(readRun({ status: "FAILED" })), false, "a run with no id cannot be retried");
});

test("a failed run says what failed, and a retry says it is one", () => {
    const failed = readRun({ ...run, status: "TARGET_FAILED", error_type: "TargetFailed", error_code: "NO_MATCHING_START" });
    assert.equal(failed.outcome, "The target failed");
    assert.equal(failed.tone, "bad");
    assert.equal(failed.error, "TargetFailed · NO_MATCHING_START");

    assert.equal(readRun({ ...run, redrive_of_run_id: "r0" }).retryOf, "r0");
});

test("a run with no source event falls back to when the row was written", () => {
    // A TIME schedule has no incoming event, and some webhook deliveries carry
    // no timestamp of their own. An empty column reads as "never", which is a
    // different claim from "we were not told".
    assert.equal(readRun({ ...run, source_occurred_at: null }).at, run.created_at);
});

test("a run payload cannot throw either", () => {
    for (const nonsense of [null, "a run", 7, []]) {
        const read = readRun(nonsense);
        assert.equal(read.broken, true);
        assert.equal(read.outcome, "Unreadable");
    }
    assert.deepEqual(readRuns(null), []);
    assert.equal(readRuns({ items: [run, run] }).length, 2);
});

/* ── how long ago ───────────────────────────────────────────────────── */

test("how long ago, with the clock passed in", () => {
    const now = Date.parse("2026-09-19T12:00:00Z");
    assert.equal(agoOf("2026-09-19T11:59:40Z", now), "just now");
    assert.equal(agoOf("2026-09-19T11:20:00Z", now), "40m ago");
    assert.equal(agoOf("2026-09-19T06:00:00Z", now), "6h ago");
    assert.equal(agoOf("2026-09-17T12:00:00Z", now), "2d ago");
    assert.equal(agoOf("2026-08-19T12:00:00Z", now), "4w ago");
    assert.equal(agoOf("", now), "");
    assert.equal(agoOf("not a date", now), "");
});

/* ── making one ─────────────────────────────────────────────────────── */

test("a draft is refused for the things the API would refuse it for", () => {
    const empty = draftProblems({ ...blankDraft(), cron: "" });
    assert.ok(empty.name);
    assert.ok(empty.cron);
    assert.ok(empty.target);

    // Five fields, because that is what `CronSchedule.parse` takes.
    assert.match(draftProblems({ ...blankDraft(), cron: "0 0 9 * * 1" }).cron, /Five fields/);
    // The platform's floor. Telling somebody after the round trip is telling
    // them after they have written the whole thing.
    assert.match(draftProblems({ ...blankDraft(), cron: "* * * * *" }).cron, new RegExp(String(MINIMUM_MINUTES)));
    assert.match(draftProblems({ ...blankDraft(), cron: "*/5 * * * *" }).cron, new RegExp(String(MINIMUM_MINUTES)));

    const good = draftProblems({ ...blankDraft(), name: "Digest", agentName: "researcher" });
    assert.deepEqual(good, {});
});

test("every offered cadence is one the platform would accept", () => {
    for (const cadence of CADENCES) {
        const wrong = draftProblems({ ...blankDraft(), name: "x", agentName: "y", cron: cadence.cron });
        assert.deepEqual(wrong, {}, cadence.label + " produces a cron the floor refuses");
        // And the words in the picker have to be the words the row will show
        // afterwards, or the picker is describing something it did not write.
        assert.equal(describeCron(cadence.cron), cadence.label);
        assert.notEqual(cadence.label, cadence.cron, cadence.cron + " has no sentence, so it should not be offered as one");
    }
});

test("the create body sends exactly one target name", () => {
    // `require_one_target_name` rejects both and neither, and a key present
    // with a null value counts as present.
    const asAgent = createRequest({ ...blankDraft(), name: "Digest", agentName: "researcher" });
    assert.equal(asAgent.agent_name, "researcher");
    assert.equal("workflow_name" in asAgent, false);

    const asWorkflow = createRequest({ ...blankDraft(), name: "Digest", target: "workflow", workflowName: "press_triage" });
    assert.equal(asWorkflow.workflow_name, "press_triage");
    assert.equal("agent_name" in asWorkflow, false);
});

test("an unset timezone is left off rather than written as UTC", () => {
    // Absence and the literal "UTC" behave identically server-side, so writing
    // it would give every exported pod bundle a diff to no effect.
    const bare = createRequest({ ...blankDraft(), name: "Digest", agentName: "a" });
    assert.deepEqual(bare.config, { cron: "0 9 * * 1-5" });

    const zoned = createRequest({ ...blankDraft(), name: "Digest", agentName: "a", timezone: " Europe/Berlin " });
    assert.deepEqual(zoned.config, { cron: "0 9 * * 1-5", timezone: "Europe/Berlin" });
});

test("an empty instruction is left off, not sent as an empty string", () => {
    const bare = createRequest({ ...blankDraft(), name: "Digest", agentName: "a", instruction: "   " });
    assert.equal("instruction" in bare, false);
    assert.equal(createRequest({ ...blankDraft(), name: "D", agentName: "a", instruction: "Do it" }).instruction, "Do it");
});
