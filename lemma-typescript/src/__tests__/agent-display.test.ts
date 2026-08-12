import { describe, expect, it } from "vitest";
import {
  buildDisplayMessageRows,
  collectCompletedRunTraceGroups,
  completedTurnTraceDurations,
  dedupToolInvocations,
  isAskUserToolName,
  isRenderableUserInteractionInvocation,
  isUserApprovalToolName,
  isUserInteractionToolName,
  latestPlanSummary,
  messageTextContent,
  normalizeAssistantMarkdown,
  prepareMessagesForDisplay,
} from "../core/agent/display.js";
import { normalizeAgentToolName } from "../core/agent/tool-names.js";
import { parseAssistantStreamEvent } from "../assistant-events.js";
import type { AssistantRenderableMessage } from "../core/agent/renderable.js";

describe("user-interaction tool predicates", () => {
  it("classifies ask_user and request_approval", () => {
    expect(isAskUserToolName("ask_user")).toBe(true);
    expect(isAskUserToolName("request_approval")).toBe(false);
    expect(isUserApprovalToolName("request_approval")).toBe(true);
    expect(isUserApprovalToolName("ask_user")).toBe(false);
    // The combined predicate matches either pausing tool.
    expect(isUserInteractionToolName("ask_user")).toBe(true);
    expect(isUserInteractionToolName("request_approval")).toBe(true);
    expect(isAskUserToolName("mcp__lemma_tools__lemma_ask_user")).toBe(true);
    expect(isUserApprovalToolName("lemma_tools_lemma_request_approval")).toBe(true);
    expect(isUserInteractionToolName("exec_command")).toBe(false);
  });

  it("does not render a completed daemon prose fallback as an interaction card", () => {
    expect(isRenderableUserInteractionInvocation({
      toolCallId: "ask-1",
      toolName: "mcp__lemma_tools__lemma_ask_user",
      args: {},
      state: "result",
      result: {
        success: false,
        interaction_fallback: true,
        message: "Ask the user directly in your reply.",
      },
    })).toBe(false);
    expect(isRenderableUserInteractionInvocation({
      toolCallId: "ask-2",
      toolName: "ask_user",
      args: {},
      state: "call",
    })).toBe(true);
    expect(isRenderableUserInteractionInvocation({
      toolCallId: "ask-3",
      toolName: "ask_user",
      args: {},
      state: "result",
      result: { success: true, answers: { Runtime: "Claude" } },
    })).toBe(true);
    expect(isRenderableUserInteractionInvocation({
      toolCallId: "ask-4",
      toolName: "ask_user",
      args: {},
      state: "result",
      result: { success: false, answers: {}, message: "User dismissed the questions." },
    })).toBe(true);
  });
});

describe("normalizeAgentToolName", () => {
  it("strips only Lemma MCP wrappers", () => {
    expect(normalizeAgentToolName("mcp__lemma_tools__lemma_display_resource")).toBe("display_resource");
    expect(normalizeAgentToolName("mcp.lemma_tools.lemma_exec_command")).toBe("exec_command");
    expect(normalizeAgentToolName("lemma_tools_lemma_ask_user")).toBe("ask_user");
    expect(normalizeAgentToolName("mcp__github__create_issue")).toBe("mcp__github__create_issue");
    expect(normalizeAgentToolName("commandExecution")).toBe("commandExecution");
  });

  it("reads the Agent Host's shorter server name as the same server", () => {
    // A local agent is handed the run-scoped server as `lemma`, not
    // `lemma_tools`, so its calls arrive namespaced that way and have to land
    // on the same tool as the pod agent's — same card, same icon, same name.
    expect(normalizeAgentToolName("mcp__lemma__lemma_pod_write_file")).toBe("pod_write_file");
    expect(normalizeAgentToolName("mcp.lemma.lemma_display_resource")).toBe("display_resource");
    expect(normalizeAgentToolName("lemma__lemma_ask_user")).toBe("ask_user");
    expect(normalizeAgentToolName("lemma/lemma_exec_command")).toBe("exec_command");
  });
});

describe("parseAssistantStreamEvent completed", () => {
  it("prefers conversation_status so a paused run surfaces WAITING", () => {
    const parsed = parseAssistantStreamEvent({
      type: "completed",
      data: { status: "COMPLETED", conversation_status: "WAITING" },
    });
    expect(parsed.status).toBe("WAITING");
  });

  it("falls back to the run status for an ordinary completion", () => {
    const parsed = parseAssistantStreamEvent({
      type: "completed",
      data: { status: "COMPLETED" },
    });
    expect(parsed.status).toBe("COMPLETED");
  });

  it("treats a stopped replay as terminal", () => {
    const parsed = parseAssistantStreamEvent({
      type: "stopped",
      data: { status: "STOPPED" },
    });
    expect(parsed.status).toBe("STOPPED");
  });
});

function tool(id: string, inv: Record<string, unknown>): AssistantRenderableMessage {
  return {
    id,
    role: "assistant",
    content: "",
    parts: [{ id: `${id}-p`, type: "tool", toolInvocation: inv as never }],
  };
}

describe("dedupToolInvocations", () => {
  it("merges a call and its result into one resolved invocation", () => {
    const message: AssistantRenderableMessage = {
      id: "m",
      role: "assistant",
      content: "",
      parts: [
        { id: "p1", type: "tool", toolInvocation: { toolCallId: "c1", toolName: "search", args: { q: "x" }, state: "call" } },
        { id: "p2", type: "tool", toolInvocation: { toolCallId: "c1", toolName: "search", args: { q: "x" }, state: "result", result: { ok: true } } },
      ],
    };
    const invocations = dedupToolInvocations(message);
    expect(invocations).toHaveLength(1);
    expect(invocations[0].state).toBe("result");
    expect(invocations[0].result).toEqual({ ok: true });
  });
});

describe("buildDisplayMessageRows", () => {
  it("clusters tool-only messages and keeps the answer row", () => {
    const messages: AssistantRenderableMessage[] = [
      { id: "u", role: "user", content: "hi" },
      tool("a1", { toolCallId: "c1", toolName: "search", args: {}, state: "result", result: {} }),
      tool("a2", { toolCallId: "c2", toolName: "read", args: {}, state: "result", result: {} }),
      { id: "a3", role: "assistant", content: "Here's the answer", metadata: { is_final_answer: true } },
    ];
    const rows = buildDisplayMessageRows(messages);
    expect(rows.some((row) => row.id.startsWith("tool-cluster-"))).toBe(true);
    expect(rows.some((row) => messageTextContent(row.message) === "Here's the answer")).toBe(true);
  });

  it("renders a plain assistant text message as its own row", () => {
    const rows = buildDisplayMessageRows([
      { id: "u", role: "user", content: "hi" },
      { id: "a", role: "assistant", content: "hello there" },
    ]);
    expect(rows).toHaveLength(2);
    expect(messageTextContent(rows[1].message)).toBe("hello there");
  });
});

describe("latestPlanSummary", () => {
  it("projects an update_plan invocation into a summary", () => {
    const plan = latestPlanSummary([
      tool("a", {
        toolCallId: "c",
        toolName: "update_plan",
        args: { plan: [{ step: "A", status: "completed" }, { step: "B", status: "in_progress" }] },
        state: "result",
        result: {},
      }),
    ]);
    expect(plan?.steps).toHaveLength(2);
    expect(plan?.completedCount).toBe(1);
    expect(plan?.inProgressCount).toBe(1);
    expect(plan?.pendingCount).toBe(0);
    expect(plan?.isComplete).toBe(false);
    expect(plan?.activeStep).toBe("B");
  });

  it("projects the two-state write_todos contract and identifies the next step", () => {
    const plan = latestPlanSummary([
      tool("a", {
        toolCallId: "c",
        toolName: "write_todos",
        args: { todos: ["- [x] Fetch report", "- [ ] Parse rows", "- [ ] Summarize"] },
        state: "result",
        result: {},
      }),
    ]);
    expect(plan?.steps).toHaveLength(3);
    expect(plan?.completedCount).toBe(1);
    expect(plan?.inProgressCount).toBe(0);
    expect(plan?.pendingCount).toBe(2);
    expect(plan?.running).toBe(true);
    expect(plan?.nextStep).toBe("Parse rows");
  });

  it("prefers the backend's full markdown result over partial call args", () => {
    const plan = latestPlanSummary([
      tool("a", {
        toolCallId: "c",
        toolName: "mcp__lemma_tools__lemma_write_todos",
        args: { todos: ["- [x] Step two"] },
        state: "result",
        result: { todos: ["- [x] Step one", "- [x] Step two", "- [ ] Step three"] },
      }),
    ]);
    expect(plan?.steps).toHaveLength(3);
    expect(plan?.completedCount).toBe(2);
    expect(plan?.nextStep).toBe("Step three");
  });

  it("marks a fully completed plan as complete and accepts the backend's star checkbox", () => {
    const plan = latestPlanSummary([
      tool("a", {
        toolCallId: "c",
        toolName: "write_todos",
        args: { todos: ["* [*] Ship it"] },
        state: "result",
        result: {},
      }),
    ]);
    expect(plan?.completedCount).toBe(1);
    expect(plan?.pendingCount).toBe(0);
    expect(plan?.running).toBe(false);
    expect(plan?.isComplete).toBe(true);
  });

  it("merges a streaming partial write_todos update into the last full plan", () => {
    const plan = latestPlanSummary([
      tool("initial", {
        toolCallId: "initial-plan",
        toolName: "write_todos",
        args: { todos: ["- [ ] Step one", "- [ ] Step two", "- [ ] Step three"] },
        state: "result",
        result: { todos: ["- [ ] Step one", "- [ ] Step two", "- [ ] Step three"] },
      }),
      tool("update", {
        toolCallId: "update-step",
        toolName: "write_todos",
        args: { todos: ["- [x] Step one"] },
        state: "call",
      }),
    ]);

    expect(plan?.steps).toEqual([
      { step: "Step one", status: "completed" },
      { step: "Step two", status: "pending" },
      { step: "Step three", status: "pending" },
    ]);
  });

  it("recovers the observed XML-flattened plan and ignores accumulated history", () => {
    const finalSnapshot =
      "RESEARCH DONE</item>\n"
      + "<item>DECK DONE</item>\n"
      + "<item>WRITE HTML DONE</item>\n"
      + "<item>RENDER PDF DONE</item>\n"
      + "<item>UPLOAD DONE";
    const plan = latestPlanSummary([
      tool("corrupt-plan", {
        toolCallId: "corrupt-plan",
        toolName: "write_todos",
        args: { todos: [finalSnapshot] },
        state: "result",
        result: {
          todos: [
            "- [ ] [ ] Research Hermes Agent</td>\n<item>- [ ] Build deck</td>",
            "- [ ] [x] Research Hermes Agent</td>\n<item>- [x] Build deck</td>",
            "- [ ] RESEARCH DONE",
            "- [ ] DECK DONE",
            "- [ ] WRITE HTML",
            "- [ ] RENDER PDF",
            "- [ ] UPLOAD",
            `- [ ] ${finalSnapshot}`,
          ],
        },
      }),
    ]);

    expect(plan?.steps).toEqual([
      { step: "RESEARCH", status: "completed" },
      { step: "DECK", status: "completed" },
      { step: "WRITE HTML", status: "completed" },
      { step: "RENDER PDF", status: "completed" },
      { step: "UPLOAD", status: "completed" },
    ]);
    expect(plan?.isComplete).toBe(true);
  });

  it("recovers prose statuses from an in-progress flattened snapshot", () => {
    const plan = latestPlanSummary([
      tool("progress-plan", {
        toolCallId: "progress-plan",
        toolName: "write_todos",
        args: {
          todos: [
            "Research Hermes Agent — done</item>\n"
            + "<item>Outline & deck .pptx — done</item>\n"
            + "<item>Write HTML report — in progress</item>\n"
            + "<item>Convert HTML → PDF (weasyprint)</item>\n"
            + "<item>Upload both to /me/ and share paths</item>\n</todos>",
          ],
        },
        state: "call",
      }),
    ]);

    expect(plan?.completedCount).toBe(2);
    expect(plan?.inProgressCount).toBe(1);
    expect(plan?.activeStep).toBe("Write HTML report");
    expect(plan?.pendingCount).toBe(2);
  });

  it("does not degrade on plan text padded with whitespace", () => {
    // Plan entries come from model output, so a step made of tens of thousands
    // of spaces is reachable. The status and checkbox patterns used to let two
    // parts of themselves claim the same spaces, which made parsing one of
    // these entries quadratic and stalled the render.
    const padded = "step" + " ".repeat(40_000) + "x";
    const started = Date.now();

    const plan = latestPlanSummary([
      tool("padded-plan", {
        toolCallId: "padded-plan",
        toolName: "write_todos",
        args: { todos: [padded, "[ ]" + " ".repeat(40_000), "done work \u2014 done"] },
        state: "call",
      }),
    ]);

    expect(plan).not.toBeNull();
    expect(Date.now() - started).toBeLessThan(1_000);
  });
});

describe("thought duration projection", () => {
  it("does not infer thought seconds from message-to-final-answer timestamps", () => {
    const messages: AssistantRenderableMessage[] = [
      { id: "u", role: "user", content: "go", createdAt: new Date("2026-07-30T10:00:00Z") },
      {
        id: "note",
        role: "assistant",
        content: "Checking the data.",
        kind: "THINKING",
        createdAt: new Date("2026-07-30T10:00:01Z"),
        metadata: { is_final_answer: false },
      },
      {
        id: "final",
        role: "assistant",
        content: "Done.",
        createdAt: new Date("2026-07-30T10:01:01Z"),
        metadata: { is_final_answer: true },
      },
    ];

    const durations = completedTurnTraceDurations(messages);
    expect(durations.has(1)).toBe(true);
    expect(durations.get(1)).toBeUndefined();

    const projected = prepareMessagesForDisplay(messages)[1].message;
    const reasoning = projected.parts?.find((part) => part.type === "reasoning");
    expect(reasoning?.durationMs).toBeUndefined();
  });

  it("preserves a runtime-supplied thought duration", () => {
    const messages: AssistantRenderableMessage[] = [
      { id: "u", role: "user", content: "go" },
      {
        id: "thinking",
        role: "assistant",
        content: "",
        kind: "THINKING",
        parts: [{
          id: "reasoning",
          type: "reasoning",
          text: "Checking the data.",
          state: "done",
          durationMs: 4200,
        }],
      },
      {
        id: "final",
        role: "assistant",
        content: "Done.",
        metadata: { is_final_answer: true },
      },
    ];

    const projected = prepareMessagesForDisplay(messages)[1].message;
    const reasoning = projected.parts?.find((part) => part.type === "reasoning");
    expect(reasoning?.durationMs).toBe(4200);
  });
});

describe("collectCompletedRunTraceGroups", () => {
  const text = (id: string, content: string): AssistantRenderableMessage => ({ id, role: "assistant", content });

  it("folds a whole run into one group, leaving the final answer outside", () => {
    const messages: AssistantRenderableMessage[] = [
      { id: "u", role: "user", content: "go" },
      tool("t1", { toolCallId: "c1", toolName: "search_cards", args: {}, state: "result", result: {} }),
      text("n1", "Let me grab the session items."),
      tool("t2", { toolCallId: "c2", toolName: "build_session", args: {}, state: "result", result: {} }),
      text("final", "We're live. Here's card 1 of 12."),
      // A later turn, so the run above is no longer the most recent one and folds.
      { id: "u2", role: "user", content: "thanks" },
      text("ack", "Any time."),
    ];
    const rows = buildDisplayMessageRows(messages);
    const { groupsByStartIndex, groupedIndexes } = collectCompletedRunTraceGroups(rows, messages, false);

    const finalIdx = rows.findIndex((row) => messageTextContent(row.message).startsWith("We're live"));
    const narrationIdx = rows.findIndex((row) => messageTextContent(row.message).startsWith("Let me grab"));

    expect(groupsByStartIndex.size).toBe(1); // ONE "Worked for", not one per text
    expect(groupedIndexes.has(narrationIdx)).toBe(true); // intermediate narration folds in
    expect(groupedIndexes.has(finalIdx)).toBe(false); // final answer stays outside
  });

  it("collapses multiple intermediate narrations into a single run group", () => {
    const messages: AssistantRenderableMessage[] = [
      { id: "u", role: "user", content: "go" },
      tool("t1", { toolCallId: "c1", toolName: "search_cards", args: {}, state: "result", result: {} }),
      text("n1", "Session built — grabbing items."),
      tool("t2", { toolCallId: "c2", toolName: "check_schema", args: {}, state: "result", result: {} }),
      text("n2", "Now I've got everything I need."),
      tool("t3", { toolCallId: "c3", toolName: "build_session", args: {}, state: "result", result: {} }),
      text("final", "We're live."),
      { id: "u2", role: "user", content: "thanks" },
      text("ack", "Any time."),
    ];
    const rows = buildDisplayMessageRows(messages);
    const { groupsByStartIndex } = collectCompletedRunTraceGroups(rows, messages, false);
    expect(groupsByStartIndex.size).toBe(1);
  });

  it("folds the most recent run once it finishes, but not while it runs", () => {
    const streaming: AssistantRenderableMessage[] = [
      { id: "u", role: "user", content: "go" },
      tool("t1", { toolCallId: "c1", toolName: "search_cards", args: {}, state: "result", result: {} }),
      text("n1", "Working on it."),
      tool("t2", { toolCallId: "c2", toolName: "build_session", args: {}, state: "call" }),
    ];
    const streamingRows = buildDisplayMessageRows(streaming);
    expect(collectCompletedRunTraceGroups(streamingRows, streaming, true).groupsByStartIndex.size).toBe(0);

    // Finished: it folds under "Worked for …" like any other completed run.
    const done: AssistantRenderableMessage[] = [
      ...streaming.slice(0, 3),
      tool("t2", { toolCallId: "c2", toolName: "build_session", args: {}, state: "result", result: {} }),
      text("final", "We're live."),
    ];
    const doneRows = buildDisplayMessageRows(done);
    expect(collectCompletedRunTraceGroups(doneRows, done, false).groupsByStartIndex.size).toBe(1);

    // …and stays folded once a later turn exists.
    const superseded: AssistantRenderableMessage[] = [
      ...done,
      { id: "u2", role: "user", content: "thanks" },
      text("ack", "Any time."),
    ];
    const supersededRows = buildDisplayMessageRows(superseded);
    expect(collectCompletedRunTraceGroups(supersededRows, superseded, false).groupsByStartIndex.size).toBe(1);
  });
});

describe("normalizeAssistantMarkdown", () => {
  it("breaks a compact inline heading onto its own block", () => {
    expect(normalizeAssistantMarkdown("Done. ## Next steps")).toContain("\n\n");
  });

  it("still turns a compact --- separator into a paragraph break", () => {
    expect(normalizeAssistantMarkdown("Imported the pod. --- Then it ran.")).toBe("Imported the pod.\n\nThen it ran.");
  });

  it("leaves a spaced table delimiter row intact", () => {
    const table = "## Next steps\n\n| Field | Type |\n| --- | --- |\n| id | int |";
    expect(normalizeAssistantMarkdown(table)).toBe(table);
  });

  it("leaves an unspaced table delimiter row intact", () => {
    const table = "## Next steps\n\n| Field | Type |\n|---|---|\n| id | int |";
    expect(normalizeAssistantMarkdown(table)).toBe(table);
  });

  it("keeps aligned delimiter rows intact", () => {
    const table = "| Field | Type |\n| :--- | ---: |\n| id | int |";
    expect(normalizeAssistantMarkdown(table)).toBe(table);
  });
});
