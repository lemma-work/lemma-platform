import { act, createElement } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { LemmaClient } from "../client.js";
import type { Conversation } from "../types.js";
import { useAssistantController, type UseAssistantControllerResult } from "../react/index.js";

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const roots: Root[] = [];

function conversation(id: string, status: string): Conversation {
  return {
    id,
    pod_id: "pod-1",
    title: id,
    status,
    created_at: "2026-07-15T12:00:00.000Z",
    updated_at: "2026-07-15T12:00:00.000Z",
  } as Conversation;
}

function fakeClient(items: Conversation[]) {
  const encoder = new TextEncoder();
  const list = vi.fn(async () => ({ items, limit: 30, next_page_token: null, total: items.length }));
  const get = vi.fn(async (id: string) => (
    items.find((item) => item.id === id) ?? conversation(id, "WAITING")
  ));
  const messagesList = vi.fn(async () => ({ items: [], limit: 100, next_page_token: null }));
  const resumeStream = vi.fn(async () => new ReadableStream<Uint8Array>({
    start(controller) {
      controller.enqueue(encoder.encode('data: {"type":"completed"}\n\n'));
      controller.close();
    },
  }));

  const client = {
    podId: "pod-1",
    withPod() {
      return this;
    },
    conversations: {
      list,
      get,
      create: vi.fn(),
      listModels: vi.fn(async () => ({ items: [] })),
      messages: { list: messagesList },
      sendMessageStream: vi.fn(),
      retryFailedRun: vi.fn(),
      resumeStream,
      stopRun: vi.fn(),
      update: vi.fn(),
    },
  } as unknown as LemmaClient;

  return { client, get, list, messagesList, resumeStream };
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
  return root;
}

async function settle() {
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0));
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

async function openConversation(client: LemmaClient, conversationId: string) {
  const controller = { current: null as UseAssistantControllerResult | null };

  function Harness() {
    controller.current = useAssistantController({
      client,
      podId: "pod-1",
      autoLoadMessages: true,
    });
    return null;
  }

  await render(createElement(Harness));
  await settle();
  await act(async () => controller.current?.openConversation(conversationId));
  await settle();
  await settle();
  return controller;
}

describe("opening a conversation", () => {
  it("reads the conversation once", async () => {
    const { client, get, messagesList } = fakeClient([conversation("older", "WAITING")]);

    await openConversation(client, "older");

    // The transcript load used to be followed by a resume probe that fetched
    // the same conversation again purely to read its status.
    expect(get.mock.calls).toEqual([["older", { pod_id: "pod-1" }]]);
    expect(messagesList).toHaveBeenCalledOnce();
  });

  it("resumes a running conversation without reading it twice", async () => {
    const { client, get, resumeStream } = fakeClient([conversation("live", "RUNNING")]);
    // Counted when the resume starts, not at the end: a finished run syncs the
    // conversation once more, and that read is the run's, not the open's.
    let readsBeforeResume = -1;
    resumeStream.mockImplementationOnce(async () => {
      readsBeforeResume = get.mock.calls.length;
      return new ReadableStream<Uint8Array>({
        start(controller) {
          controller.enqueue(new TextEncoder().encode('data: {"type":"completed"}\n\n'));
          controller.close();
        },
      });
    });

    await openConversation(client, "live");

    expect(resumeStream).toHaveBeenCalledOnce();
    expect(readsBeforeResume).toBe(1);
  });

  it("falls back to the session probe when the open fetch failed", async () => {
    const { client, get, resumeStream } = fakeClient([conversation("live", "RUNNING")]);
    get.mockRejectedValueOnce(new Error("Network unavailable"));
    let readsBeforeResume = -1;
    resumeStream.mockImplementationOnce(async () => {
      readsBeforeResume = get.mock.calls.length;
      return new ReadableStream<Uint8Array>({
        start(controller) {
          controller.enqueue(new TextEncoder().encode('data: {"type":"completed"}\n\n'));
          controller.close();
        },
      });
    });

    await openConversation(client, "live");

    // Nothing to hand over, so the session asks for the conversation itself
    // rather than assuming the run is dead.
    expect(resumeStream).toHaveBeenCalledOnce();
    expect(readsBeforeResume).toBe(2);
  });
});
