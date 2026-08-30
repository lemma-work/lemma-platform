import { act, createElement } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";
import { parseAssistantStreamEvent } from "../assistant-events.js";
import type { LemmaClient } from "../client.js";
import { AgentController } from "../core/agent/index.js";
import { useAssistantSession, type UseAssistantSessionResult } from "../react/index.js";

/**
 * The server names a conversation from its first user message, in a background
 * job started when the run starts — so the title lands *mid-turn*, on the
 * conversation channel rather than the run's, and reaches every client that is
 * already streaming. These pin the client half of that trip: the frame the
 * backend actually publishes, and what holding it does to the record on screen.
 */

/** Exactly what `encode_stream_chunk` puts on the wire for a rename. */
function titleFrame(conversationId: string, title: string) {
  return { type: "title", data: { conversation_id: conversationId, title }, agent_run_id: null };
}

function sseStream(events: unknown[]): ReadableStream<Uint8Array> {
  const encoder = new TextEncoder();
  return new ReadableStream<Uint8Array>({
    start(controller) {
      for (const event of events) {
        controller.enqueue(encoder.encode(`data: ${JSON.stringify(event)}\n\n`));
      }
      controller.close();
    },
  });
}

function fakeClient(events: unknown[]): LemmaClient {
  return {
    podId: "pod-1",
    withPod() {
      return this as unknown as LemmaClient;
    },
    conversations: {
      create: async () => ({ id: "conv-1", status: "WAITING", pod_id: "pod-1", title: null }),
      get: async (id: string) => ({ id, status: "COMPLETED" }),
      list: async () => ({ items: [], limit: 20, next_page_token: null }),
      messages: { list: async () => ({ items: [], limit: 100, next_page_token: null }) },
      sendMessageStream: async () => sseStream(events),
      resumeStream: async () => sseStream([]),
    },
  } as unknown as LemmaClient;
}

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const roots: Root[] = [];

afterEach(async () => {
  while (roots.length > 0) {
    const root = roots.pop();
    if (!root) continue;
    await act(async () => root.unmount());
  }
  document.body.innerHTML = "";
});

async function render(element: ReturnType<typeof createElement>) {
  const container = document.createElement("div");
  document.body.appendChild(container);
  const root = createRoot(container);
  roots.push(root);
  await act(async () => {
    root.render(element);
    await Promise.resolve();
  });
}

describe("the rename frame", () => {
  it("carries the new title and the conversation it belongs to", () => {
    expect(parseAssistantStreamEvent(titleFrame("conv-1", "Japan Spring Trip Plan"))).toEqual({
      title: "Japan Spring Trip Plan",
      conversationId: "conv-1",
    });
  });

  it("says nothing about the run", () => {
    // A rename arrives while the agent is still working. Reading any status off
    // it would end a turn that has not ended.
    const parsed = parseAssistantStreamEvent(titleFrame("conv-1", "Quarterly numbers"));
    expect(parsed.status).toBeUndefined();
    expect(parsed.message).toBeUndefined();
    expect(parsed.error).toBeUndefined();
  });

  it("is ignored when it carries no title", () => {
    expect(parseAssistantStreamEvent({ type: "title", data: { title: "   " } })).toEqual({});
  });
});

describe("AgentController rename handling", () => {
  it("renames the conversation it is holding, mid-stream", async () => {
    const onTitle = vi.fn();
    const controller = new AgentController({
      client: fakeClient([
        { type: "token", data: "Look", kind: "text" },
        titleFrame("conv-1", "Ashwin's records"),
        { type: "completed" },
      ]),
      scope: { podId: "pod-1", agentName: "triage" },
      onTitle,
    });

    await controller.createConversation();
    expect(controller.getState().conversation?.title).toBeNull();

    await controller.sendMessage("who has the most centuries");

    expect(controller.getState().conversation?.title).toBe("Ashwin's records");
    expect(onTitle).toHaveBeenCalledWith("Ashwin's records", "conv-1");
    // The turn ran to its own end: the rename did not terminate it.
    expect(controller.getState().status).toBe("COMPLETED");
  });

  it("leaves another conversation's record alone", async () => {
    const onTitle = vi.fn();
    const controller = new AgentController({
      client: fakeClient([
        titleFrame("conv-other", "Not this one"),
        { type: "completed" },
      ]),
      scope: { podId: "pod-1" },
      onTitle,
    });

    await controller.createConversation();
    await controller.sendMessage("hi");

    expect(controller.getState().conversation?.title).toBeNull();
    // Still reported: a caller holding a list of conversations owns the row the
    // controller does not, and that row is the one being renamed.
    expect(onTitle).toHaveBeenCalledWith("Not this one", "conv-other");
  });
});

describe("useAssistantSession rename handling", () => {
  it("renames the record on screen and reports the rename to its owner", async () => {
    // The hook the product renders through: `useAssistantController` wraps this
    // one, and the sidebar row it renames is a record the controller owns, not
    // this hook's.
    const onTitle = vi.fn();
    const client = fakeClient([
      { type: "token", data: "Look", kind: "text" },
      titleFrame("conv-1", "Ashwin's records"),
      { type: "completed" },
    ]);

    let session: UseAssistantSessionResult | null = null;
    function Harness() {
      session = useAssistantSession({ client, podId: "pod-1", autoLoad: false, onTitle });
      return null;
    }

    await render(createElement(Harness));
    await act(async () => {
      await session!.createConversation();
    });
    expect(session!.conversation?.title).toBeNull();

    await act(async () => {
      await session!.sendMessage("who has the most centuries");
    });

    expect(session!.conversation?.title).toBe("Ashwin's records");
    expect(onTitle).toHaveBeenCalledWith("Ashwin's records", "conv-1");
    expect(session!.status).toBe("COMPLETED");
  });
});
