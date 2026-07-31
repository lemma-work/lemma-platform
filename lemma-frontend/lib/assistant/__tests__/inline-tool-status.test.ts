import { describe, expect, it } from "vitest";
import type { AssistantRenderableMessage, AssistantToolInvocation } from "lemma-sdk/react";
import {
  currentToolStatusLabel,
  isInlineToolStatusAlreadyVisible,
  resolveThinkingOwner,
  type InlineToolStatus,
} from "@/components/lemma/assistant/assistant-format";
import type { DisplayMessageRow } from "@/components/lemma/assistant/assistant-experience";

function toolRow(
  invocation: AssistantToolInvocation,
  sourceIndex = 1,
): DisplayMessageRow {
  const message: AssistantRenderableMessage = {
    id: `message-${invocation.toolCallId}`,
    role: "assistant",
    content: "",
    createdAt: new Date("2026-07-11T00:00:01.000Z"),
    parts: [{
      id: `part-${invocation.toolCallId}`,
      type: "tool",
      toolInvocation: invocation,
    }],
    toolInvocations: [invocation],
  };

  return {
    id: `row-${invocation.toolCallId}`,
    message,
    sourceIndexes: [sourceIndex],
  };
}

describe("inline tool-status handoff", () => {
  it("carries the tool identity through both streaming and durable status sources", () => {
    const row = toolRow({
      toolCallId: "call-123",
      toolName: "list_tables",
      args: { comment: "List all tables in the pod" },
      state: "call",
    });

    const streamingStatus = currentToolStatusLabel({
      messages: [],
      isConversationBusy: true,
      streamingTool: {
        toolCallId: "call-123",
        toolName: "list_tables",
        args: { comment: "List all tables in the pod" },
      },
    });
    const durableStatus = currentToolStatusLabel({
      messages: [row.message],
      isConversationBusy: true,
      streamingTool: null,
    });

    expect(streamingStatus).toMatchObject({
      label: "List all tables in the pod",
      toolCallId: "call-123",
      toolName: "list_tables",
    });
    expect(durableStatus).toMatchObject({
      label: "List all tables in the pod",
      toolCallId: "call-123",
      toolName: "list_tables",
    });
  });

  it("suppresses the transient status once its matching call row is visible", () => {
    const row = toolRow({
      toolCallId: "call-123",
      toolName: "list_tables",
      args: {},
      state: "call",
    });
    const status: InlineToolStatus = {
      label: "List all tables in the pod",
      shimmer: true,
      toolCallId: "call-123",
      toolName: "list_tables",
    };

    expect(isInlineToolStatusAlreadyVisible({ rows: [row], latestUser: 0, status })).toBe(true);
  });

  it("keeps a genuinely newer call status visible", () => {
    const row = toolRow({
      toolCallId: "call-123",
      toolName: "list_tables",
      args: {},
      state: "result",
      result: { success: true },
    });
    const status: InlineToolStatus = {
      label: "List all tables in the pod",
      shimmer: true,
      toolCallId: "call-456",
      toolName: "list_tables",
    };

    expect(isInlineToolStatusAlreadyVisible({ rows: [row], latestUser: 0, status })).toBe(false);
  });

  it("uses an active same-name row during the partial-token window before an id arrives", () => {
    const activeRow = toolRow({
      toolCallId: "call-123",
      toolName: "list_tables",
      args: {},
      state: "call",
    });
    const completedRow = toolRow({
      toolCallId: "call-122",
      toolName: "list_tables",
      args: {},
      state: "result",
      result: { success: true },
    });
    const partialStatus: InlineToolStatus = {
      label: "Running list tables",
      shimmer: true,
      toolName: "list_tables",
    };

    expect(isInlineToolStatusAlreadyVisible({ rows: [activeRow], latestUser: 0, status: partialStatus })).toBe(true);
    expect(isInlineToolStatusAlreadyVisible({ rows: [completedRow], latestUser: 0, status: partialStatus })).toBe(false);
  });
});

describe("who owns the Thinking label", () => {
  function reasoningRow(state: "streaming" | "done", sourceIndex = 1): DisplayMessageRow {
    const message: AssistantRenderableMessage = {
      id: `reasoning-message-${state}`,
      role: "assistant",
      content: "",
      createdAt: new Date("2026-07-11T00:00:01.000Z"),
      parts: [{
        id: `reasoning-part-${state}`,
        type: "reasoning",
        text: state === "streaming" ? "Inspecting the workspace" : "Inspected the workspace",
        state,
      }],
    };

    return { id: `reasoning-row-${state}`, message, sourceIndexes: [sourceIndex] };
  }

  it("gives a streaming thought the label, so the placeholder yields", () => {
    expect(resolveThinkingOwner({
      rows: [reasoningRow("streaming")],
      latestUser: 0,
      hasRunStatus: true,
    })).toBe("reasoning");
  });

  it("gives the tool rollup the label once tools are running", () => {
    const row = toolRow({
      toolCallId: "call-1",
      toolName: "list_tables",
      args: {},
      state: "call",
    });

    expect(resolveThinkingOwner({
      rows: [row],
      latestUser: 0,
      hasRunStatus: true,
    })).toBe("tool-rollup");
  });

  it("still prefers the thought when a run has both a thought and tools", () => {
    const tool = toolRow({
      toolCallId: "call-2",
      toolName: "list_tables",
      args: {},
      state: "call",
    }, 2);

    expect(resolveThinkingOwner({
      rows: [reasoningRow("streaming"), tool],
      latestUser: 0,
      hasRunStatus: true,
    })).toBe("reasoning");
  });

  it("falls back to the run-status placeholder while the run is still empty", () => {
    expect(resolveThinkingOwner({
      rows: [],
      latestUser: 0,
      hasRunStatus: true,
    })).toBe("run-status");
  });

  it("hands the label back to the placeholder once the thought settles", () => {
    // The durable THINKING message renders "Thought for Ns", not "Thinking",
    // so it no longer competes for the streaming label.
    expect(resolveThinkingOwner({
      rows: [reasoningRow("done")],
      latestUser: 0,
      hasRunStatus: true,
    })).toBe("run-status");
  });

  it("ignores reasoning that belongs to an earlier run", () => {
    expect(resolveThinkingOwner({
      rows: [reasoningRow("streaming")],
      latestUser: 1,
      hasRunStatus: true,
    })).toBe("run-status");
  });

  it("names nobody when there is no run status to place", () => {
    expect(resolveThinkingOwner({
      rows: [],
      latestUser: 0,
      hasRunStatus: false,
    })).toBeNull();
  });
});
