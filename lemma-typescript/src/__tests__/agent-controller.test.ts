import { describe, expect, it, vi } from "vitest";
import type { LemmaClient } from "../client.js";
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
}

function fakeClient({ events, onSend }: FakeClientHooks): LemmaClient {
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
      return sseStream(events);
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
    expect(notifications).toBeGreaterThan(0);

    const outputs = selectAgentOutputs(state);
    expect(outputs.finalOutputText).toBe("Hello world");
    expect(outputs.finalOutput?.id).toBe("msg-1");

    unsubscribe();
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
