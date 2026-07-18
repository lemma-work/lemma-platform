import { act, createElement } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { LemmaClient } from "../client.js";
import {
  useAssistantSession,
  type UseAssistantSessionResult,
} from "../react/index.js";

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const roots: Root[] = [];

function droppedStream(): ReadableStream<Uint8Array> {
  return new ReadableStream<Uint8Array>({
    start(controller) {
      controller.error(new Error("stream disconnected"));
    },
  });
}

function captureHookResult<T>() {
  let value: T | null = null;
  return {
    set(nextValue: T) {
      value = nextValue;
    },
    get() {
      if (!value) throw new Error("Hook result is not available.");
      return value;
    },
  };
}

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

afterEach(async () => {
  while (roots.length > 0) {
    const root = roots.pop();
    if (!root) continue;
    await act(async () => root.unmount());
  }
  document.body.innerHTML = "";
});

describe("assistant session stream recovery", () => {
  it("hydrates the completed server result after the foreground stream drops", async () => {
    const finalMessage = {
      id: "msg-persisted",
      role: "assistant",
      kind: "text",
      text: "Completed in the background",
      created_at: "2026-07-18T00:00:00.000Z",
      metadata: { is_final_answer: true },
    };
    const get = vi.fn(async (id: string) => ({
      id,
      pod_id: "pod-1",
      status: "COMPLETED",
    }));
    const messagesList = vi.fn(async () => ({
      items: [finalMessage],
      limit: 100,
      next_page_token: null,
    }));
    const resumeStream = vi.fn();
    const conversations = {
      create: async () => ({ id: "conv-1", status: "WAITING", pod_id: "pod-1" }),
      get,
      list: async () => ({ items: [], limit: 20, next_page_token: null }),
      messages: { list: messagesList },
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
    const session = captureHookResult<UseAssistantSessionResult>();

    function Harness() {
      session.set(useAssistantSession({ client, podId: "pod-1", autoLoad: false }));
      return null;
    }

    await render(createElement(Harness));
    await act(async () => {
      await session.get().createConversation();
    });
    await act(async () => {
      await session.get().sendMessage("finish this in the background");
    });

    expect(get).toHaveBeenCalledWith("conv-1", { pod_id: "pod-1" });
    expect(messagesList).toHaveBeenCalledWith("conv-1", {
      limit: 100,
      page_token: undefined,
    });
    expect(resumeStream).not.toHaveBeenCalled();
    expect(session.get()).toMatchObject({
      status: "COMPLETED",
      isStreaming: false,
      error: null,
      finalOutputText: "Completed in the background",
    });
    expect(session.get().messages).toContainEqual(finalMessage);
  });
});
