/**
 * What happens to a transcript when the realtime stream lets it down.
 *
 * The web chat has one source of truth on screen: the SSE stream a send opens.
 * There is no poller behind it, so anything that ends that stream while the
 * conversation is still being written to leaves a transcript that stops moving
 * until the page is reloaded — with every message sitting in the database the
 * whole time, which is what made it look like a rendering bug rather than a
 * delivery one.
 *
 * Three ways that happened, one test each, plus the guard that the healthy path
 * did not pick up any extra requests to pay for them.
 */
import { act, createElement } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { LemmaClient } from "../client.js";
import type { Conversation } from "../types.js";
import {
  useAssistantController,
  useAssistantSession,
  type UseAssistantControllerResult,
  type UseAssistantSessionResult,
} from "../react/index.js";

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

const T0 = Date.parse("2026-08-24T12:00:00.000Z");

function messageFrame(
  id: string,
  role: string,
  text: string,
  offsetMs: number,
  extra: Record<string, unknown> = {},
) {
  return {
    type: "message",
    data: {
      id,
      role,
      kind: "TEXT",
      text,
      conversation_id: "c1",
      created_at: new Date(T0 + offsetMs).toISOString(),
      ...extra,
    },
  };
}

async function settle(rounds = 8) {
  for (let index = 0; index < rounds; index += 1) {
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 2));
    });
  }
}

/**
 * Pump until the thing we are waiting for happens, rather than for a fixed
 * stretch of wall clock. Both recovery paths are on real timers — a reconnect
 * backoff, a resume ladder — and a fixed sleep long enough on an idle machine
 * is a coin toss on a loaded one.
 */
async function waitUntil(
  condition: () => boolean,
  { timeoutMs = 15_000, label = "condition" }: { timeoutMs?: number; label?: string } = {},
) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (condition()) return;
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 25));
    });
  }
  throw new Error(`Timed out waiting for ${label}`);
}

/** For the negative cases: let the whole ladder run out, then look. */
async function waitMs(ms: number) {
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, ms));
  });
  await settle();
}

/**
 * A server whose conversation status the test can move, whose transcript it can
 * write to directly (that is the database, which a reload reads), and whose
 * streams it holds open.
 */
function fakeServer(options: { status: string; persisted?: unknown[] }) {
  const state = { status: options.status };
  const persisted = options.persisted ?? [];
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
  const get = vi.fn(async (id: string) => conversation(id, state.status));
  const messagesList = vi.fn(async () => ({
    items: [...persisted],
    limit: 100,
    next_page_token: null,
  }));
  const resolveApproval = vi.fn(async () => ({
    approval_id: "a1",
    decision: "APPROVE",
    // What an approved `request_approval` answers: the decision is committed,
    // the run that resumes from it is a job somebody else has to pick up.
    status: "queued",
  }));

  const client = {
    podId: "pod-1",
    withPod() {
      return this;
    },
    conversations: {
      list: vi.fn(async () => ({
        items: [conversation("c1", state.status)],
        limit: 30,
        next_page_token: null,
        total: 1,
      })),
      get,
      create: vi.fn(async () => conversation("c1", state.status)),
      listModels: vi.fn(async () => ({ items: [] })),
      messages: { list: messagesList },
      sendMessageStream,
      retryFailedRun: vi.fn(),
      resumeStream,
      stopRun: vi.fn(),
      update: vi.fn(),
      approvals: { resolve: resolveApproval },
    },
  } as unknown as LemmaClient;

  return {
    client,
    state,
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

afterEach(async () => {
  while (roots.length > 0) {
    const root = roots.pop();
    if (!root) continue;
    await act(async () => root.unmount());
  }
  document.body.innerHTML = "";
});

describe("a stream the transport gives up on", () => {
  it("keeps reading the current stream after Stop until its final answer and acknowledgement arrive", async () => {
    const server = fakeServer({ status: "WAITING" });
    const stopRun = vi.spyOn(server.client.conversations, "stopRun").mockImplementation(async () => {
      server.state.status = "STOP_REQUESTED";
      return conversation("c1", server.state.status);
    });
    const controller = await mount(server.client);
    await act(async () => controller.current?.openConversation("c1"));
    await act(async () => { void controller.current?.sendMessage("start"); });
    await waitUntil(() => server.sendStreams.length === 1);
    server.state.status = "RUNNING";
    server.sendStreams[0].push({ type: "token", kind: "text", data: "Partial work" });
    await waitUntil(() => transcript(controller).includes("Partial work"));
    await act(async () => controller.current?.stop());
    expect(stopRun).toHaveBeenCalledTimes(1);
    expect(transcript(controller)).toContain("Partial work");
    const answer = messageFrame("m-answer", "assistant", "Partial work, then stopped.", 1);
    server.persisted.push(answer.data);
    server.state.status = "STOPPED";
    server.sendStreams[0].push(answer);
    server.sendStreams[0].push({ type: "completed", data: { status: "STOPPED" } });
    server.sendStreams[0].close();
    await waitUntil(() => transcript(controller).includes("Partial work, then stopped."));
    expect(server.resumeStream).not.toHaveBeenCalled();
  });

  it("reopens a stopping run and receives its final transcript without starting another run", async () => {
    const server = fakeServer({ status: "STOP_REQUESTED" });
    const controller = await mount(server.client);
    await act(async () => controller.current?.openConversation("c1"));
    await waitUntil(() => server.resumeStreams.length === 1, { label: "attach to the stopping run" });
    const answer = messageFrame("m-answer", "assistant", "Work preserved after Stop.", 1);
    server.persisted.push(answer.data);
    server.state.status = "STOPPED";
    server.resumeStreams[0].push(answer);
    server.resumeStreams[0].push({ type: "completed", data: { status: "STOPPED" } });
    server.resumeStreams[0].close();
    await waitUntil(() => transcript(controller).includes("Work preserved after Stop."));
    expect(server.sendMessageStream).not.toHaveBeenCalled();
    expect(server.resumeStream).toHaveBeenCalledTimes(1);
  });

  it("reconnects, because `stream_error` is not the run failing", async () => {
    const server = fakeServer({ status: "WAITING" });
    const controller = await mount(server.client);
    await act(async () => controller.current?.openConversation("c1"));
    await settle();

    await act(async () => {
      void controller.current?.sendMessage("do the long thing");
    });
    await settle();
    server.state.status = "RUNNING";

    server.sendStreams[0].push(messageFrame("m-user", "user", "do the long thing", 0));
    await settle();

    // The subscription behind the stream died. The run has not.
    server.sendStreams[0].push({
      type: "stream_error",
      data: "Realtime stream interrupted. Reconnect to continue.",
    });
    server.sendStreams[0].close();
    await waitUntil(() => server.resumeStream.mock.calls.length > 0, {
      label: "the client to reconnect",
    });

    expect(server.resumeStream).toHaveBeenCalledTimes(1);
    // And nothing was reported to the reader as a failure: the run is fine.
    expect(controller.current?.error).toBeFalsy();
  });

  it("still stops on a real `error`, which is the run failing", async () => {
    const server = fakeServer({ status: "WAITING" });
    const controller = await mount(server.client);
    await act(async () => controller.current?.openConversation("c1"));
    await settle();

    await act(async () => {
      void controller.current?.sendMessage("do the impossible thing");
    });
    await settle();
    server.state.status = "FAILED";

    server.sendStreams[0].push({ type: "error", data: "the agent fell over" });
    server.sendStreams[0].close();
    await waitMs(1400);

    expect(server.resumeStream).not.toHaveBeenCalled();
    expect(controller.current?.error).toBeTruthy();
  });
});

describe("an answer whose durable frame never arrives", () => {
  it("keeps the streamed words on screen and lists the transcript once", async () => {
    // The row is in the database. Only its realtime frame went missing —
    // publishing is best-effort, and the fan-out drops a slow subscriber.
    const server = fakeServer({ status: "WAITING" });
    const controller = await mount(server.client);
    await act(async () => controller.current?.openConversation("c1"));
    await settle();
    const listsAfterOpen = server.messagesList.mock.calls.length;

    await act(async () => {
      void controller.current?.sendMessage("write me the report");
    });
    await settle();
    server.state.status = "RUNNING";

    // Quiet tool work, then the one thing the reader is waiting for.
    server.sendStreams[0].push(messageFrame("m-tool", "assistant", "", 1000, {
      kind: "TOOL_CALL",
      tool_name: "pod_write_file",
      tool_call_id: "tc-1",
      tool_args: {},
    }));
    await settle();
    for (const token of ["The ", "report ", "is ", "ready."]) {
      server.sendStreams[0].push({ type: "token", kind: "text", data: token });
    }
    await settle();
    expect(transcript(controller)).toContain("The report is ready.");

    // Its message frame never comes, but the row exists.
    server.persisted.push(messageFrame("m-answer", "assistant", "The report is ready.", 3000).data);
    server.state.status = "WAITING";
    server.sendStreams[0].push({
      type: "completed",
      data: { conversation_id: "c1", status: "COMPLETED", conversation_status: "WAITING" },
    });
    server.sendStreams[0].close();
    await settle();

    // The answer never blinked out, and the store now really holds it.
    expect(transcript(controller)).toContain("The report is ready.");
    expect(server.messagesList.mock.calls.length).toBe(listsAfterOpen + 1);
  });
});

describe("answering a question the agent asked", () => {
  it("waits for the queued resume run instead of reading once and giving up", async () => {
    const server = fakeServer({ status: "WAITING" });
    const controller = await mount(server.client);
    await act(async () => controller.current?.openConversation("c1"));
    await settle();

    await act(async () => {
      await controller.current?.resolveUserApproval?.("a1", "APPROVE" as never);
    });
    await settle();
    // Still WAITING at this point: the worker has not picked the job up.
    expect(server.resumeStream).not.toHaveBeenCalled();

    server.state.status = "RUNNING";
    await waitUntil(() => server.resumeStream.mock.calls.length > 0, {
      label: "the client to attach to the resumed run",
    });

    expect(server.resumeStream).toHaveBeenCalledTimes(1);
  });

  it("keeps asking, without ever attaching to a conversation that is not running", async () => {
    // The ladder outlasts the approved tool now, so "it stopped asking" is no
    // longer the thing to assert — the conversation staying WAITING is. What
    // must hold for as long as it asks is that it never opens a stream against
    // a run that does not exist.
    const server = fakeServer({ status: "WAITING" });
    const controller = await mount(server.client);
    await act(async () => controller.current?.openConversation("c1"));
    await settle();
    const readsBeforeApproval = server.get.mock.calls.length;

    await act(async () => {
      await controller.current?.resolveUserApproval?.("a1", "APPROVE" as never);
    });
    await waitMs(4000);

    expect(server.resumeStream).not.toHaveBeenCalled();
    expect(server.get.mock.calls.length).toBeGreaterThan(readsBeforeApproval);
  });

  it("still attaches when the approved tool outlives the old three-second ladder", async () => {
    // The worker runs the approved tool *before* the resume run starts, and
    // deferring it to a worker is an admission that it can take minutes. A
    // ladder that ran out first left the answer streaming into nothing.
    const server = fakeServer({ status: "WAITING" });
    const controller = await mount(server.client);
    await act(async () => controller.current?.openConversation("c1"));
    await settle();

    await act(async () => {
      await controller.current?.resolveUserApproval?.("a1", "APPROVE" as never);
    });
    // Comfortably past the 3.3s the ladder used to cover.
    await waitMs(5000);
    expect(server.resumeStream).not.toHaveBeenCalled();

    // The tool finishes: its return is written, then the run starts.
    server.persisted.push(messageFrame("m-return", "tool", "", 2000, {
      kind: "TOOL_RETURN", tool_call_id: "a1", tool_name: "request_approval",
      tool_result: { decision: "APPROVE" },
    }).data);
    server.state.status = "RUNNING";

    await waitUntil(() => server.resumeStream.mock.calls.length > 0, {
      label: "the client to attach to the run the worker finally started",
    });
    // ...and the return the worker wrote while nobody was subscribed is read
    // back, since the stream it just opened carries the new run, not that row.
    expect(
      (controller.current?.messages ?? []).some((message) => (
        (message.toolInvocations ?? []).some((invocation) => (
          invocation.toolCallId === "a1"
          && (invocation.state === "result" || !!invocation.result?.decision)
        ))
      )),
    ).toBe(true);
  }, 30_000);
});

describe("the wait a click has to sit through", () => {
  it("does not hold the click open for the length of a transcript read", async () => {
    // The decision POST is small. Listing the transcript is the heaviest read
    // in the chat, and running them in series is what made an approval that
    // the server had already accepted still look like it was being sent.
    const server = fakeServer({ status: "WAITING" });
    server.messagesList.mockImplementation(async () => {
      await new Promise((resolve) => setTimeout(resolve, 700));
      return { items: [...server.persisted], limit: 100, next_page_token: null };
    });
    // An inline resolve — the shape that does still want the read, because it
    // appended the tool return before answering.
    server.client.conversations.approvals.resolve = vi.fn(async () => ({
      approval_id: "a1", decision: "APPROVE", status: "resolved",
    })) as never;

    const controller = await mount(server.client);
    await act(async () => controller.current?.openConversation("c1"));
    await settle();

    const start = Date.now();
    await act(async () => {
      await controller.current?.resolveUserApproval?.("a1", "APPROVE" as never);
    });
    const heldFor = Date.now() - start;

    expect(heldFor).toBeLessThan(300);
    // The read still happens; it just is not what the button waits on.
    await waitUntil(() => server.messagesList.mock.calls.length > 1, {
      label: "the transcript to catch up on its own",
    });
  }, 30_000);

  it("wakes a stream sitting in reconnect backoff instead of waiting it out", async () => {
    // `isStreaming` is true for a stream that is alive AND for one asleep
    // between reconnect attempts, so the forced resume declined in both cases —
    // and in the second there was nothing carrying the run to decline in favour
    // of. The click now shortens the sleep rather than duplicating the stream.
    const server = fakeServer({ status: "WAITING" });
    const controller = await mount(server.client);
    await act(async () => controller.current?.openConversation("c1"));
    await settle();

    await act(async () => { void controller.current?.sendMessage("do the long thing"); });
    await settle();
    server.state.status = "RUNNING";

    // The transport dies mid-run, and every reconnect attempt fails, so the
    // backoff climbs: 1s, 2s, 4s...
    server.resumeStream.mockRejectedValue(new Error("stream cut"));
    server.sendStreams[0].close();
    await waitMs(3500);
    const attemptsBeforeApproval = server.resumeStream.mock.calls.length;
    expect(attemptsBeforeApproval).toBeGreaterThanOrEqual(2);

    await act(async () => {
      await controller.current?.resolveUserApproval?.("a1", "APPROVE" as never);
    });
    // Inside the 4s sleep that would otherwise have run to ~7s.
    await waitMs(600);

    expect(server.resumeStream.mock.calls.length).toBeGreaterThan(attemptsBeforeApproval);
  }, 30_000);
});

describe("the healthy path", () => {
  it("costs the same as before: one stream per turn, nothing read back", async () => {
    const server = fakeServer({ status: "WAITING" });
    const controller = await mount(server.client);
    await act(async () => controller.current?.openConversation("c1"));
    await settle();

    // Opening reads the record and its transcript, once each.
    const readsAfterOpen = server.get.mock.calls.length;
    const listsAfterOpen = server.messagesList.mock.calls.length;

    for (const [index, text] of ["first", "second"].entries()) {
      await act(async () => {
        void controller.current?.sendMessage(text);
      });
      await settle();
      const stream = server.sendStreams[index];
      stream.push(messageFrame(`m-user-${index}`, "user", text, index * 10_000));
      stream.push(messageFrame(`m-asst-${index}`, "assistant", `answer ${index}`, index * 10_000 + 1000));
      await settle();
      stream.push({
        type: "completed",
        data: { conversation_id: "c1", status: "COMPLETED", conversation_status: "WAITING" },
      });
      stream.close();
      await settle();
    }

    expect(server.sendMessageStream).toHaveBeenCalledTimes(2);
    // A turn that delivered its own messages asks the server for nothing.
    expect(server.get.mock.calls.length).toBe(readsAfterOpen);
    expect(server.messagesList.mock.calls.length).toBe(listsAfterOpen);
    expect(transcript(controller)).toEqual(
      expect.arrayContaining(["answer 0", "answer 1"]),
    );
  });

  it("re-reading a conversation that has not moved does not re-render", async () => {
    // What keeps the recovery paths cheap. A run whose ending is in doubt is
    // now re-read — by the reconnect loop, and once per attempt while waiting
    // for a queued resume — and nearly every one of those reads comes back
    // holding exactly what was already on screen. Everything downstream of the
    // record renders on its identity, so handing back a new object for the same
    // record is a whole transcript re-rendering for nothing.
    const server = fakeServer({ status: "WAITING" });
    let renders = 0;
    const session = { current: null as UseAssistantSessionResult | null };
    function Harness() {
      renders += 1;
      session.current = useAssistantSession({
        client: server.client,
        conversationId: "c1",
        podId: "pod-1",
        autoLoad: false,
      });
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

    // Two warm-up reads: the first record landing is a real change, and the
    // effects that settle behind it are one-time.
    for (let warmUp = 0; warmUp < 2; warmUp += 1) {
      await act(async () => {
        await session.current?.refreshConversation("c1");
      });
      await settle();
    }
    const settledRenders = renders;
    const settledRecord = session.current?.conversation;

    // Now the steady state: three more reads of a conversation that has not
    // moved, which is what the resume ladder does while it waits.
    for (let attempt = 0; attempt < 3; attempt += 1) {
      await act(async () => {
        await session.current?.refreshConversation("c1");
      });
      await settle();
    }

    expect(server.get.mock.calls.length).toBeGreaterThanOrEqual(5);
    expect(renders).toBe(settledRenders);
    expect(session.current?.conversation).toBe(settledRecord);

    // A record that did move still lands, or the transcript would go stale.
    server.state.status = "RUNNING";
    await act(async () => {
      await session.current?.refreshConversation("c1");
    });
    await settle();
    expect(session.current?.conversation?.status).toBe("RUNNING");
  });
});
