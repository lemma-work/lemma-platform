// Regression coverage for the pagination/reload report: a reloaded
// conversation renders its transcript, whatever shape the message window is —
// mid-turn starts, unflagged history, tool-only stretches, notifications.

import { describe, expect, it } from "vitest";
import { buildDisplayMessageRows, type AssistantRenderableMessage } from "lemma-sdk";
import { buildChatTurns } from "../turns";

const T0 = new Date("2026-08-20T13:00:00Z").getTime();

function msg(partial: Partial<AssistantRenderableMessage> & { role: string }): AssistantRenderableMessage {
  return {
    id: partial.id ?? `m-${Math.random().toString(36).slice(2)}`,
    kind: "TEXT",
    content: "",
    ...partial,
  } as AssistantRenderableMessage;
}

function longHistory(count: number): AssistantRenderableMessage[] {
  const messages: AssistantRenderableMessage[] = [];
  for (let i = 0; i < count; i += 1) {
    const at = new Date(T0 + i * 60_000);
    if (i % 5 === 0) {
      messages.push(msg({ id: `u-${i}`, role: "user", content: `question ${i}`, createdAt: at }));
    } else if (i % 5 === 1) {
      // Old history: no metadata flags at all.
      messages.push(msg({ id: `a-${i}`, role: "assistant", content: `answer ${i} — with a fair amount of text`, createdAt: at }));
    } else if (i % 5 === 2) {
      messages.push(msg({
        id: `t-${i}`,
        role: "assistant",
        kind: "TOOL_CALL",
        createdAt: at,
        tool_name: "exec_command",
        tool_call_id: `call-${i}`,
        toolInvocations: [{ toolCallId: `call-${i}`, toolName: "exec_command", args: { cmd: `run ${i}` }, state: "result", result: { success: true } }],
        parts: [{ id: `p-${i}`, type: "tool", toolInvocation: { toolCallId: `call-${i}`, toolName: "exec_command", args: { cmd: `run ${i}` }, state: "result", result: { success: true } } }],
      }));
    } else if (i % 5 === 3) {
      messages.push(msg({
        id: `th-${i}`,
        role: "assistant",
        kind: "THINKING",
        content: `thinking ${i}`,
        createdAt: at,
        parts: [{ id: `r-${i}`, type: "reasoning", text: `thinking ${i}`, state: "done" }],
      }));
    } else {
      messages.push(msg({ id: `n-${i}`, role: "system", kind: "NOTIFICATION", content: `notification ${i}`, createdAt: at }));
    }
  }
  return messages;
}

function turnsForWindow(messages: AssistantRenderableMessage[]) {
  return buildChatTurns({
    rows: buildDisplayMessageRows(messages),
    messages,
    isRunActive: false,
    podId: "pod-1",
    conversationId: "conv-1",
  });
}

describe("buildChatTurns over paginated windows", () => {
  it("a full history renders every turn", () => {
    const turns = turnsForWindow(longHistory(60));
    expect(turns.length).toBeGreaterThan(8);
    expect(turns.every((turn) => turn.userMessage || turn.items.length > 0 || turn.trace.length > 0)).toBe(true);
  });

  it("a window that starts mid-turn still renders — the ask can live in an older page", () => {
    // The exact reload regression: a page boundary that lands on assistant
    // work (no user message in the window) must still produce its turn.
    const windowMessages = [
      msg({
        id: "t-1",
        role: "assistant",
        kind: "TOOL_CALL",
        createdAt: new Date(T0),
        tool_name: "exec_command",
        tool_call_id: "call-1",
        toolInvocations: [{ toolCallId: "call-1", toolName: "exec_command", args: { cmd: "make" }, state: "result", result: { success: true } }],
        parts: [{ id: "p-1", type: "tool", toolInvocation: { toolCallId: "call-1", toolName: "exec_command", args: { cmd: "make" }, state: "result", result: { success: true } } }],
      }),
      msg({ id: "a-1", role: "assistant", content: "Done.", createdAt: new Date(T0 + 60_000) }),
    ];
    const turns = turnsForWindow(windowMessages);
    expect(turns).toHaveLength(1);
    expect(turns[0].userMessage).toBeNull();
    expect(turns[0].trace).toHaveLength(1);
    expect(turns[0].items[0]).toMatchObject({ kind: "text", text: "Done.", answer: true });
  });

  it("a window that is only a user question renders", () => {
    const turns = turnsForWindow([longHistory(60)[0]]);
    expect(turns).toHaveLength(1);
    expect(turns[0].userMessage?.content).toBe("question 0");
  });

  it("a window of only notifications renders as notices, not blank", () => {
    const messages = longHistory(60).filter((m) => m.kind === "NOTIFICATION");
    const turns = turnsForWindow(messages);
    expect(turns.length).toBeGreaterThan(0);
    expect(turns.flatMap((turn) => turn.items).every((item) => item.kind === "notice")).toBe(true);
  });

  it("messages with null content or missing parts never blank the transcript", () => {
    const weird: AssistantRenderableMessage[] = [
      msg({ id: "u-1", role: "user", content: "hello", createdAt: new Date(T0) }),
      msg({ id: "a-1", role: "assistant", content: null as unknown as string, createdAt: new Date(T0 + 1000) }),
      msg({ id: "a-2", role: "assistant", content: undefined as unknown as string, createdAt: new Date(T0 + 2000) }),
      msg({ id: "a-3", role: "assistant", content: "real answer", createdAt: new Date(T0 + 3000) }),
    ];
    const turns = turnsForWindow(weird);
    expect(turns).toHaveLength(1);
    const texts = turns[0].items.filter((item) => item.kind === "text");
    expect(texts.map((item) => item.kind === "text" && item.text)).toEqual(["real answer"]);
  });
});
