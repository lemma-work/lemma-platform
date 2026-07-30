import { describe, expect, it } from "vitest";
import type { AssistantRenderableMessage, AssistantToolInvocation } from "lemma-sdk/react";
import {
  currentToolStatusLabel,
  isInlineToolStatusAlreadyVisible,
  isStreamingReasoningAlreadyVisible,
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

describe("streaming reasoning status handoff", () => {
  it("suppresses the fallback Thinking status once reasoning is visible", () => {
    const message: AssistantRenderableMessage = {
      id: "reasoning-message",
      role: "assistant",
      content: "",
      createdAt: new Date("2026-07-11T00:00:01.000Z"),
      parts: [{
        id: "reasoning-part",
        type: "reasoning",
        text: "Inspecting the workspace",
        state: "streaming",
      }],
    };
    const row: DisplayMessageRow = {
      id: "reasoning-row",
      message,
      sourceIndexes: [1],
    };

    expect(isStreamingReasoningAlreadyVisible({
      rows: [row],
      latestUser: 0,
    })).toBe(true);
  });

  it("does not suppress status for completed or prior-run reasoning", () => {
    const message: AssistantRenderableMessage = {
      id: "reasoning-message",
      role: "assistant",
      content: "",
      createdAt: new Date("2026-07-11T00:00:01.000Z"),
      parts: [{
        id: "reasoning-part",
        type: "reasoning",
        text: "Inspected the workspace",
        state: "done",
      }],
    };
    const row: DisplayMessageRow = {
      id: "reasoning-row",
      message,
      sourceIndexes: [1],
    };

    expect(isStreamingReasoningAlreadyVisible({
      rows: [row],
      latestUser: 0,
    })).toBe(false);
    expect(isStreamingReasoningAlreadyVisible({
      rows: [row],
      latestUser: 1,
    })).toBe(false);
  });
});
