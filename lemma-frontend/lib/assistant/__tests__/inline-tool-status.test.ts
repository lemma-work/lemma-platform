import { describe, expect, it } from "vitest";
import type { AssistantRenderableMessage, AssistantToolInvocation } from "lemma-sdk/react";
import { buildDisplayMessageRows } from "lemma-sdk";
import {
  currentToolStatusLabel,
  isInlineToolStatusAlreadyVisible,
  currentRunStatusLabel,
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

// The arbitration this file used to test is gone. Nothing in the transcript
// competes for the word "Thinking" any more — a streaming thought renders as
// prose and a tool group renders as one "Ran 3 commands" line — so there is one
// status line and its only job is never to go quiet mid-run.
describe("the run status line", () => {
  function assistantToolMessage(sequence: number): AssistantRenderableMessage {
    return {
      id: `tool-message-${sequence}`,
      role: "assistant",
      content: "",
      createdAt: new Date("2026-07-11T00:00:01.000Z"),
      toolInvocations: [{
        toolCallId: `call-${sequence}`,
        toolName: "list_tables",
        args: {},
        state: "call",
      }],
    };
  }

  const userMessage: AssistantRenderableMessage = {
    id: "user-1",
    role: "user",
    content: "list the tables",
    createdAt: new Date("2026-07-11T00:00:00.000Z"),
  };

  const nowMs = new Date("2026-07-11T00:00:09.000Z").getTime();

  it("says something while the run is only calling tools", () => {
    // The old implementation returned null here — a run working through tools
    // with no assistant text yet — so the transcript showed no sign of life.
    const messages = [userMessage, assistantToolMessage(1)];
    const status = currentRunStatusLabel({
      messages,
      rows: buildDisplayMessageRows(messages),
      isConversationBusy: true,
      nowMs,
    });

    expect(status).not.toBeNull();
    expect(status?.label).toMatch(/^Working for /);
  });

  it("says Thinking before the run has produced anything", () => {
    const messages = [userMessage];
    expect(currentRunStatusLabel({
      messages,
      rows: buildDisplayMessageRows(messages),
      isConversationBusy: true,
      nowMs,
    })).toEqual({ label: "Thinking", shimmer: true });
  });

  it("goes quiet once the run is no longer busy", () => {
    const messages = [userMessage, assistantToolMessage(1)];
    expect(currentRunStatusLabel({
      messages,
      rows: buildDisplayMessageRows(messages),
      isConversationBusy: false,
      nowMs,
    })).toBeNull();
  });
});
