import { describe, expect, it } from "vitest";
import { mapConversationMessages } from "../react/useAssistantController.js";
import type { AssistantRenderableMessage, AssistantToolInvocation } from "../core/agent/renderable.js";

// The transcript is mapped once, here. Three consumers used to re-fetch the raw
// messages and merge tool returns a second time on top of this output, on the
// belief that the mapper dropped them. These cases pin what the mapper actually
// produces, so that belief cannot come back.
//
// See docs/design/conversation-messages.md.

let clock = 0;
function at(): string {
  clock += 1000;
  return new Date(Date.UTC(2026, 0, 1, 0, 0, 0) + clock).toISOString();
}

function userText(id: string, text: string) {
  return { id, role: "user", kind: "TEXT", text, created_at: at(), conversation_id: "c1" } as never;
}

function assistantText(id: string, text: string) {
  return { id, role: "assistant", kind: "TEXT", text, created_at: at(), conversation_id: "c1" } as never;
}

function toolCall(id: string, toolCallId: string, toolName: string, args: Record<string, unknown>) {
  return {
    id,
    role: "assistant",
    kind: "TOOL_CALL",
    tool_call_id: toolCallId,
    tool_name: toolName,
    tool_args: args,
    created_at: at(),
    conversation_id: "c1",
  } as never;
}

function toolReturn(id: string, toolCallId: string, toolName: string, result: unknown) {
  return {
    id,
    role: "tool",
    kind: "TOOL_RETURN",
    tool_call_id: toolCallId,
    tool_name: toolName,
    tool_result: result,
    created_at: at(),
    conversation_id: "c1",
  } as never;
}

function allInvocations(messages: AssistantRenderableMessage[]): AssistantToolInvocation[] {
  return messages.flatMap((message) => message.toolInvocations ?? []);
}

describe("mapConversationMessages", () => {
  it("folds a TOOL_RETURN into its originating TOOL_CALL", () => {
    const mapped = mapConversationMessages([
      userText("m1", "list the tables"),
      toolCall("m2", "call-1", "pod_tables", { datastore: "main" }),
      toolReturn("m3", "call-1", "pod_tables", { tables: ["orders", "customers"] }),
      assistantText("m4", "You have two tables."),
    ]);

    const invocations = allInvocations(mapped);
    expect(invocations).toHaveLength(1);
    expect(invocations[0]).toMatchObject({
      toolCallId: "call-1",
      toolName: "pod_tables",
      state: "result",
      args: { datastore: "main" },
      result: { tables: ["orders", "customers"] },
    });
  });

  it("leaves nothing for a second tool-return merge to do", () => {
    const mapped = mapConversationMessages([
      userText("m1", "check both"),
      toolCall("m2", "call-1", "pod_tables", {}),
      toolReturn("m3", "call-1", "pod_tables", { ok: true }),
      toolCall("m4", "call-2", "pod_records", { table: "orders" }),
      toolReturn("m5", "call-2", "pod_records", { count: 12 }),
      assistantText("m6", "Done."),
    ]);

    // This is the invariant the deleted `hydrateToolReturnMessages` violated:
    // every invocation is already resolved, so re-applying the raw returns
    // could only rewrite each value to itself.
    const invocations = allInvocations(mapped);
    expect(invocations).toHaveLength(2);
    for (const invocation of invocations) {
      expect(invocation.state).toBe("result");
      expect(invocation.result).toBeDefined();
    }
    expect(invocations.map((invocation) => invocation.result)).toEqual([
      { ok: true },
      { count: 12 },
    ]);
  });

  it("passes the flat message fields through untouched", () => {
    const mapped = mapConversationMessages([
      toolCall("m1", "call-1", "pod_tables", { datastore: "main" }),
    ]);

    // The fields three consumers re-fetched raw messages to recover.
    expect(mapped[0]).toMatchObject({
      kind: "TOOL_CALL",
      tool_call_id: "call-1",
      tool_name: "pod_tables",
    });
    expect(mapped[0].tool_args).toEqual({ datastore: "main" });
  });

  it("keeps a tool return that never had a matching call", () => {
    const mapped = mapConversationMessages([
      userText("m1", "resume"),
      toolReturn("m2", "orphan-1", "pod_tables", { tables: [] }),
    ]);

    const invocations = allInvocations(mapped);
    expect(invocations).toHaveLength(1);
    expect(invocations[0]).toMatchObject({
      toolCallId: "orphan-1",
      state: "result",
      result: { tables: [] },
    });
  });

  it("normalizes a non-object tool result rather than dropping it", () => {
    const mapped = mapConversationMessages([
      toolCall("m1", "call-1", "pod_query", {}),
      toolReturn("m2", "call-1", "pod_query", "42 rows"),
    ]);

    expect(allInvocations(mapped)[0]).toMatchObject({
      state: "result",
      result: { output: "42 rows" },
    });
  });

  it("recovers the tool name from the return when the call was generic", () => {
    const mapped = mapConversationMessages([
      toolCall("m1", "call-1", "tool", {}),
      toolReturn("m2", "call-1", "pod_tables", { ok: true }),
    ]);

    expect(allInvocations(mapped)[0].toolName).toBe("pod_tables");
  });
});
