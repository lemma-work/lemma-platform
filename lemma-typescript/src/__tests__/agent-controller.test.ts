import { describe, expect, it, vi } from "vitest";
import type { LemmaClient } from "../client.js";
import { ConversationStatus } from "../openapi_client/index.js";
import { AgentController, selectAgentOutputs } from "../core/agent/index.js";

/** Build a ReadableStream of SSE frames from a list of event payloads. */
function sseStream(events: unknown[]): ReadableStream<Uint8Array> {
  const encoder = new TextEncoder();
  return new ReadableStream<Uint8Array>({
    start(controller) {
      for (const event of events) {
        const data = typeof event === "string" ? event : JSON.stringify(event);
        controller.enqueue(encoder.encode(`data: ${data}\n\n`));
      }
      controller.close();
    },
  });
}

function pacedSseStream(events: unknown[], intervalMs: number): ReadableStream<Uint8Array> {
  const encoder = new TextEncoder();
  let index = 0;
  return new ReadableStream<Uint8Array>({
    async pull(controller) {
      const event = events[index];
      if (event === undefined) {
        controller.close();
        return;
      }
      index += 1;
      const data = typeof event === "string" ? event : JSON.stringify(event);
      controller.enqueue(encoder.encode(`data: ${data}\n\n`));
      await new Promise((resolve) => setTimeout(resolve, intervalMs));
    },
  });
}

function droppedStream(): ReadableStream<Uint8Array> {
  return new ReadableStream<Uint8Array>({
    start(controller) {
      controller.error(new Error("stream disconnected"));
    },
  });
}

interface FakeClientHooks {
  events: unknown[];
  onSend?: (conversationId: string, content: string) => void;
  paceMs?: number;
}

function fakeClient({ events, onSend, paceMs }: FakeClientHooks): LemmaClient {
  const conversations = {
    create: async (payload: { pod_id?: string }) => ({
      id: "conv-1",
      status: "WAITING",
      pod_id: payload.pod_id ?? "pod-1",
    }),
    get: async (id: string) => ({ id, status: "WAITING" }),
    list: async () => ({ items: [], limit: 20, next_page_token: null }),
    messages: {
      list: async () => ({ items: [], limit: 100, next_page_token: null }),
    },
    sendMessageStream: async (
      conversationId: string,
      body: { content: string },
    ) => {
      onSend?.(conversationId, body.content);
      return paceMs ? pacedSseStream(events, paceMs) : sseStream(events);
    },
    resumeStream: async () => sseStream([]),
    stopRun: async () => ({ id: "conv-1", status: "WAITING" }),
  };

  return {
    podId: "pod-1",
    withPod() {
      return this as unknown as LemmaClient;
    },
    conversations,
  } as unknown as LemmaClient;
}

function makeController(events: unknown[], hooks: Partial<FakeClientHooks> = {}) {
  return new AgentController({
    client: fakeClient({ events, ...hooks }),
    scope: { podId: "pod-1", agentName: "triage" },
  });
}

describe("AgentController", () => {
  it("keeps received text and the server's stopping status until the run settles", async () => {
    const client = fakeClient({ events: [] });
    let channel!: ReadableStreamDefaultController<Uint8Array>;
    const encoder = new TextEncoder();
    const stream = new ReadableStream<Uint8Array>({ start(value) { channel = value; } });
    vi.spyOn(client.conversations, "sendMessageStream").mockResolvedValue(stream);
    vi.spyOn(client.conversations, "stopRun").mockImplementation(async (id) => ({
      ...await client.conversations.get(id), status: ConversationStatus.STOP_REQUESTED,
    }));
    const controller = new AgentController({ client, scope: { podId: "pod-1" } });
    await controller.createConversation();
    const sending = controller.sendMessage("start");
    channel.enqueue(encoder.encode(`data: ${JSON.stringify({ type: "token", kind: "text", data: "Partial answer" })}\n\n`));
    try {
      await vi.waitFor(() => expect(controller.getState().streamingText).toBe("Partial answer"));
      await controller.stop();
      expect(controller.getState().status).toBe("STOP_REQUESTED");
      expect(controller.getState().streamingText).toBe("Partial answer");
    } finally {
      channel.enqueue(encoder.encode(`data: ${JSON.stringify({ type: "completed", data: { status: "STOPPED" } })}\n\n`));
      channel.close();
      await sending;
      controller.destroy();
    }
  });

  it("streams a turn: tokens, final message, terminal status", async () => {
    const finalMessage = {
      id: "msg-1",
      role: "assistant",
      kind: "text",
      text: "Hello world",
      created_at: "2026-06-18T00:00:00.000Z",
      metadata: { is_final_answer: true },
    };
    const controller = makeController([
      { type: "token", data: "Hel", kind: "text" },
      { type: "token", data: "lo", kind: "text" },
      finalMessage,
      { type: "completed" },
    ]);

    let notifications = 0;
    const unsubscribe = controller.subscribe(() => {
      notifications += 1;
    });

    const conversation = await controller.createConversation();
    expect(conversation.id).toBe("conv-1");
    expect(controller.getState().conversationId).toBe("conv-1");

    await controller.sendMessage("hi");

    const state = controller.getState();
    expect(state.isStreaming).toBe(false);
    expect(state.status).toBe("COMPLETED");
    expect(state.messages).toHaveLength(1);
    expect(state.messages[0]?.id).toBe("msg-1");
    // Streaming buffer is cleared once the assistant message lands.
    expect(state.streamingText).toBe("");
    expect(state.streamingThinking).toBe("");
    expect(notifications).toBeGreaterThan(0);

    const outputs = selectAgentOutputs(state);
    expect(outputs.finalOutputText).toBe("Hello world");
    expect(outputs.finalOutput?.id).toBe("msg-1");

    unsubscribe();
  });

  it("streams thinking separately from answer text", async () => {
    const controller = makeController([
      { type: "token", data: "Considering", kind: "thinking" },
      {
        id: "thought-1",
        role: "assistant",
        kind: "thinking",
        text: "Considering",
        created_at: "2026-06-18T00:00:00.000Z",
      },
      { type: "token", data: "Done", kind: "text" },
      {
        id: "answer-1",
        role: "assistant",
        kind: "text",
        text: "Done",
        created_at: "2026-06-18T00:00:01.000Z",
      },
      { type: "completed" },
    ], { paceMs: 5 });

    const snapshots: Array<{ thinking: string; text: string }> = [];
    controller.subscribe(() => {
      const state = controller.getState();
      snapshots.push({
        thinking: state.streamingThinking,
        text: state.streamingText,
      });
    });

    await controller.createConversation();
    await controller.sendMessage("work");

    expect(controller.getState().messages.map((message) => message.kind)).toEqual([
      "thinking",
      "text",
    ]);
    expect(controller.getState().streamingThinking).toBe("");
    expect(controller.getState().streamingText).toBe("");
    expect(snapshots.some(({ thinking }) => thinking === "Considering")).toBe(true);
    expect(snapshots.some(({ text }) => text === "Done")).toBe(true);
    expect(snapshots.every(({ thinking, text }) => !(thinking && text))).toBe(true);
  });

  it("keeps an Agent Host call running from the token that follows it", async () => {
    // The host sends a call whole, so its message lands first and the `tool`
    // token after it -- the reverse of a streamed pydantic-ai call. The
    // indicator has to survive the message and last until the return.
    const call = {
      id: "call-msg",
      role: "assistant",
      kind: "TOOL_CALL",
      tool_name: "exec_command",
      tool_call_id: "call-1",
      tool_args: { cmd: "make test" },
      created_at: "2026-06-18T00:00:00.000Z",
      metadata: { tool_source: "native", tool_title: "make test" },
    };
    const controller = makeController([
      call,
      {
        type: "token",
        kind: "tool",
        data: JSON.stringify({ tool_name: "exec_command", tool_call_id: "call-1", args: { cmd: "make test" } }),
      },
      { type: "token", kind: "tool_output", data: "running 12 tests\n", tool_call_id: "call-1" },
      {
        id: "return-msg",
        role: "tool",
        kind: "TOOL_RETURN",
        tool_name: "exec_command",
        tool_call_id: "call-1",
        tool_result: { exit_code: 0, stdout: "ok" },
        created_at: "2026-06-18T00:00:01.000Z",
      },
      { type: "completed" },
    ], { paceMs: 5 });

    const seen: Array<{ tool: string | undefined; id: string | undefined; text: string }> = [];
    controller.subscribe(() => {
      const state = controller.getState();
      seen.push({ tool: state.streamingTool?.toolName, id: state.streamingTool?.toolCallId, text: state.streamingText });
    });

    await controller.createConversation();
    await controller.sendMessage("run the tests");

    expect(seen.some(({ tool, id }) => tool === "exec_command" && id === "call-1")).toBe(true);
    // Live terminal output is not answer text.
    expect(seen.every(({ text }) => text === "")).toBe(true);
    expect(controller.getState().streamingTool).toBeNull();
    expect(controller.getState().messages.map((message) => message.id)).toEqual(["call-msg", "return-msg"]);
  });

  it("surfaces a stream error as FAILED + error state", async () => {
    const controller = makeController([
      { type: "error", data: { message: "boom" } },
    ]);

    await controller.createConversation();
    await controller.sendMessage("hi");

    const state = controller.getState();
    expect(state.status).toBe("FAILED");
    expect(state.error?.message).toBe("boom");
    expect(state.isStreaming).toBe(false);
  });

  it("reconnects a dropped stream while the server run is still active", async () => {
    vi.useFakeTimers();
    try {
      const finalMessage = {
        id: "msg-recovered",
        role: "assistant",
        kind: "text",
        text: "Recovered result",
        created_at: "2026-07-18T00:00:00.000Z",
        metadata: { is_final_answer: true },
      };
      let markReconciliationStarted: (() => void) | undefined;
      const reconciliationStarted = new Promise<void>((resolve) => {
        markReconciliationStarted = resolve;
      });
      const get = vi.fn(async (id: string) => {
        markReconciliationStarted?.();
        return { id, status: "RUNNING" };
      });
      const resumeStream = vi.fn(async () => sseStream([
        finalMessage,
        { type: "completed" },
      ]));
      const conversations = {
        create: async () => ({ id: "conv-1", status: "WAITING", pod_id: "pod-1" }),
        get,
        list: async () => ({ items: [], limit: 20, next_page_token: null }),
        messages: {
          list: async () => ({ items: [], limit: 100, next_page_token: null }),
        },
        sendMessageStream: async () => droppedStream(),
        resumeStream,
        stopRun: async () => ({ id: "conv-1", status: "WAITING" }),
      };
      const client = {
        podId: "pod-1",
        withPod() {
          return this;
        },
        conversations,
      } as unknown as LemmaClient;
      const controller = new AgentController({
        client,
        scope: { podId: "pod-1", agentName: "triage" },
      });

      await controller.createConversation();
      const turn = controller.sendMessage("hi");
      await reconciliationStarted;

      expect(get).toHaveBeenCalledWith("conv-1", { pod_id: "pod-1" });
      expect(controller.getState().isStreaming).toBe(true);

      await vi.advanceTimersByTimeAsync(1_000);
      await turn;

      expect(resumeStream).toHaveBeenCalledOnce();
      expect(controller.getState()).toMatchObject({
        status: "COMPLETED",
        isStreaming: false,
        error: null,
      });
      expect(controller.getState().messages).toHaveLength(1);
      expect(controller.getState().messages[0]).toMatchObject(finalMessage);
    } finally {
      vi.useRealTimers();
    }
  });

  function reconnectingClient(options: {
    resumeStream: () => Promise<ReadableStream<Uint8Array>>;
    authStatus?: () => string;
  }): LemmaClient {
    return {
      podId: "pod-1",
      withPod() {
        return this;
      },
      auth: { getState: () => ({ status: options.authStatus?.() ?? "authenticated", user: null }) },
      conversations: {
        create: async () => ({ id: "conv-1", status: "WAITING", pod_id: "pod-1" }),
        get: async (id: string) => ({ id, status: "RUNNING" }),
        list: async () => ({ items: [], limit: 20, next_page_token: null }),
        messages: { list: async () => ({ items: [], limit: 100, next_page_token: null }) },
        sendMessageStream: async () => droppedStream(),
        resumeStream: options.resumeStream,
        stopRun: async () => ({ id: "conv-1", status: "WAITING" }),
      },
    } as unknown as LemmaClient;
  }

  it("backs off on a resumed stream that opens and closes with nothing in it", async () => {
    vi.useFakeTimers();
    try {
      const resumeStream = vi.fn(async () => sseStream([]));
      const controller = new AgentController({
        client: reconnectingClient({ resumeStream }),
        scope: { podId: "pod-1", agentName: "triage" },
      });
      await controller.createConversation();
      void controller.sendMessage("hi");

      /* Resetting the backoff on connect made this every second, forever.
         Backing off, the first 15 s hold 1 + 2 + 4 + 8. */
      await vi.advanceTimersByTimeAsync(15_000);
      expect(resumeStream).toHaveBeenCalledTimes(4);
      controller.cancel();
    } finally {
      vi.useRealTimers();
    }
  });

  it("stops reconnecting once the session is signed out", async () => {
    vi.useFakeTimers();
    try {
      let status = "authenticated";
      const resumeStream = vi.fn(async () => {
        status = "unauthenticated";
        throw Object.assign(new Error("Unauthorized"), { statusCode: 401 });
      });
      const controller = new AgentController({
        client: reconnectingClient({ resumeStream, authStatus: () => status }),
        scope: { podId: "pod-1", agentName: "triage" },
      });
      await controller.createConversation();
      const turn = controller.sendMessage("hi");

      await vi.advanceTimersByTimeAsync(120_000);
      await turn;
      expect(resumeStream).toHaveBeenCalledOnce();
      expect(controller.getState().isStreaming).toBe(false);
    } finally {
      vi.useRealTimers();
    }
  });

  it("setConversationId resets the session snapshot", async () => {
    const controller = makeController([
      {
        id: "msg-1",
        role: "assistant",
        kind: "text",
        text: "done",
        created_at: "2026-06-18T00:00:00.000Z",
      },
      { type: "completed" },
    ]);

    await controller.createConversation();
    await controller.sendMessage("hi");
    expect(controller.getState().messages).toHaveLength(1);

    controller.setConversationId("conv-2");
    const state = controller.getState();
    expect(state.conversationId).toBe("conv-2");
    expect(state.messages).toHaveLength(0);
    expect(state.status).toBeUndefined();
    expect(state.error).toBeNull();
  });

  it("notifies subscribers on each state transition and stops after unsubscribe", async () => {
    const controller = makeController([{ type: "completed" }]);
    let count = 0;
    const unsubscribe = controller.subscribe(() => {
      count += 1;
    });

    await controller.createConversation();
    const afterCreate = count;
    expect(afterCreate).toBeGreaterThan(0);

    unsubscribe();
    await controller.sendMessage("hi");
    expect(count).toBe(afterCreate);
  });
});
