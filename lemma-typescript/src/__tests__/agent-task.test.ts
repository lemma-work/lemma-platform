import { describe, expect, it } from "vitest";
import type { LemmaClient } from "../client.js";
import {
  AgentController,
  agentActivityLabel,
  selectAgentTask,
  type AgentSessionState,
} from "../core/agent/index.js";

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

function fakeClient(events: unknown[]): LemmaClient {
  const conversations = {
    create: async () => ({ id: "conv-1", status: "WAITING" }),
    get: async (id: string) => ({ id, status: "WAITING" }),
    messages: { list: async () => ({ items: [], limit: 100, next_page_token: null }) },
    sendMessageStream: async () => sseStream(events),
    resumeStream: async () => sseStream([]),
    stopRun: async () => ({ id: "conv-1", status: "WAITING" }),
    list: async () => ({ items: [], limit: 20, next_page_token: null }),
  };
  return {
    podId: "pod-1",
    withPod() {
      return this as unknown as LemmaClient;
    },
    conversations,
  } as unknown as LemmaClient;
}

async function runTask(events: unknown[]): Promise<AgentController> {
  const controller = new AgentController({
    client: fakeClient(events),
    scope: { podId: "pod-1", agentName: "classify" },
  });
  await controller.createConversation();
  await controller.sendMessage("input");
  return controller;
}

function assistantMessage(text: string) {
  return {
    id: "msg-1",
    role: "assistant",
    kind: "text",
    text,
    created_at: "2026-06-18T00:00:00.000Z",
    metadata: { is_final_answer: true },
  };
}

describe("selectAgentTask", () => {
  it("parses structured JSON output on a completed run", async () => {
    const controller = await runTask([
      assistantMessage('{"label":"spam","score":0.9}'),
      { type: "completed" },
    ]);
    const task = selectAgentTask<{ label: string; score: number }>(controller.getState());
    expect(task.status).toBe("done");
    expect(task.isDone).toBe(true);
    expect(task.output).toEqual({ label: "spam", score: 0.9 });
    expect(task.outputText).toBe('{"label":"spam","score":0.9}');
  });

  it("leaves output null with parseOutput: false", async () => {
    const controller = await runTask([
      assistantMessage('{"label":"spam"}'),
      { type: "completed" },
    ]);
    const task = selectAgentTask(controller.getState(), { parseOutput: false });
    expect(task.status).toBe("done");
    expect(task.output).toBeNull();
    expect(task.outputText).toBe('{"label":"spam"}');
  });

  it("returns plain text output for a non-JSON answer", async () => {
    const controller = await runTask([
      assistantMessage("just words"),
      { type: "completed" },
    ]);
    const task = selectAgentTask(controller.getState());
    expect(task.status).toBe("done");
    expect(task.output).toBeNull();
    expect(task.outputText).toBe("just words");
  });

  it("is idle before any run", () => {
    const controller = new AgentController({
      client: fakeClient([]),
      scope: { podId: "pod-1", agentName: "classify" },
    });
    const task = selectAgentTask(controller.getState());
    expect(task.status).toBe("idle");
    expect(task.isRunning).toBe(false);
    expect(task.output).toBeNull();
  });

  it("reports error status on a failed run", async () => {
    const controller = await runTask([{ type: "error", data: { message: "nope" } }]);
    const task = selectAgentTask(controller.getState());
    expect(task.status).toBe("error");
    expect(task.error?.message).toBe("nope");
  });
});

describe("agentActivityLabel", () => {
  const base: AgentSessionState = {
    conversationId: "c",
    conversation: null,
    status: "RUNNING",
    messages: [],
    streamingText: "",
    streamingThinking: "",
    streamingTool: null,
    isStreaming: true,
    error: null,
  };

  it("names the active tool", () => {
    expect(
      agentActivityLabel({
        ...base,
        streamingTool: { toolName: "search", state: "call" },
      }),
    ).toBe("Using search");
  });

  it("falls back to Working while running", () => {
    expect(agentActivityLabel(base)).toBe("Working…");
  });

  it("is empty once settled", () => {
    expect(
      agentActivityLabel({ ...base, isStreaming: false, status: "COMPLETED" }),
    ).toBe("");
  });
});
