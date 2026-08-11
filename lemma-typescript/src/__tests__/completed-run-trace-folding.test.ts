import { describe, expect, it } from "vitest";
import {
  buildDisplayMessageRows,
  collectCompletedRunTraceGroups,
} from "../core/agent/display.js";
import type { AssistantRenderableMessage } from "../core/agent/renderable.js";

// Folding is a property of the transcript, not of how the reader reached it.
// The most recent run stays open — folding it the instant it ended was the
// largest jerk in the transcript, and deciding by "did this session watch it"
// made a live conversation render differently from the same one reloaded.
//
// See docs/design/conversation-messages.md.

const RUN_ID = "run-1";

let clock = 0;
function at(): Date {
  clock += 1000;
  return new Date(Date.UTC(2026, 0, 1) + clock);
}

function user(id: string, content: string): AssistantRenderableMessage {
  return { id, role: "user", content, createdAt: at(), kind: "TEXT" };
}

function toolTurn(id: string, runId: string | null): AssistantRenderableMessage {
  return {
    id,
    role: "assistant",
    content: "",
    createdAt: at(),
    kind: "TOOL_CALL",
    agent_run_id: runId,
    toolInvocations: [{
      toolCallId: `${id}-call`,
      toolName: "pod_tables",
      args: {},
      state: "result",
      result: { ok: true },
    }],
    parts: [{
      id: `${id}-tool`,
      type: "tool",
      toolInvocation: {
        toolCallId: `${id}-call`,
        toolName: "pod_tables",
        args: {},
        state: "result",
        result: { ok: true },
      },
    }],
  };
}

function answer(id: string, content: string, runId: string | null): AssistantRenderableMessage {
  return { id, role: "assistant", content, createdAt: at(), kind: "TEXT", agent_run_id: runId };
}

/** A finished run that did tool work and then answered. */
function finishedRun(runId: string | null) {
  return [
    user("u1", "list the tables"),
    toolTurn("a1", runId),
    answer("a2", "You have two tables.", runId),
  ];
}

function foldingFor(messages: AssistantRenderableMessage[], isRunActive = false) {
  const rows = buildDisplayMessageRows(messages);
  return collectCompletedRunTraceGroups(rows, messages, isRunActive);
}

describe("collectCompletedRunTraceGroups", () => {
  it("folds a finished run under its duration", () => {
    const groups = foldingFor(finishedRun(RUN_ID));

    expect(groups.groupsByStartIndex.size).toBe(1);
    for (const group of groups.groupsByStartIndex.values()) {
      expect(group.label).toMatch(/^Worked/);
    }
    expect(groups.groupedIndexes.size).toBeGreaterThan(0);
  });

  it("leaves a run that is still going open", () => {
    // While it works, its steps are the thing being read — and the transcript's
    // own status line already says it is working.
    expect(foldingFor(finishedRun(RUN_ID), true).groupsByStartIndex.size).toBe(0);
    expect(foldingFor(finishedRun(RUN_ID), true).groupedIndexes.size).toBe(0);
  });

  it("folds every finished run in the transcript, not just the older ones", () => {
    const messages = [
      ...finishedRun("run-old"),
      user("u2", "and again"),
      toolTurn("b1", RUN_ID),
      answer("b2", "Same two.", RUN_ID),
    ];

    expect(foldingFor(messages).groupsByStartIndex.size).toBe(2);
  });

  it("renders a finished run the same however the reader got to it", () => {
    // The rule reads only the transcript — no session state, no "did this tab
    // watch it" — which is what makes a reload look like what was already there.
    const messages = finishedRun(RUN_ID);
    const first = foldingFor(messages);
    const second = foldingFor(messages);

    expect([...second.groupedIndexes]).toEqual([...first.groupedIndexes]);
    expect([...second.traceIndexes]).toEqual([...first.traceIndexes]);
    expect([...second.groupsByStartIndex.keys()]).toEqual([...first.groupsByStartIndex.keys()]);
  });

  it("keeps an earlier run folded while a later one is running", () => {
    const messages = [
      ...finishedRun("run-old"),
      user("u2", "and again"),
      toolTurn("b1", RUN_ID),
    ];

    // The older run folds; the live one does not.
    expect(foldingFor(messages, true).groupsByStartIndex.size).toBe(1);
    expect(foldingFor(messages, true).groupsByStartIndex.has(1)).toBe(true);
  });
});

describe("buildDisplayMessageRows keys", () => {
  it("keeps a collapsible row's id stable as the cluster grows", () => {
    const one = buildDisplayMessageRows([user("u1", "go"), toolTurn("a1", RUN_ID)]);
    const two = buildDisplayMessageRows([user("u1", "go"), toolTurn("a1", RUN_ID), toolTurn("a2", RUN_ID)]);

    const idOf = (rows: ReturnType<typeof buildDisplayMessageRows>) =>
      rows.find((row) => row.message.role === "assistant")?.id;

    // A second tool arriving must not re-key the row and remount it mid-run.
    expect(idOf(one)).toBeDefined();
    expect(idOf(one)).toBe(idOf(two));
  });
});

describe("a run that is still going", () => {
  // Every row of a live run is working-out. Designating one of them the answer
  // just because it is currently last gives it the answer's weight and its
  // copy/timestamp footer, mid-run, between two tool groups.
  const live = [
    user("u1", "check the pod"),
    toolTurn("a1", RUN_ID),
    answer("a2", "Let me pull the pod inventory in parallel.", RUN_ID),
    toolTurn("a3", RUN_ID),
  ];

  it("treats every row as trace while it runs", () => {
    const rows = buildDisplayMessageRows(live);
    const { traceIndexes } = collectCompletedRunTraceGroups(rows, live, true);

    const assistantRows = rows
      .map((row, index) => ({ row, index }))
      .filter(({ row }) => row.message.role === "assistant");

    expect(assistantRows.length).toBeGreaterThan(0);
    for (const { index } of assistantRows) {
      expect(traceIndexes.has(index)).toBe(true);
    }
  });

  it("names an answer once the run settles", () => {
    const rows = buildDisplayMessageRows(live);
    const { traceIndexes } = collectCompletedRunTraceGroups(rows, live, false);

    const lastAssistantIndex = rows.reduce(
      (last, row, index) => (row.message.role === "assistant" ? index : last),
      -1,
    );
    // The closing row is the answer, so it is the one row that is not trace.
    const closingIndex = rows.reduce(
      (last, row, index) => (row.message.role === "assistant" && row.message.content ? index : last),
      -1,
    );
    expect(lastAssistantIndex).toBeGreaterThan(-1);
    expect(traceIndexes.has(closingIndex)).toBe(false);
  });
});
