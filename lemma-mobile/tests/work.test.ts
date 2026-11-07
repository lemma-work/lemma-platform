import { describe, expect, it } from "vitest";
import { hrefOfNeed, workRows } from "../src/data/model";
import { SAMPLE_LEMMAS } from "../src/data/sample";
import {
    accessOf, addLine, draftOf, editLine, fieldsOf, flowLine, flowSummaries, markOf, metaOf, personOf, planToWrite, PlanMovedOn, removeLine, runMeta, runsLine,
    healthMark, phoneWords, runsFor, sayAge, sayPermissions, sayRunLine, scheduleGroups, SCOPE_NOTE, shownOf, skillPath, splitRuns, stampAt, stepCount, stepNames, surfacesFor, surfaceState, usedBy, whatStarts, withMeta, wordsOf, xrayGroups,
} from "../src/areas/work/model";
import { NEW_SCHEDULE, workRoutes } from "../src/areas/work/routes";
import { workSample } from "../src/areas/work/sample";
import type { Member, Surface } from "../src/areas/work/source";
import { readAgentDetail, readAgentRow } from "@web/data/agents";
import { boardOf } from "@web/workflow/board";
import { parsePlan, serializePlan } from "../src/areas/work/web/plan";
import { buildTree, readGraph } from "@web/workflow/run-tree";
import { readRun, type RunRow } from "@web/workflow/runs";
import { readSchedule } from "@web/schedule/schedules";
import { readShape } from "@web/workflow/shape";
import { automationOf } from "@web/workflow/turn-on";

const [SUPPORT, LAUNCH, , , BOOKS] = SAMPLE_LEMMAS;
const NOW = Date.parse("2026-10-24T12:00:00Z");
const MIN = 60_000;

function run(extra: Partial<Record<string, unknown>> = {}): RunRow {
    return readRun({ id: "r1", workflow_id: "w1", status: "COMPLETED", started_at: new Date(NOW - 10 * MIN).toISOString(), completed_at: new Date(NOW - 8 * MIN).toISOString(), start_type: "MANUAL", ...extra })!;
}

/* ── the copies stay the web's ───────────────────────────────────────── */

/* ── rows and lines ──────────────────────────────────────────────────── */

describe("a run in a line", () => {
    it("names the step a run died at, by its label, with the error", () => {
        expect(sayRunLine(run({ status: "FAILED", failed_node_id: "find", error: "Timed out" }), new Map([["find", "Find tickets"]]))).toBe("Failed at Find tickets: Timed out");
    });
    it("says where a going run is, and how a finished one started", () => {
        expect(sayRunLine(run({ status: "RUNNING", current_node_id: "reply" }))).toBe("At reply");
        expect(sayRunLine(run({ start_type: "DATASTORE_EVENT" }))).toBe("Started when a row changed");
    });
    it("marks a run violet only when it is yours or moving, amber when it failed", () => {
        expect(markOf(run({ status: "WAITING" }), true)).toBe("yours");
        expect(markOf(run({ status: "WAITING" }), false)).toBe("quiet");
        expect(markOf(run({ status: "RUNNING" }), false)).toBe("going");
        expect(markOf(run({ status: "FAILED" }), false)).toBe("failed");
        expect(markOf(run(), false)).toBe("quiet");
    });
    it("counts a finished run's steps as ran, a going one's as of the whole", () => {
        expect(runMeta(run(), { done: 3, total: 5 }, "you", NOW)).toBe("3 steps ran · Started by hand by you · 10m ago · took 2m");
        expect(runMeta(run({ status: "RUNNING", completed_at: null }), { done: 1, total: 4 }, null, NOW)).toBe("1 of 4 steps · Started by hand · 10m ago · going for 10m");
    });
    it("names a person by user id, the reader as you", () => {
        const members: Member[] = [{ id: "m1", userId: "u1", name: "Sam" }];
        expect(personOf("me", "me", members)).toBe("you");
        expect(personOf("u1", "me", members)).toBe("Sam");
        expect(personOf("u9", "me", members)).toBeNull();
        expect(personOf(null, "me", members)).toBeNull();
    });
    it("stamps zoneless wire times as UTC", () => {
        expect(stampAt("2026-10-24T11:56:00", NOW)).toBe("4m");
        expect(stampAt(null, NOW)).toBeNull();
    });
});

describe("the Runs tab", () => {
    const flows = [
        { id: "w1", name: "triage", description: null, steps: 3, kinds: [], active: true, updated: null, may: [], perPerson: false, targets: ["agent:writer", "agent:gone", "agent:POD_DEFAULT"] },
        { id: "w2", name: "weekly", description: null, steps: 1, kinds: [], active: false, updated: null, may: [], perPerson: true, targets: [] },
    ];
    const runs = [run({ id: "a", status: "RUNNING" }), run({ id: "b", status: "WAITING" }), run({ id: "c", status: "FAILED", started_at: new Date(NOW - 60 * MIN).toISOString() })];
    const waiting = [{ wait: { id: "x", runId: "b", workflowId: "w1", podId: "p", nodeId: "ask", type: "HUMAN" as const, schema: {}, uiSchema: null, createdAt: null, externalRef: null, agentName: null, question: null }, run: runs[1] }];
    const job = readSchedule({ id: "s", name: "hourly", schedule_type: "TIME", config: { cron: "0 * * * *" }, workflow_name: "triage", is_active: true });

    it("sums each workflow up from the reads the tab holds", () => {
        const [one, two] = flowSummaries({ flows, runs, waiting, schedules: [job], agentNames: ["writer"] });
        expect(one).toMatchObject({ runs: 3, waiting: 1, going: true, schedules: 1, missing: ["gone"] });
        expect(one.last?.id).toBe("a");
        expect(two).toMatchObject({ runs: 0, waiting: 0, going: false, last: null });
        expect(flowLine(one, true, NOW)).toBe("Admin · Needs setup · last ran 10m ago · 1 schedule on");
        expect(flowLine(two, true, NOW)).toBe("Each person · Paused · never run");
        expect(flowLine(two, false, NOW)).toBe("Each person · Paused · no recent runs");
    });
    it("keeps a run waiting on you out of Running", () => {
        const split = splitRuns(runs, waiting);
        expect(split.running.map((one) => one.id)).toEqual(["a"]);
        expect(split.history.map((one) => one.id)).toEqual(["c"]);
    });
    it("groups schedules into yours and shared only once the reader is known", () => {
        const theirs = readSchedule({ id: "t", name: "theirs", schedule_type: "TIME", config: { cron: "0 9 * * *" }, agent_name: "POD_DEFAULT", user_id: "sam" });
        const mine = readSchedule({ id: "m", name: "mine", schedule_type: "TIME", config: { cron: "0 9 * * *" }, agent_name: "x", user_id: "me" });
        expect(scheduleGroups([theirs, mine], null)).toEqual([]);
        expect(scheduleGroups([theirs, mine], "me").map((group) => [group.key, group.rows.map((row) => row.id)])).toEqual([["mine", ["m"]], ["space", ["t"]]]);
        expect(runsLine(theirs, "Support")).toBe("Support does this itself");
        expect(runsLine(job, "Support")).toBe("Runs the workflow Triage");
    });
});

/* ── a workflow, a run ───────────────────────────────────────────────── */

describe("a workflow", () => {
    it("says what starts it", () => {
        expect(whatStarts(automationOf({ type: "DATASTORE_EVENT", config: { table_name: "tickets", operations: ["INSERT"] } }))).toBe("A row added in tickets.");
        expect(whatStarts(automationOf({ type: "EVENT", config: { connector_id: "slack", connector_trigger_id: "message_posted" } }))).toBe("An event in slack — message posted.");
        expect(whatStarts(automationOf(null))).toBe("Someone presses Run now.");
    });
    it("counts steps without the end, and names them by label", async () => {
        const graph = await workSample().workflow(SUPPORT.id, "triage_new_tickets");
        const shape = readShape(graph);
        expect(stepCount(shape, null)).toBe(4);
        expect(stepCount(null, 7)).toBe(7);
        expect(stepNames(shape).get("refund")).toBe("Approve the refund");
    });
    it("says how long a card has sat at its step, and its start when that is all it knows", () => {
        expect(sayAge({ since: new Date(NOW - 40 * MIN).toISOString(), here: true }, NOW)).toBe("40m here");
        expect(sayAge({ since: new Date(NOW - 40 * MIN).toISOString(), here: false }, NOW)).toBe("started 40m ago");
    });
});

describe("the web's words, the phone's way", () => {
    it("never says pod or space", () => {
        const said = [
            ...Object.values(SCOPE_NOTE),
            runsFor({ kind: "time" }, false), runsFor({ kind: "time" }, true), runsFor({ kind: "manual" }, false), runsFor({ kind: "rows", table: "t", operations: [] }, false),
            phoneWords("The timetable lives on a pod schedule, not on the workflow."),
        ];
        for (const line of said) expect(line).not.toMatch(/\b(pod|space|teammate)\b/i);
    });
    it("keeps amber for a schedule that failed", () => {
        expect(healthMark("bad")).toBe("bad");
        expect(healthMark("warn")).toBe("quiet");
        expect(healthMark("off")).toBe("quiet");
    });
});

describe("a step's output", () => {
    it("reads keys as words", () => {
        expect(wordsOf("ticket_id")).toBe("Ticket id");
        expect(wordsOf("replyText")).toBe("Reply Text");
    });
    it("draws values as a person reads them", () => {
        expect(shownOf(true)).toEqual({ kind: "text", text: "Yes" });
        expect(shownOf(["a", 2])).toEqual({ kind: "text", text: "a, 2" });
        expect(shownOf("x\ny").kind).toBe("prose");
        expect(shownOf({ a: 1 }).kind).toBe("data");
    });
    it("leaves out what is said above it and what is empty", () => {
        const read = fieldsOf({ summary: "Done.", ticket: "T-1", note: null }, "Done.");
        expect(read?.fields.map((field) => field.label)).toEqual(["Ticket"]);
        expect(fieldsOf({}, null)).toBeNull();
        expect(fieldsOf(undefined, null)).toBeNull();
        expect(fieldsOf("plain", null)?.whole).toEqual({ kind: "prose", text: "plain" });
    });
});

describe("a run's tree, from the sample", () => {
    it("hangs a decision's arms under it and marks the one taken", async () => {
        const source = workSample();
        const detail = await source.run(SUPPORT.id, "run-triage-0");
        const graph = readGraph(await source.workflow(SUPPORT.id, "triage_new_tickets"))!;
        const tree = buildTree(graph);
        expect(tree.map((item) => item.type)).toEqual(["step", "decision", "step", "step"]);
        expect(detail?.wait?.type).toBe("HUMAN");
        expect(detail?.wait?.nodeId).toBe("refund");
    });
    it("lays the runs still going along the steps, yours first", async () => {
        const source = workSample();
        const shape = readShape(await source.workflow(SUPPORT.id, "triage_new_tickets"));
        const going = await source.runsInFlight(SUPPORT.id, "wf-triage");
        const waits = new Map((await source.waitingOnMe(SUPPORT.id)).map((one) => [one.run.id, one.wait]));
        const board = boardOf(shape, going, "wf-triage", waits, "member-me");
        expect(board.count).toBe(2);
        expect(board.mine).toBe(1);
        expect(board.columns.find((column) => column.id === "refund")?.cards[0].yours).toBe(true);
    });
});

/* ── the sample source writes ────────────────────────────────────────── */

describe("the sample source", () => {
    it("answers a form, and the run goes on", async () => {
        const source = workSample();
        expect((await source.waitingOnMe(SUPPORT.id)).map((one) => one.run.id)).toEqual(["run-triage-0"]);
        await expect(source.submitForm(SUPPORT.id, "run-triage-0", "wrong", {})).rejects.toThrow(/different step/);
        await source.submitForm(SUPPORT.id, "run-triage-0", "refund", { approve: true, amount: 240 });
        expect(await source.waitingOnMe(SUPPORT.id)).toEqual([]);
        expect((await source.run(SUPPORT.id, "run-triage-0"))?.status).toBe("COMPLETED");
    });
    it("holds the forms Home's sample asks for in Launch and Books", async () => {
        const source = workSample();
        expect((await source.run(LAUNCH.id, "run-quotes"))?.wait?.schema).toBeTruthy();
        expect((await source.run(BOOKS.id, "run-supplier"))?.wait?.type).toBe("HUMAN");
    });
    it("starts a run that waits on its first form", async () => {
        const source = workSample();
        const made = await source.startRun(LAUNCH.id, "partner_quotes");
        expect(made?.status).toBe("WAITING");
        expect((await source.waitingOnMe(LAUNCH.id)).length).toBe(2);
    });
    it("cancels, and retries a failed run from where it failed", async () => {
        const source = workSample();
        await source.cancelRun(SUPPORT.id, "run-chase-0");
        expect((await source.run(SUPPORT.id, "run-chase-0"))?.status).toBe("CANCELLED");
        expect((await source.run(SUPPORT.id, "run-chase-3"))?.failedNodeId).toBe("find");
        await source.retryRun(SUPPORT.id, "run-chase-3");
        expect((await source.run(SUPPORT.id, "run-chase-3"))?.status).toBe("RUNNING");
    });
    it("has a schedule the breaker stopped, and resuming clears its streak", async () => {
        const source = workSample();
        const stopped = (await source.schedules(SUPPORT.id)).find((one) => one.id === "sched-sweep")!;
        expect(stopped.pausedByFailures).toBe(true);
        expect(stopped.failures).toBe(5);
        const resumed = await source.setScheduleActive(SUPPORT.id, "sched-sweep", true);
        expect(resumed.failures).toBe(0);
        expect(resumed.active).toBe(true);
    });
    it("edits an agent, sending only what changed", async () => {
        const source = workSample();
        const before = await source.agent(SUPPORT.id, "reply_writer");
        const { draftOfAgent } = await import("@web/data/agents");
        const was = draftOfAgent(before);
        const saved = await source.updateAgent(SUPPORT.id, "reply_writer", was, { ...was, maxOutputTokens: "2000" });
        expect(saved.runtime?.settings.max_output_tokens).toBe(2000);
        expect(saved.instruction).toBe(before.instruction);
    });
    it("keeps a plan, and its sample has one for Support", async () => {
        const source = workSample();
        const text = await source.planText(SUPPORT.id);
        expect(parsePlan(text!).outcome).toBe("Every customer hears back within a day");
        expect(await source.planText(BOOKS.id)).toBeNull();
    });
});

/* ── agents ──────────────────────────────────────────────────────────── */

describe("an agent", () => {
    const writer = readAgentDetail({ name: "reply_writer", visibility: "POD", allowed_actions: ["agent.read", "agent.execute", "agent.update", "agent.delete"] });
    const front = readAgentDetail({ name: "pod_default", kind: "POD_DEFAULT", allowed_actions: ["agent.read", "agent.execute", "agent.update", "agent.delete"] });
    const reader = readAgentDetail({ name: "auditor", visibility: "RESTRICTED", allowed_actions: ["agent.read"] });

    it("says what the reader may do, never deleting the Lemma's own", () => {
        expect(accessOf(writer, "Support")).toEqual({ who: "Everyone in Support can use it.", you: "You can use it, change it and delete it." });
        expect(accessOf(front, "Support").you).toBe("You can use it. Support’s own agent is changed by talking to it, not here.");
        expect(accessOf(reader, "Support")).toEqual({ who: "Only the people it is shared with can use it.", you: "You can see it, but not use or change it. You may read Auditor, not change it." });
    });
    it("says permissions as verbs", () => {
        expect(sayPermissions(["table.read", "table.update"])).toBe("can read and update");
        expect(sayPermissions([])).toBe("no access");
    });
    it("finds what hands it work, under its wire name or the Lemma's", () => {
        const flows = [{ id: "w", name: "triage", description: null, steps: 1, kinds: [], active: true, updated: null, may: [], perPerson: false, targets: ["agent:reply_writer"] }];
        const jobs = [readSchedule({ id: "s", name: "digest", schedule_type: "TIME", config: { cron: "0 9 * * *" }, agent_name: "POD_DEFAULT" })];
        expect(usedBy(readAgentRow({ name: "reply_writer" }), jobs, flows).flows).toHaveLength(1);
        expect(usedBy(readAgentRow({ name: "pod_default", kind: "POD_DEFAULT" }), jobs, flows).jobs).toHaveLength(1);
    });
    it("finds its own surfaces, the Lemma's own for its own agent", () => {
        const surfaces: Surface[] = [
            { id: "1", platform: "WHATSAPP", handle: "+1", active: true, status: "ACTIVE", mine: true, agentKey: null },
            { id: "2", platform: "SLACK", handle: "#x", active: false, status: "NEEDS_SETUP", mine: false, agentKey: "reply_writer" },
        ];
        expect(surfacesFor(surfaces, front).map((one) => one.id)).toEqual(["1"]);
        expect(surfacesFor(surfaces, writer).map((one) => one.id)).toEqual(["2"]);
        expect(surfaceState("NEEDS_SETUP", false)).toBe("Finish setup");
        expect(surfaceState("ACTIVE", true)).toBe("Connected");
    });
});

/* ── the plan ────────────────────────────────────────────────────────── */

describe("the plan", () => {
    const text = "---\noutcome: Every customer hears back within a day\n---\n\n# Our plan\n\nSome prose the parser does not read.\n\n## What must be true\n- One — Ada, Oct 2\n\n## Never\n- Two — Sam, Oct 9\n";
    const plan = { ...parsePlan(text), file: text };

    it("adds a signed line, edits one, and drops one emptied", () => {
        const added = addLine(plan, "how", " Nudge hourly ", "Ada", new Date("2026-10-24T12:00:00"));
        expect(added.sections.how).toEqual([{ text: "Nudge hourly", by: "Ada", on: "Oct 24" }]);
        expect(addLine(plan, "how", "  ", "Ada", new Date())).toBe(plan);
        expect(editLine(plan, "must", 0, "One")).toBeNull();
        expect(editLine(plan, "must", 0, "Uno")?.sections.must[0]).toEqual({ text: "Uno", by: "Ada", on: "Oct 2" });
        expect(editLine(plan, "must", 0, " ")?.sections.must).toEqual([]);
        expect(removeLine(plan, "never", 0).sections.never).toEqual([]);
    });
    it("writes into the file as it was, keeping what it does not read", () => {
        const next = planToWrite(addLine(plan, "how", "Nudge hourly", "Ada", new Date("2026-10-24T12:00:00")), text, true);
        expect(next).toContain("Some prose the parser does not read.");
        expect(next).toContain("- Nudge hourly — Ada, Oct 24");
        expect(parsePlan(next).sections.never[0].text).toBe("Two");
    });
    it("refuses to write over a file someone changed since it was read", () => {
        expect(() => planToWrite(plan, text + "- More — Lemma, Oct 24\n", true)).toThrow(PlanMovedOn);
        expect(() => planToWrite(plan, null, true)).toThrow(PlanMovedOn);
    });
    it("writes a new file whole", () => {
        const next = planToWrite({ ...plan, file: undefined }, null, false);
        expect(next).toBe(serializePlan({ ...plan, file: undefined }));
    });
    it("drafts from what the Lemma runs, signed by whoever saves it", () => {
        const job = readSchedule({ id: "s", name: "morning-digest", schedule_type: "TIME", config: { cron: "0 9 * * 1-5" }, agent_name: "POD_DEFAULT", is_active: true });
        const { draft, stamped } = draftOf({ description: "Answers customers.", schedules: [job], agents: [readAgentRow({ name: "reply_writer", description: "Drafts replies." })], skills: ["tone"] }, "Ada", new Date("2026-10-24T12:00:00"));
        expect(draft.sections.must[0].text).toBe("Answers customers.");
        expect(draft.sections.how[0].text).toBe("Follows the tone skill");
        expect(draft.sections.who.map((line) => line.text)).toEqual(["Morning digest · every weekday at 09:00 UTC", "Reply writer: drafts replies."]);
        expect(stamped.sections.who[0]).toMatchObject({ by: "Ada", on: "Oct 24" });
    });
    it("edits the measure as text, and drops a due date off a weekly bar", () => {
        const meta = metaOf(plan);
        expect(meta).toEqual({ outcome: "Every customer hears back within a day", kind: "week", target: "", unit: "", due: "", measure: "" });
        const next = withMeta(plan, { ...meta, target: " 40 ", due: "2026-11-30", measure: "SELECT 1" });
        expect(next).toMatchObject({ target: "40", due: null, measure: "SELECT 1", kind: "week" });
        expect(withMeta(plan, { ...meta, kind: "date", due: "2026-11-30" }).due).toBe("2026-11-30");
    });
});

/* ── X-ray ───────────────────────────────────────────────────────────── */

describe("X-ray", () => {
    const href = { schedule: (id: string) => "s/" + id, workflow: (name: string) => "w/" + name, agent: (name: string) => "a/" + name, table: (name: string) => "t/" + name, app: (name: string) => "p/" + name, file: (path: string) => "f" + path, reach: "settings" };
    it("groups what a Lemma runs on, each piece with where it opens", () => {
        const job = readSchedule({ id: "s1", name: "digest", schedule_type: "TIME", config: { cron: "0 9 * * *" }, agent_name: "x", is_active: false, last_fired_at: new Date(NOW - 2 * 3_600_000).toISOString() });
        const groups = xrayGroups({
            lemmaName: "Support",
            schedules: { data: [job], failed: false },
            workflows: { data: undefined, failed: false },
            agents: { data: [readAgentRow({ name: "pod_default", kind: "POD_DEFAULT" })], failed: false },
            tables: { data: [], failed: true },
            apps: { data: [{ name: "queue", label: "Ticket queue", detail: "" }], failed: false },
            skills: { data: [{ name: "tone.md", path: "/skills/tone.md", description: "How we sound" }], failed: false },
            surfaces: { data: [{ id: "1", platform: "WHATSAPP", handle: "+1", active: true, status: "ACTIVE", mine: true, agentKey: null }], failed: false },
            href,
            now: NOW,
        });
        expect(groups.map((group) => group.title)).toEqual(["Runs on its own", "Workflows", "Agents", "Tables", "Apps", "Skills it follows", "Where people reach it"]);
        expect(groups[0].pieces?.[0]).toMatchObject({ name: "Digest", line: "Every day at 09:00 UTC · paused · last ran 2h ago", href: "s/s1", off: true });
        expect(groups[1].pieces).toBeNull();
        expect(groups[2].pieces?.[0].name).toBe("Lem (answers here)");
        expect(groups[3].failed).toBe(true);
        expect(groups[5].pieces?.[0]).toMatchObject({ name: "tone", href: "f/skills/tone.md" });
        expect(groups[6].pieces?.[0]).toMatchObject({ name: "WhatsApp", href: "settings" });
    });
    it("opens a skill folder at its SKILL.md", () => {
        expect(skillPath({ kind: "folder", path: "/skills/tone/" })).toBe("/skills/tone/SKILL.md");
        expect(skillPath({ kind: "file", path: "/skills/tone.md" })).toBe("/skills/tone.md");
    });
});

/* ── where things open ───────────────────────────────────────────────── */

describe("routes into area D", () => {
    it("opens a form in Needs you, and a run in a Lemma's Recently, on the run screen", () => {
        expect(hrefOfNeed({ podId: "p", conversationId: null, channelId: null, workflowRunId: "r 1", title: "Fill it in" })).toBe("/lemma/p/run/r%201");
        const rows = workRows({
            podId: "p",
            conversations: [{ id: "c", title: "Workflow run: triage", activityAt: new Date(NOW).toISOString(), kind: "TASK", boundTo: null, origin: { kind: "workflow", label: "Workflow run", runId: "r9" }, agentId: null, status: "IDLE", waitingOnYou: false }],
            tables: [],
            now: NOW,
        });
        expect(rows[0].href).toBe("/lemma/p/run/r9");
    });
    it("names each place once", () => {
        expect(workRoutes.runs("p", "schedules")).toBe("/lemma/p/section/runs?view=schedules");
        expect(workRoutes.schedule("p", NEW_SCHEDULE)).toBe("/lemma/p/schedule/new");
        expect(workRoutes.agent("p", "pod_default")).toBe("/lemma/p/agent/pod_default");
    });
});
