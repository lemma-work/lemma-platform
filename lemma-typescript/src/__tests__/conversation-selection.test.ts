import { act, createElement } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { LemmaClient } from "../client.js";
import type { Conversation } from "../types.js";
import {
  useAssistantController,
  useConversations,
  type UseAssistantControllerResult,
  type UseConversationsResult,
} from "../react/index.js";

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const roots: Root[] = [];

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

function conversation(id: string, updatedAt: string): Conversation {
  return {
    id,
    pod_id: "pod-1",
    title: id,
    status: "WAITING",
    created_at: updatedAt,
    updated_at: updatedAt,
  } as Conversation;
}

function fakeClient(items: Conversation[]) {
  const createdConversation = conversation("created", "2026-07-16T12:00:00.000Z");
  const list = vi.fn(async () => ({
    items,
    limit: 30,
    next_page_token: null,
    total: items.length,
  }));
  const create = vi.fn(async () => createdConversation);
  const get = vi.fn(async (id: string) => (
    items.find((item) => item.id === id)
    ?? conversation(id, "2026-07-01T12:00:00.000Z")
  ));
  const encoder = new TextEncoder();
  const sendMessageStream = vi.fn(async () => new ReadableStream<Uint8Array>({
    start(controller) {
      controller.enqueue(encoder.encode('data: {"type":"completed"}\n\n'));
      controller.close();
    },
  }));
  const retryFailedRun = vi.fn(async () => ({
    conversation_id: "failed",
    agent_run_id: "retry-run-1",
    started_new_run: true,
  }));
  const resumeStream = vi.fn(async () => new ReadableStream<Uint8Array>({
    start(controller) {
      controller.enqueue(encoder.encode('data: {"type":"completed"}\n\n'));
      controller.close();
    },
  }));
  const listModels = vi.fn(async () => ({ items: [] }));

  const client = {
    podId: "pod-1",
    withPod() {
      return this;
    },
    conversations: {
      list,
      create,
      get,
      listModels,
      messages: {
        list: vi.fn(async () => ({ items: [], limit: 100, next_page_token: null })),
      },
      sendMessageStream,
      retryFailedRun,
      resumeStream,
      stopRun: vi.fn(),
      update: vi.fn(),
    },
  } as unknown as LemmaClient;

  return { client, create, get, list, listModels, resumeStream, retryFailedRun, sendMessageStream };
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

describe("explicit conversation selection", () => {
  it("loads controller history without opening the newest conversation", async () => {
    const { client, list } = fakeClient([
      conversation("newest", "2026-07-16T12:00:00.000Z"),
      conversation("older", "2026-07-15T12:00:00.000Z"),
    ]);
    const controller = captureHookResult<UseAssistantControllerResult>();

    function Harness({ agentName }: { agentName: string }) {
      controller.set(useAssistantController({
        client,
        podId: "pod-1",
        agentName,
        autoLoadMessages: false,
      }));
      return null;
    }

    const root = await render(createElement(Harness, { agentName: "alpha" }));
    await settle();

    expect(list).toHaveBeenCalledWith({
      pod_id: "pod-1",
      limit: 30,
      page_token: undefined,
    });
    expect(controller.get().conversations.map((item) => item.id)).toEqual(["newest", "older"]);
    expect(controller.get().openedConversationId).toBeNull();
    expect(controller.get().activeConversationId).toBeNull();

    await act(async () => controller.get().openConversation("older"));
    expect(controller.get().openedConversationId).toBe("older");

    await act(async () => controller.get().openConversation("off-page"));
    await settle();
    expect(controller.get().openedConversationId).toBe("off-page");
    expect(controller.get().conversations.some((item) => item.id === "off-page")).toBe(true);
    const historyBeforeAgentChange = controller.get().conversations.map((item) => item.id);

    await act(async () => {
      root.render(createElement(Harness, { agentName: "beta" }));
      await Promise.resolve();
    });
    await settle();

    expect(controller.get().openedConversationId).toBeNull();
    expect(controller.get().conversations.map((item) => item.id)).toEqual(historyBeforeAgentChange);
    expect(list).toHaveBeenCalledOnce();
  });

  it("requires list selection and preserves an explicitly selected off-page conversation", async () => {
    const { client, create, get } = fakeClient([
      conversation("newest", "2026-07-16T12:00:00.000Z"),
      conversation("older", "2026-07-15T12:00:00.000Z"),
    ]);
    const conversations = captureHookResult<UseConversationsResult>();

    function Harness({ initialConversationId }: { initialConversationId: string | null }) {
      conversations.set(useConversations({
        client,
        podId: "pod-1",
        initialConversationId,
      }));
      return null;
    }

    const root = await render(createElement(Harness, { initialConversationId: null }));
    await settle();

    expect(conversations.get().selectedConversationId).toBeNull();
    expect(conversations.get().effectiveSelectedConversationId).toBeNull();
    await act(async () => conversations.get().selectLatestConversation());
    expect(conversations.get().selectedConversationId).toBe("newest");

    await act(async () => {
      root.render(createElement(Harness, { initialConversationId: "off-page" }));
      await Promise.resolve();
    });
    await settle();

    expect(conversations.get().selectedConversationId).toBe("off-page");
    await act(async () => {
      await conversations.get().ensureConversation();
    });
    expect(get).toHaveBeenCalledWith("off-page", { pod_id: "pod-1" });
    expect(create).not.toHaveBeenCalled();
  });

  it("creates and selects a conversation only when no conversation was selected", async () => {
    const { client, create } = fakeClient([
      conversation("newest", "2026-07-16T12:00:00.000Z"),
    ]);
    const conversations = captureHookResult<UseConversationsResult>();

    function Harness() {
      conversations.set(useConversations({ client, podId: "pod-1" }));
      return null;
    }

    await render(createElement(Harness));
    await settle();

    await act(async () => {
      await conversations.get().ensureConversation({ title: "New conversation" });
    });

    expect(create).toHaveBeenCalledOnce();
    expect(conversations.get().selectedConversationId).toBe("created");
  });

  it("opens a newly created controller conversation on the first send", async () => {
    const { client, create, sendMessageStream } = fakeClient([
      conversation("newest", "2026-07-16T12:00:00.000Z"),
    ]);
    const controller = captureHookResult<UseAssistantControllerResult>();

    function Harness() {
      controller.set(useAssistantController({
        client,
        podId: "pod-1",
        autoLoadMessages: false,
      }));
      return null;
    }

    await render(createElement(Harness));
    await settle();
    expect(controller.get().openedConversationId).toBeNull();

    await act(async () => {
      await controller.get().sendMessage("hello");
    });

    expect(create).toHaveBeenCalledOnce();
    expect(sendMessageStream).toHaveBeenCalledOnce();
    expect(controller.get().openedConversationId).toBe("created");
  });

  it("keeps commands available while automatic controller loading is deferred", async () => {
    const { client, create, list, listModels, sendMessageStream } = fakeClient([]);
    const controller = captureHookResult<UseAssistantControllerResult>();

    function Harness() {
      controller.set(useAssistantController({
        client,
        podId: "pod-1",
        autoLoad: false,
        autoLoadMessages: false,
      }));
      return null;
    }

    await render(createElement(Harness));
    await settle();

    expect(list).not.toHaveBeenCalled();
    expect(listModels).not.toHaveBeenCalled();

    const attachment = new File(["notes"], "notes.txt", { type: "text/plain" });
    await act(async () => {
      await controller.get().uploadFiles([attachment], { deferUntilSend: true });
    });
    expect(controller.get().pendingFiles).toEqual([attachment]);

    await act(async () => controller.get().clearPendingFiles());
    await act(async () => {
      await controller.get().sendMessage("hello");
    });

    expect(create).toHaveBeenCalledOnce();
    expect(sendMessageStream).toHaveBeenCalledOnce();
    expect(controller.get().openedConversationId).toBe("created");
  });

  it("retries a failed run without appending a duplicate user message", async () => {
    const failedConversation = {
      ...conversation("failed", "2026-07-19T12:00:00.000Z"),
      last_run_status: "FAILED",
      last_run_error: "User daemon is not connected",
      last_run_retryable: true,
    } as Conversation;
    const { client, resumeStream, retryFailedRun, sendMessageStream } = fakeClient([
      failedConversation,
    ]);
    retryFailedRun.mockImplementationOnce(async () => {
      failedConversation.status = "RUNNING" as Conversation["status"];
      failedConversation.last_run_status = "RUNNING" as Conversation["last_run_status"];
      failedConversation.last_run_error = null;
      failedConversation.last_run_retryable = false;
      return {
        conversation_id: failedConversation.id,
        agent_run_id: "retry-run-1",
        started_new_run: true,
      };
    });
    const encoder = new TextEncoder();
    sendMessageStream.mockImplementationOnce(async () => new ReadableStream<Uint8Array>({
      start(streamController) {
        streamController.enqueue(encoder.encode(
          'data: {"type":"error","data":{"message":"User daemon is not connected"}}\n\n',
        ));
        streamController.close();
      },
    }));
    const controller = captureHookResult<UseAssistantControllerResult>();

    function Harness() {
      controller.set(useAssistantController({
        client,
        podId: "pod-1",
        autoLoadMessages: false,
      }));
      return null;
    }

    await render(createElement(Harness));
    await settle();
    await act(async () => controller.get().openConversation("failed"));
    await act(async () => controller.get().sendMessage("finish the report"));

    expect(controller.get().error).toBe("User daemon is not connected");
    expect(controller.get().canRetryFailedMessage).toBe(true);
    expect(controller.get().messages.filter((message) => message.role === "user")).toHaveLength(1);

    await act(async () => controller.get().retryFailedMessage());

    expect(retryFailedRun).toHaveBeenCalledOnce();
    expect(resumeStream).toHaveBeenCalledWith(
      "failed",
      expect.objectContaining({ agent_run_id: "retry-run-1" }),
    );
    expect(sendMessageStream).toHaveBeenCalledOnce();
    expect(controller.get().messages.filter((message) => message.role === "user")).toHaveLength(1);
    expect(controller.get().canRetryFailedMessage).toBe(false);
    expect(controller.get().error).toBeNull();
  });

  it("restores a partial failure banner without enabling retry", async () => {
    const partialFailure = {
      ...conversation("partial", "2026-07-19T12:00:00.000Z"),
      last_run_status: "FAILED",
      last_run_error: "Tool execution failed",
      last_run_retryable: false,
    } as Conversation;
    const { client } = fakeClient([partialFailure]);
    const controller = captureHookResult<UseAssistantControllerResult>();

    function Harness() {
      controller.set(useAssistantController({
        client,
        podId: "pod-1",
        autoLoadMessages: false,
      }));
      return null;
    }

    await render(createElement(Harness));
    await settle();
    await act(async () => controller.get().openConversation("partial"));
    await settle();

    expect(controller.get().error).toBe("Tool execution failed");
    expect(controller.get().canRetryFailedMessage).toBe(false);
  });

  it("does not enable retry for a generic transport failure", async () => {
    const { client, sendMessageStream } = fakeClient([
      conversation("waiting", "2026-07-19T12:00:00.000Z"),
    ]);
    sendMessageStream.mockRejectedValueOnce(new Error("Network unavailable"));
    const controller = captureHookResult<UseAssistantControllerResult>();

    function Harness() {
      controller.set(useAssistantController({
        client,
        podId: "pod-1",
        autoLoadMessages: false,
      }));
      return null;
    }

    await render(createElement(Harness));
    await settle();
    await act(async () => controller.get().openConversation("waiting"));
    await act(async () => controller.get().sendMessage("hello"));

    expect(controller.get().error).toBe("Network unavailable");
    expect(controller.get().canRetryFailedMessage).toBe(false);
  });
});
