import { describe, expect, it } from "vitest";
import {
  buildDisplayMessageRows,
  collectCompletedRunTraceGroups,
  dedupToolInvocations,
  isAskUserToolName,
  isRenderableUserInteractionInvocation,
  isUserApprovalToolName,
  isUserInteractionToolName,
  latestPlanSummary,
  messageTextContent,
  normalizeAssistantMarkdown,
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
    expect(plan?.activeStep).toBe("B");
  });

  it("projects a write_todos invocation from markdown checklist lines", () => {
    const plan = latestPlanSummary([
      tool("a", {
        toolCallId: "c",
        toolName: "write_todos",
        args: { todos: ["- [x] Fetch report", "- [~] Parse rows", "- [ ] Summarize"] },
        state: "result",
        result: {},
      }),
    ]);
    expect(plan?.steps).toHaveLength(3);
    expect(plan?.completedCount).toBe(1);
    expect(plan?.inProgressCount).toBe(1);
    expect(plan?.activeStep).toBe("Parse rows");
  });

  it("prefers the write_todos result's full list over partial call args", () => {
    const plan = latestPlanSummary([
      tool("a", {
        toolCallId: "c",
        toolName: "write_todos",
        args: { todos: ["- [x] Step two"] },
        state: "result",
        result: { todos: [
          { content: "Step one", status: "completed" },
          { content: "Step two", status: "completed" },
          { content: "Step three", status: "in_progress" },
        ] },
      }),
    ]);
    expect(plan?.steps).toHaveLength(3);
    expect(plan?.completedCount).toBe(2);
    expect(plan?.activeStep).toBe("Step three");
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
    ];
    const rows = buildDisplayMessageRows(messages);
    const { groupsByStartIndex } = collectCompletedRunTraceGroups(rows, messages, false);
    expect(groupsByStartIndex.size).toBe(1);
  });

  it("never folds the active/streaming run", () => {
    const streaming: AssistantRenderableMessage[] = [
      { id: "u", role: "user", content: "go" },
      tool("t1", { toolCallId: "c1", toolName: "search_cards", args: {}, state: "result", result: {} }),
      text("n1", "Working on it."),
      tool("t2", { toolCallId: "c2", toolName: "build_session", args: {}, state: "call" }),
    ];
    const streamingRows = buildDisplayMessageRows(streaming);
    expect(collectCompletedRunTraceGroups(streamingRows, streaming, true).groupsByStartIndex.size).toBe(0);

    // The same shape, now finished with an answer, folds into one group.
    const done: AssistantRenderableMessage[] = [
      ...streaming.slice(0, 3),
      tool("t2", { toolCallId: "c2", toolName: "build_session", args: {}, state: "result", result: {} }),
      text("final", "We're live."),
    ];
    const doneRows = buildDisplayMessageRows(done);
    expect(collectCompletedRunTraceGroups(doneRows, done, false).groupsByStartIndex.size).toBe(1);
  });
});

describe("normalizeAssistantMarkdown", () => {
  it("breaks a compact inline heading onto its own block", () => {
    expect(normalizeAssistantMarkdown("Done. ## Next steps")).toContain("\n\n");
  });
});
