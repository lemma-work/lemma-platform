import { act, createElement } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { LemmaClient } from "../client.js";
import type { Conversation } from "../types.js";
import { useAssistantController, type UseAssistantControllerResult } from "../react/index.js";

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const roots: Root[] = [];

function conversation(id: string): Conversation {
  return {
    id,
    pod_id: "pod-1",
    title: id,
    status: "WAITING",
    created_at: "2026-07-15T12:00:00.000Z",
    updated_at: "2026-07-15T12:00:00.000Z",
  } as Conversation;
}

function fakeClient(ids: string[], options: { empty?: boolean } = {}) {
  const items = ids.map(conversation);
  const messagesList = vi.fn(async (id: string) => ({
    items: options.empty ? [] : [{
      id: `${id}-m1`,
      conversation_id: id,
      role: "user",
      kind: "TEXT",
      text: `hello from ${id}`,
      created_at: "2026-07-15T12:00:01.000Z",
      metadata: null,
    }],
    limit: 100,
    next_page_token: null,
  }));

  const client = {
    podId: "pod-1",
    withPod() {
      return this;
    },
    conversations: {
      list: vi.fn(async () => ({ items, limit: 30, next_page_token: null, total: items.length })),
      get: vi.fn(async (id: string) => conversation(id)),
      create: vi.fn(async () => conversation("fresh")),
      listModels: vi.fn(async () => ({ items: [] })),
      messages: { list: messagesList },
      sendMessageStream: vi.fn(async () => new ReadableStream<Uint8Array>({
        start(streamController) {
          streamController.enqueue(new TextEncoder().encode('data: {"type":"completed"}\n\n'));
          streamController.close();
        },
      })),
      retryFailedRun: vi.fn(),
      resumeStream: vi.fn(),
      stopRun: vi.fn(),
      update: vi.fn(),
    },
  } as unknown as LemmaClient;

  return { client, messagesList };
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

async function mount(client: LemmaClient) {
  const controller = { current: null as UseAssistantControllerResult | null };

  function Harness() {
    controller.current = useAssistantController({ client, podId: "pod-1", autoLoadMessages: true });
    return null;
  }

  const container = document.createElement("div");
  document.body.appendChild(container);
  const root = createRoot(container);
  roots.push(root);
  await act(async () => {
    root.render(createElement(Harness));
    await Promise.resolve();
  });
  await settle();
  return controller;
}

async function open(
  controller: { current: UseAssistantControllerResult | null },
  conversationId: string,
) {
  await act(async () => controller.current?.openConversation(conversationId));
  await settle();
  await settle();
}

function fetchesFor(messagesList: ReturnType<typeof vi.fn>, conversationId: string) {
  return messagesList.mock.calls.filter(([id]) => id === conversationId).length;
}

describe("re-opening a conversation the store no longer holds", () => {
  it("re-fetches a transcript that retention evicted", async () => {
    // The store retains the last five transcripts, so opening a sixth drops the
    // first. Re-opening it has to be a load, not a silent cache hit.
    const ids = ["c1", "c2", "c3", "c4", "c5", "c6"];
    const { client, messagesList } = fakeClient(ids);
    const controller = await mount(client);

    for (const id of ids) {
      await open(controller, id);
    }
    const before = fetchesFor(messagesList, "c1");

    await open(controller, "c1");

    expect(fetchesFor(messagesList, "c1")).toBe(before + 1);
    expect(controller.current?.isLoadingMessages).toBe(false);
    expect(controller.current?.messages.length).toBeGreaterThan(0);
  });

  it("still opens a retained transcript without re-fetching it", async () => {
    const { client, messagesList } = fakeClient(["c1", "c2"]);
    const controller = await mount(client);

    await open(controller, "c1");
    await open(controller, "c2");
    const before = fetchesFor(messagesList, "c1");

    await open(controller, "c1");

    expect(fetchesFor(messagesList, "c1")).toBe(before);
    expect(controller.current?.isLoadingMessages).toBe(false);
    expect(controller.current?.messages.length).toBeGreaterThan(0);
  });

  it("leaves no spinner running on a conversation that has no messages", async () => {
    // An empty transcript is indistinguishable from an evicted one by contents
    // alone, so a guard that asks "are we holding messages?" gets this wrong.
    const { client, messagesList } = fakeClient(["empty", "other"], { empty: true });
    const controller = await mount(client);

    await open(controller, "empty");
    await open(controller, "other");
    const before = fetchesFor(messagesList, "empty");

    await open(controller, "empty");

    expect(controller.current?.isLoadingMessages).toBe(false);
    // Still resident — an empty conversation is retained like any other, so
    // re-opening it must not turn into a fetch on every click.
    expect(fetchesFor(messagesList, "empty")).toBe(before);
  });

  it("re-fetches after starting a new conversation dropped the store", async () => {
    // Creating a conversation clears the runtime store, which evicts every
    // transcript it was holding.
    const { client, messagesList } = fakeClient(["c1"]);
    const controller = await mount(client);

    await open(controller, "c1");
    const before = fetchesFor(messagesList, "c1");

    await act(async () => {
      controller.current?.closeConversation();
    });
    await settle();
    await act(async () => {
      await controller.current?.sendMessage("a new thread");
    });
    await settle();

    await open(controller, "c1");

    expect(fetchesFor(messagesList, "c1")).toBe(before + 1);
    expect(controller.current?.isLoadingMessages).toBe(false);
    expect(controller.current?.messages.length).toBeGreaterThan(0);
  });

  it("re-fetches a transcript whose first load failed", async () => {
    const { client, messagesList } = fakeClient(["c1", "c2"]);
    messagesList.mockRejectedValueOnce(new Error("Network unavailable"));
    const controller = await mount(client);

    await open(controller, "c1");
    const before = fetchesFor(messagesList, "c1");

    await open(controller, "c2");
    await open(controller, "c1");

    expect(fetchesFor(messagesList, "c1")).toBe(before + 1);
    expect(controller.current?.isLoadingMessages).toBe(false);
    expect(controller.current?.messages.length).toBeGreaterThan(0);
  });
});
