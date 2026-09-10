/**
 * A message typed at an Agent Host turn, which cannot be told anything.
 *
 * ACP has no steering primitive: `session/prompt` is one request that returns
 * when the turn ends, and as of 2.1.0 there is no method to add input to one in
 * flight. The in-process LEMMA harness is different — a capability claims the
 * message per node — so the two kinds cannot behave the same, and pretending
 * they did is the bug this covers. The message used to be persisted at once and
 * answered by a follow-up run later, which put it in the transcript looking
 * delivered while the agent could not see it.
 *
 * @vitest-environment jsdom
 */
import { act, createElement } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { LemmaClient } from "../client.js";
import type { Conversation } from "../types.js";
import { useAssistantController, type UseAssistantControllerResult } from "../react/index.js";
import { forgetQueuedSteersInMemory } from "../react/queued-steers.js";

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const roots: Root[] = [];
const AGENT_HOST_MODEL = "claude-code";

function conversation(status: string, model: string): Conversation {
  return {
    id: "c1",
    pod_id: "pod-1",
    title: "c1",
    status,
    model,
    pod_cwd: "/me/c/2026-08-30/ab12cd34",
    created_at: "2026-08-30T12:00:00.000Z",
    updated_at: "2026-08-30T12:00:00.000Z",
  } as Conversation;
}

function fakeClient(options: { status?: string; harnessKind?: string } = {}) {
  const current = conversation(options.status ?? "RUNNING", AGENT_HOST_MODEL);
  const appendMessage = vi.fn(async () => ({
    conversation_id: "c1",
    agent_run_id: "run-1",
    started_new_run: false,
  }));
  const stopRun = vi.fn(async () => ({}));
  const client = {
    podId: "pod-1",
    withPod() {
      return this;
    },
    conversations: {
      list: vi.fn(async () => ({ items: [current], limit: 30, next_page_token: null })),
      get: vi.fn(async () => current),
      create: vi.fn(async () => current),
      // Which harness answers is a property of the model, not the conversation.
      listModels: vi.fn(async () => ({
        items: [{ id: AGENT_HOST_MODEL, harness_kind: options.harnessKind ?? "HARNESS" }],
      })),
      messages: { list: vi.fn(async () => ({ items: [], limit: 100, next_page_token: null })) },
      sendMessageStream: vi.fn(async () => new ReadableStream<Uint8Array>({ start() {} })),
      appendMessage,
      retryFailedRun: vi.fn(),
      resumeStream: vi.fn(async () => new ReadableStream<Uint8Array>({ start() {} })),
      stopRun,
      update: vi.fn(),
    },
    files: { upload: vi.fn(), folder: { create: vi.fn() } },
  } as unknown as LemmaClient;
  return { client, appendMessage, stopRun };
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
  await act(async () => {
    controller.current?.selectConversation("c1");
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
  vi.restoreAllMocks();
  window.localStorage.clear();
  // Module-level, so it outlives a test unless it is cleared here.
  forgetQueuedSteersInMemory();
});

describe("a steer aimed at an Agent Host turn", () => {
  it("is queued and named as queued, not sent into a turn that cannot see it", async () => {
    const { client, appendMessage } = fakeClient();
    const controller = await mount(client);

    await act(async () => {
      await controller.current?.steerMessage("also check the invoices");
    });
    await settle();

    expect(appendMessage).not.toHaveBeenCalled();
    expect(controller.current?.queuedSteers.map((item) => item.content)).toEqual([
      "also check the invoices",
    ]);
  });

  it("is still there after the page goes away and comes back", async () => {
    const first = fakeClient();
    const firstController = await mount(first.client);
    await act(async () => {
      await firstController.current?.steerMessage("one more thing");
    });
    await settle();

    // The remount is the point: React state is gone, and a queued message a
    // navigation silently discarded would be the same broken promise.
    const second = fakeClient();
    const secondController = await mount(second.client);

    expect(secondController.current?.queuedSteers.map((item) => item.content)).toEqual([
      "one more thing",
    ]);
    expect(second.appendMessage).not.toHaveBeenCalled();
  });

  it("interrupts the turn when asked to send now", async () => {
    const { client, stopRun, appendMessage } = fakeClient();
    const controller = await mount(client);
    await act(async () => {
      await controller.current?.steerMessage("stop and do this instead");
    });
    await settle();

    await act(async () => {
      await controller.current?.sendQueuedSteersNow();
    });
    await settle();

    expect(stopRun).toHaveBeenCalled();
    // Not sent yet: `STOP_REQUESTED` still counts as running, so the queue waits
    // for the stop to land rather than racing the turn it just cancelled.
    expect(appendMessage).not.toHaveBeenCalled();
    expect(controller.current?.queuedSteers).toHaveLength(1);
  });

  it("goes straight out when no turn is actually running", async () => {
    const { client, appendMessage, stopRun } = fakeClient({ status: "WAITING" });
    const controller = await mount(client);
    await act(async () => {
      await controller.current?.steerMessage("queued while it looked busy");
    });
    // Nothing is running, so a steer is an ordinary send and never queues.
    await settle();
    expect(appendMessage).toHaveBeenCalledTimes(1);
    expect(stopRun).not.toHaveBeenCalled();
    expect(controller.current?.queuedSteers).toEqual([]);
  });

  it("keeps what did not go out when a send fails part way through", async () => {
    // Clearing the whole queue before the first request was simpler and lost
    // more: one failure took every message behind it out of both state and
    // storage, with nothing left to retype from.
    window.localStorage.setItem(
      "lemma.queued-steers.c1",
      JSON.stringify([
        { id: "one", content: "first", queuedAt: "2026-09-10T00:00:00Z" },
        { id: "two", content: "second", queuedAt: "2026-09-10T00:00:01Z" },
        { id: "three", content: "third", queuedAt: "2026-09-10T00:00:02Z" },
      ]),
    );
    forgetQueuedSteersInMemory();

    const { client, appendMessage } = fakeClient({ status: "WAITING" });
    let calls = 0;
    (appendMessage as unknown as { mockImplementation: (fn: () => Promise<unknown>) => void })
      .mockImplementation(async () => {
        calls += 1;
        if (calls === 2) throw new Error("the second one failed");
        return { conversation_id: "c1", agent_run_id: "run-1", started_new_run: false };
      });
    const controller = await mount(client);

    await act(async () => {
      await controller.current?.sendQueuedSteersNow().catch(() => {});
    });
    await settle();

    // The one that went out is gone; the one that failed and the one behind it
    // are still queued, and still on screen.
    expect(controller.current?.queuedSteers.map((item) => item.content)).toEqual([
      "second",
      "third",
    ]);
  });

  it("drops one on request without sending it", async () => {
    const { client, appendMessage } = fakeClient();
    const controller = await mount(client);
    await act(async () => {
      await controller.current?.steerMessage("never mind");
    });
    await settle();
    const queued = controller.current?.queuedSteers[0];

    await act(async () => {
      controller.current?.discardQueuedSteer(queued?.id ?? "");
    });
    await settle();

    expect(controller.current?.queuedSteers).toEqual([]);
    expect(appendMessage).not.toHaveBeenCalled();
    // And it does not come back on the next mount.
    const next = await mount(fakeClient().client);
    expect(next.current?.queuedSteers).toEqual([]);
  });

  it("leaves an in-process run steering exactly as it did", async () => {
    // The LEMMA harness claims a mid-run message per node, so there is nothing
    // to queue and queuing it would delay an answer that used to be immediate.
    const { client, appendMessage } = fakeClient({ harnessKind: "LEMMA" });
    const controller = await mount(client);

    await act(async () => {
      await controller.current?.steerMessage("also check the invoices");
    });
    await settle();

    expect(appendMessage).toHaveBeenCalledTimes(1);
    expect(controller.current?.queuedSteers).toEqual([]);
  });
});
