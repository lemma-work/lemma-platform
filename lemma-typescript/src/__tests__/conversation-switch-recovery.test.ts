/**
 * What happens to a running conversation you walk away from.
 *
 * Switching conversations aborts the SSE stream the open one was being written
 * by — correctly, since there is one session and one stream. The run does not
 * stop, though: it keeps writing to the database the whole time you are
 * somewhere else. So coming back is a recovery, and it needs two things that
 * opening a conversation for the first time gets for free — the transcript
 * re-listed, and the stream reattached if the run is still going.
 *
 * It got neither. The controller does its own loading (`autoLoad: false` on the
 * session), and both of its open paths skip the whole load-and-resume block
 * when `loadedConversationIds` says the transcript is already held — which,
 * thanks to retention, is always true of the conversation you were just in.
 * The turn froze at the moment you looked away and stayed frozen.
 */
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
    created_at: "2026-08-24T12:00:00.000Z",
    updated_at: "2026-08-24T12:00:00.000Z",
  } as Conversation;
}

/** A stream the test pushes frames into, one at a time. */
function pushableStream() {
  const encoder = new TextEncoder();
  let controller!: ReadableStreamDefaultController<Uint8Array>;
  const stream = new ReadableStream<Uint8Array>({
    start(c) {
      controller = c;
    },
  });
  return {
    stream,
    push(frame: unknown) {
      controller.enqueue(encoder.encode(`data: ${JSON.stringify(frame)}\n\n`));
    },
    close() {
      controller.close();
    },
  };
}

const T0 = Date.now();

function messageFrame(
  id: string,
  role: string,
  text: string,
  offsetMs: number,
  conversationId = "c1",
) {
  return {
    type: "message",
    data: {
      id,
      role,
      kind: "TEXT",
      text,
      conversation_id: conversationId,
      created_at: new Date(T0 + offsetMs).toISOString(),
    },
  };
}

async function settle(rounds = 10) {
  for (let index = 0; index < rounds; index += 1) {
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 3));
    });
  }
}

/** Two conversations, each with a status the test can move and a transcript it can write to. */
function fakeServer() {
  const statuses: Record<string, string> = { c1: "WAITING", c2: "WAITING" };
  const persisted: Record<string, unknown[]> = { c1: [], c2: [] };
  const sendStreams: ReturnType<typeof pushableStream>[] = [];
  const resumeStreams: ReturnType<typeof pushableStream>[] = [];

  const sendMessageStream = vi.fn(async () => {
    const next = pushableStream();
    sendStreams.push(next);
    return next.stream;
  });
  const resumeStream = vi.fn(async () => {
    const next = pushableStream();
    resumeStreams.push(next);
    return next.stream;
  });
  const get = vi.fn(async (id: string) => conversation(id, statuses[id]));
  const messagesList = vi.fn(async (id: string) => ({
    items: [...(persisted[id] ?? [])],
    limit: 100,
    next_page_token: null,
  }));

  const client = {
    podId: "pod-1",
    withPod() {
      return this;
    },
    conversations: {
      list: vi.fn(async () => ({
        items: [conversation("c1", statuses.c1), conversation("c2", statuses.c2)],
        limit: 30,
        next_page_token: null,
        total: 2,
      })),
      get,
      create: vi.fn(async () => conversation("c1", statuses.c1)),
      listModels: vi.fn(async () => ({ items: [] })),
      messages: { list: messagesList },
      sendMessageStream,
      retryFailedRun: vi.fn(),
      resumeStream,
      stopRun: vi.fn(),
      update: vi.fn(),
      approvals: { resolve: vi.fn() },
    },
  } as unknown as LemmaClient;

  return {
    client,
    statuses,
    persisted,
    sendStreams,
    resumeStreams,
    sendMessageStream,
    resumeStream,
    get,
    messagesList,
  };
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

function transcript(controller: { current: UseAssistantControllerResult | null }): string[] {
  return (controller.current?.messages ?? [])
    .map((message) => message.content)
    .filter((content): content is string => !!content && content.trim().length > 0);
}

/** Open c1, send a turn, and leave the run going. */
async function startARun(server: ReturnType<typeof fakeServer>) {
  const controller = await mount(server.client);
  await act(async () => controller.current?.openConversation("c1"));
  await settle();
  await act(async () => {
    void controller.current?.sendMessage("do the long thing");
  });
  await settle();
  server.statuses.c1 = "RUNNING";
  server.sendStreams[0].push(messageFrame("m-user", "user", "do the long thing", 0));
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

describe("coming back to a conversation you left mid-turn", () => {
  it("reattaches to the run that is still going", async () => {
    const server = fakeServer();
    const controller = await startARun(server);
    for (const token of ["Working ", "on ", "it"]) {
      server.sendStreams[0].push({ type: "token", kind: "text", data: token });
    }
    await settle();
    expect(transcript(controller)).toContain("Working on it");

    await act(async () => controller.current?.openConversation("c2"));
    await settle();
    await act(async () => controller.current?.openConversation("c1"));
    await settle(20);

    expect(server.resumeStream).toHaveBeenCalled();
    // And the reader can see that something is still happening.
    expect(controller.current?.isLoading).toBe(true);
  });

  it("shows the answer that landed while you were away", async () => {
    const server = fakeServer();
    const controller = await startARun(server);

    await act(async () => controller.current?.openConversation("c2"));
    await settle();

    // The run finishes somewhere else. Its answer is a row in the database and
    // its realtime frame went to a stream nobody is holding any more.
    server.persisted.c1.push(messageFrame("m-answer", "assistant", "here is the answer", 5000).data);
    server.statuses.c1 = "WAITING";

    await act(async () => controller.current?.openConversation("c1"));
    await settle(20);

    expect(transcript(controller)).toContain("here is the answer");
  });

  it("does not leave the rejoined fragment sitting under the finished answer", async () => {
    // A resumed stream starts at the token after it attached, so the buffer it
    // fills holds the *end* of the answer. The bridge that retires that buffer
    // when its durable message lands used to match by prefix, which is the
    // shape of a buffer filled from the run's first token and nothing else —
    // so on this path it never matched, and the tail stayed on screen as a
    // second, shorter copy of the reply.
    const server = fakeServer();
    const controller = await startARun(server);
    for (const token of ["The ", "report "]) {
      server.sendStreams[0].push({ type: "token", kind: "text", data: token });
    }
    await settle();

    await act(async () => controller.current?.openConversation("c2"));
    await settle();
    await act(async () => controller.current?.openConversation("c1"));
    await settle(20);

    const resumed = server.resumeStreams[server.resumeStreams.length - 1];
    for (const token of ["is ", "ready."]) {
      resumed.push({ type: "token", kind: "text", data: token });
    }
    await settle();
    expect(transcript(controller)).toContain("is ready.");

    resumed.push(messageFrame("m-answer", "assistant", "The report is ready.", 5000));
    server.statuses.c1 = "WAITING";
    resumed.push({
      type: "completed",
      data: { conversation_id: "c1", status: "COMPLETED", conversation_status: "WAITING" },
    });
    resumed.close();
    await settle(15);

    expect(transcript(controller)).toContain("The report is ready.");
    expect(transcript(controller)).not.toContain("is ready.");
  });

  it("re-selecting the open conversation does not kill its stream", async () => {
    const server = fakeServer();
    const controller = await startARun(server);
    for (const token of ["still ", "going"]) {
      server.sendStreams[0].push({ type: "token", kind: "text", data: token });
    }
    await settle();

    await act(async () => controller.current?.openConversation("c1"));
    await settle(10);

    // The same stream is still the one delivering, with no reconnect in between.
    expect(server.resumeStream).not.toHaveBeenCalled();
    server.sendStreams[0].push({ type: "token", kind: "text", data: " strong" });
    await settle();
    expect(transcript(controller)).toContain("still going strong");
  });
});

describe("a conversation you left alone", () => {
  it("is still re-opened without a refetch", async () => {
    // The point of retention. Only a conversation whose run was live when we
    // walked away has to pay for a catch-up.
    const server = fakeServer();
    const controller = await mount(server.client);
    await act(async () => controller.current?.openConversation("c1"));
    await settle();

    await act(async () => {
      void controller.current?.sendMessage("quick question");
    });
    await settle();
    server.sendStreams[0].push(messageFrame("m-user", "user", "quick question", 0));
    server.sendStreams[0].push(messageFrame("m-answer", "assistant", "quick answer", 1000));
    server.sendStreams[0].push({
      type: "completed",
      data: { conversation_id: "c1", status: "COMPLETED", conversation_status: "WAITING" },
    });
    server.sendStreams[0].close();
    await settle();

    const listsBefore = server.messagesList.mock.calls.length;
    await act(async () => controller.current?.openConversation("c2"));
    await settle();
    await act(async () => controller.current?.openConversation("c1"));
    await settle(15);

    // c2 was listed once on its way in; c1 came back out of the store.
    expect(server.messagesList.mock.calls.length).toBe(listsBefore + 1);
    expect(server.resumeStream).not.toHaveBeenCalled();
    expect(transcript(controller)).toContain("quick answer");
  });
});
