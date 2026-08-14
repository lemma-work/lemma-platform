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

describe("an answer told over more than one message", () => {
  // The agent often lands its answer as several messages — a paragraph, then
  // the caveat, then the question. Only the last one was being shown: the rule
  // took the *last* run-closing row as the answer and folded everything before
  // it, so the transcript opened mid-thought at "One honest flag: …" and the
  // paragraph that set it up was hidden inside "Worked for 1m 30s".
  const messages = [
    user("u1", "hey"),
    toolTurn("a1", RUN_ID),
    answer("a2", "You have 5 drafts waiting.", RUN_ID),
    answer("a3", "One honest flag: these came in July 2.", RUN_ID),
    answer("a4", "What do you want to do?", RUN_ID),
  ];

  it("shows every trailing message, not just the last", () => {
    const { groupedIndexes } = foldingFor(messages);
    // Row 0 is the user turn; row 1 is the tool work and folds.
    expect(groupedIndexes.has(1)).toBe(true);
    expect(groupedIndexes.has(2)).toBe(false);
    expect(groupedIndexes.has(3)).toBe(false);
    expect(groupedIndexes.has(4)).toBe(false);
  });

  it("counts none of the answer as working-out", () => {
    const { traceIndexes } = foldingFor(messages);
    expect(traceIndexes.has(1)).toBe(true);
    expect(traceIndexes.has(2)).toBe(false);
    expect(traceIndexes.has(3)).toBe(false);
  });

  it("still folds narration that came before more work", () => {
    // Text with another tool call after it was working-out, not the answer.
    const withLaterWork = [
      user("u1", "hey"),
      toolTurn("a1", RUN_ID),
      answer("a2", "Checking the drafts table.", RUN_ID),
      toolTurn("a3", RUN_ID),
      answer("a4", "You have 5 drafts waiting.", RUN_ID),
    ];
    const { groupedIndexes } = foldingFor(withLaterWork);
    expect(groupedIndexes.has(2)).toBe(true);
    expect(groupedIndexes.has(3)).toBe(true);
    expect(groupedIndexes.has(4)).toBe(false);
  });
});

describe("an answer that also shows a resource", () => {
  // The shape that still hid text after the first fix: the agent says what it
  // found *and* calls display_resource in the same message. The card is hoisted
  // out of the rollup and drawn under the answer, so the reader sees the Drafts
  // table — but the sentence introducing it stays folded, and the answer opens
  // at the caveat that follows.
  function answerWithCard(id: string, content: string, runId: string | null): AssistantRenderableMessage {
    return {
      id,
      role: "assistant",
      content,
      createdAt: at(),
      kind: "TEXT",
      agent_run_id: runId,
      toolInvocations: [{
        toolCallId: `${id}-call`,
        toolName: "display_resource",
        args: {},
        state: "result",
        result: { ok: true },
      }],
      parts: [{
        id: `${id}-tool`,
        type: "tool",
        toolInvocation: {
          toolCallId: `${id}-call`,
          toolName: "display_resource",
          args: {},
          state: "result",
          result: { ok: true },
        },
      }],
    };
  }

  const messages = [
    user("u1", "hey"),
    toolTurn("a1", RUN_ID),
    answerWithCard("a2", "You have 5 drafts waiting.", RUN_ID),
    answer("a3", "One honest flag: these came in July 2.", RUN_ID),
  ];

  it("keeps the sentence that introduces the card", () => {
    const { groupedIndexes } = foldingFor(messages);
    expect(groupedIndexes.has(1)).toBe(true);
    expect(groupedIndexes.has(2)).toBe(false);
    expect(groupedIndexes.has(3)).toBe(false);
  });
});

describe("the real smart-inbox transcript", () => {
  // Taken from an actual conversation. Every assistant TEXT message carries
  // `is_final_answer: true` — the backend sets it on all of them — so the flag
  // cannot pick out "the answer" on its own. What matters is that seq 23, the
  // findings table, is the substance of the reply and was being folded away,
  // leaving the transcript to open at seq 27's "One honest flag: …".
  function thinking(id: string): AssistantRenderableMessage {
    return { id, role: "assistant", content: "considering", createdAt: at(), kind: "THINKING", agent_run_id: RUN_ID };
  }
  function displayResource(id: string): AssistantRenderableMessage {
    return {
      id,
      role: "assistant",
      content: "",
      createdAt: at(),
      kind: "TOOL_CALL",
      agent_run_id: RUN_ID,
      toolInvocations: [{
        toolCallId: `${id}-call`,
        toolName: "display_resource",
        args: {},
        state: "result",
        result: { success: true },
      }],
      parts: [{
        id: `${id}-tool`,
        type: "tool",
        toolInvocation: {
          toolCallId: `${id}-call`,
          toolName: "display_resource",
          args: {},
          state: "result",
          result: { success: true },
        },
      }],
    };
  }

  const messages: AssistantRenderableMessage[] = [
    user("s0", "hey"),
    thinking("s1"),
    answer("s2", "Hey! Let me pull up where your inbox stands.", RUN_ID),
    toolTurn("s3", RUN_ID),
    thinking("s7"),
    answer("s8", "Query is locked down on this pod — using records list instead.", RUN_ID),
    toolTurn("s9", RUN_ID),
    thinking("s22"),
    answer("s23", "Your inbox is fully triaged. 5 drafts waiting on your approval.", RUN_ID),
    displayResource("s24"),
    thinking("s26"),
    answer("s27", "One honest flag: these came in July 2.", RUN_ID),
  ];

  it("still folds the narration that came before the work", () => {
    // "Let me pull up where your inbox stands" and "Query is locked down" are
    // narration with tool work after them. They carry `is_final_answer` too,
    // which is exactly why that flag cannot be the rule — showing all four
    // would make one turn read as four answers.
    const rows = buildDisplayMessageRows(messages);
    const { groupedIndexes } = collectCompletedRunTraceGroups(rows, messages, false);
    const textOf = (i: number) => String(rows[i]?.message?.content ?? "");
    const preamble = rows.findIndex((_, i) => textOf(i).startsWith("Hey! Let me pull up"));
    const fallbackNote = rows.findIndex((_, i) => textOf(i).startsWith("Query is locked down"));

    expect(groupedIndexes.has(preamble)).toBe(true);
    expect(groupedIndexes.has(fallbackNote)).toBe(true);
  });

  it("shows the findings, not just the closing caveat", () => {
    const rows = buildDisplayMessageRows(messages);
    const { groupedIndexes } = collectCompletedRunTraceGroups(rows, messages, false);
    const textOf = (i: number) => String(rows[i]?.message?.content ?? "");
    const findings = rows.findIndex((_, i) => textOf(i).startsWith("Your inbox is fully triaged"));
    const caveat = rows.findIndex((_, i) => textOf(i).startsWith("One honest flag"));

    expect(findings).toBeGreaterThan(-1);
    expect(caveat).toBeGreaterThan(-1);
    expect(groupedIndexes.has(caveat)).toBe(false);
    expect(groupedIndexes.has(findings)).toBe(false);
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
