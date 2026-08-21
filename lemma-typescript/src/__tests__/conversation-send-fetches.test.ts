import { act, createElement, useEffect } from "react";
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

function sse(lines: string[]) {
  const encoder = new TextEncoder();
  return new ReadableStream<Uint8Array>({
    start(controller) {
      for (const line of lines) controller.enqueue(encoder.encode(`data: ${line}\n\n`));
      controller.close();
    },
  });
}

function cleanTurn(conversationId: string, atMs = Date.now()) {
  return [
    JSON.stringify({ type: "token", kind: "text", data: "hi " }),
    JSON.stringify({
      type: "message",
      data: {
        id: `m-user-${conversationId}`,
        role: "user",
        kind: "TEXT",
        text: "hello",
        conversation_id: conversationId,
        created_at: new Date(atMs).toISOString(),
      },
    }),
    JSON.stringify({
      type: "message",
      data: {
        id: `m-asst-${conversationId}`,
        role: "assistant",
        kind: "TEXT",
        text: "hi there",
        conversation_id: conversationId,
        created_at: new Date(atMs + 1000).toISOString(),
      },
    }),
    JSON.stringify({ type: "completed", data: { conversation_status: "WAITING" } }),
  ];
}

const CLEAN_TURN = cleanTurn("c1");

const FAILED_TURN = [
  JSON.stringify({ type: "error", data: { message: "the agent fell over" } }),
];

function fakeClient(items: Conversation[], streamLines: string[] = CLEAN_TURN) {
  const list = vi.fn(async () => ({ items, limit: 30, next_page_token: null, total: items.length }));
  const get = vi.fn(async (id: string) => items.find((item) => item.id === id) ?? conversation(id, "WAITING"));
  const create = vi.fn(async () => conversation("c-new", "WAITING"));
  const messagesList = vi.fn(async () => ({ items: [], limit: 100, next_page_token: null }));
  const sendMessageStream = vi.fn(async () => sse([...streamLines]));

  const client = {
    podId: "pod-1",
    withPod() {
      return this;
    },
    conversations: {
      list,
      get,
      create,
      listModels: vi.fn(async () => ({ items: [] })),
      messages: { list: messagesList },
      sendMessageStream,
      retryFailedRun: vi.fn(),
      resumeStream: vi.fn(),
      stopRun: vi.fn(),
      update: vi.fn(),
    },
  } as unknown as LemmaClient;

  return { client, list, get, create, messagesList, sendMessageStream };
}

async function settle() {
  for (let index = 0; index < 6; index += 1) {
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 1));
    });
  }
}

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
  });
  await settle();
  return controller;
}

afterEach(async () => {
  while (roots.length > 0) {
    const root = roots.pop();
    if (!root) continue;
    await act(async () => root.unmount());
  }
  document.body.innerHTML = "";
});

describe("sending a message", () => {
  it("creates the conversation and streams, and reads neither back", async () => {
    const { client, get, create, messagesList, sendMessageStream } = fakeClient([]);
    const controller = await mount(client);

    await act(async () => {
      await controller.current?.sendMessage("hello");
    });
    await settle();

    expect(create).toHaveBeenCalledOnce();
    expect(sendMessageStream).toHaveBeenCalledOnce();
    // The create returned the record and the stream delivered the transcript.
    // Reading either back is asking the server to repeat itself.
    expect(get).not.toHaveBeenCalled();
    expect(messagesList).not.toHaveBeenCalled();
  });

  it("costs one request per turn in a conversation already open", async () => {
    const { client, get, messagesList, sendMessageStream } = fakeClient([conversation("c1", "WAITING")]);
    const controller = await mount(client);

    await act(async () => controller.current?.openConversation("c1"));
    await settle();

    // Opening reads the conversation and its transcript, once each.
    expect(get).toHaveBeenCalledOnce();
    expect(messagesList).toHaveBeenCalledOnce();

    await act(async () => {
      await controller.current?.sendMessage("first");
    });
    await settle();
    await act(async () => {
      await controller.current?.sendMessage("second");
    });
    await settle();

    expect(sendMessageStream).toHaveBeenCalledTimes(2);
    // Neither send re-read anything: the session takes the record the
    // controller is already holding rather than fetching its own copy.
    expect(get).toHaveBeenCalledOnce();
    expect(messagesList).toHaveBeenCalledOnce();
  });

  it("re-reads the conversation after a failed run, for the retry affordance", async () => {
    const { client, get, messagesList } = fakeClient([conversation("c1", "WAITING")], FAILED_TURN);
    const controller = await mount(client);

    await act(async () => controller.current?.openConversation("c1"));
    await settle();
    const readsAfterOpen = get.mock.calls.length;
    const listsAfterOpen = messagesList.mock.calls.length;

    await act(async () => {
      await controller.current?.sendMessage("this one fails");
    });
    await settle();

    // `last_run_error` and `last_run_retryable` live only on the conversation,
    // so a failure is the one ending that still has to ask for it.
    expect(get.mock.calls.length).toBe(readsAfterOpen + 1);
    // The transcript is still not re-listed — the stream is not what failed.
    expect(messagesList.mock.calls.length).toBe(listsAfterOpen);
  });
});

describe("the turn you just sent", () => {
  it("is on screen before the conversation exists", async () => {
    let releaseCreate: (() => void) | null = null;
    const createGate = new Promise<void>((resolve) => {
      releaseCreate = resolve;
    });
    const { client } = fakeClient([]);
    (client.conversations.create as unknown as ReturnType<typeof vi.fn>).mockImplementation(async () => {
      await createGate;
      return conversation("c-new", "WAITING");
    });

    const controller = await mount(client);
    let sent: Promise<void> | undefined;
    await act(async () => {
      sent = controller.current?.sendMessage("show me immediately");
      await Promise.resolve();
    });

    // Mid-create: no conversation id yet, and the turn is already rendered.
    expect(controller.current?.activeConversationId).toBeNull();
    expect(controller.current?.messages.map((message) => message.content)).toEqual(["show me immediately"]);

    releaseCreate?.();
    await act(async () => {
      await sent;
    });
    await settle();

    // And it is still the same one turn once the conversation lands under it.
    const userMessages = controller.current?.messages.filter((message) => message.role === "user") ?? [];
    expect(userMessages.map((message) => message.content)).toEqual(["show me immediately"]);
  });

  it("does not follow you into the next conversation when the send fails", async () => {
    const { client } = fakeClient([conversation("c1", "WAITING")]);
    (client.conversations.create as unknown as ReturnType<typeof vi.fn>).mockRejectedValue(
      new Error("create failed"),
    );

    const controller = await mount(client);
    await act(async () => {
      await controller.current?.sendMessage("never lands");
    });
    await settle();

    await act(async () => controller.current?.openConversation("c1"));
    await settle();

    expect(controller.current?.messages).toEqual([]);
  });
});

describe("the stream a send just opened", () => {
  it("survives the conversation id reaching the session", async () => {
    const { client, sendMessageStream } = fakeClient([]);
    let releaseCreate: (() => void) | null = null;
    const createGate = new Promise<void>((resolve) => { releaseCreate = resolve; });
    (client.conversations.create as unknown as ReturnType<typeof vi.fn>).mockImplementation(async () => {
      await createGate;
      return conversation("c-new", "WAITING");
    });

    let releaseStream: (() => void) | null = null;
    const streamGate = new Promise<void>((resolve) => { releaseStream = resolve; });
    let streamSignal: AbortSignal | undefined;
    let streamOpened: (() => void) | null = null;
    const streamOpenedPromise = new Promise<void>((resolve) => { streamOpened = resolve; });
    (sendMessageStream as unknown as ReturnType<typeof vi.fn>).mockImplementation(
      async (_id: string, _body: unknown, options: { signal?: AbortSignal }) => {
        streamSignal = options?.signal;
        streamOpened?.();
        // The gap every real send has: the request is in flight, and React is
        // free to commit the state the create queued a moment ago.
        await streamGate;
        return sse(cleanTurn("c-new"));
      },
    );

    const controller = await mount(client);
    let sent: Promise<void> | undefined;
    await act(async () => {
      sent = controller.current?.sendMessage("hello");
      await Promise.resolve();
    });

    // Released outside `act`, so the send runs on to open its stream before
    // React is given a chance to commit. That is the real ordering: a create
    // takes a network round-trip, so the render it queues lands while the
    // stream request is already in flight.
    releaseCreate?.();
    await streamOpenedPromise;
    expect(streamSignal).toBeDefined();

    // Now let React commit. Pointing the session at the conversation that was
    // just created must not cancel the stream opened for it.
    await settle();
    expect(streamSignal?.aborted).toBe(false);

    releaseStream?.();
    await act(async () => { await sent; });
    await settle();

    expect(controller.current?.error).toBeNull();
    expect(controller.current?.messages.map((message) => message.role)).toEqual(["user", "assistant"]);
  });
});

describe("a conversation opened as the controller mounts", () => {
  it("stays open", async () => {
    const { client, messagesList } = fakeClient([conversation("c1", "WAITING")]);
    const controller = { current: null as UseAssistantControllerResult | null };

    // A consumer that opens a conversation from its own mount effect, which is
    // what a route does when it renders in the same commit as the provider.
    // Child effects run before the parent's, so whatever the controller resets
    // on its own first run lands *after* this — and used to undo it.
    function Consumer() {
      useEffect(() => {
        controller.current?.openConversation("c1");
        // Once, on mount: this is the route arriving, not a subscription.
        // eslint-disable-next-line react-hooks/exhaustive-deps
      }, []);
      return null;
    }

    function Harness() {
      controller.current = useAssistantController({ client, podId: "pod-1", autoLoadMessages: true });
      return createElement(Consumer);
    }

    const container = document.createElement("div");
    document.body.appendChild(container);
    const root = createRoot(container);
    roots.push(root);
    await act(async () => {
      root.render(createElement(Harness));
    });
    await settle();

    expect(controller.current?.activeConversationId).toBe("c1");
    expect(messagesList).toHaveBeenCalled();
  });
});
