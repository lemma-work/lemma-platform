import { describe, expect, it, vi } from "vitest";
import type { HttpClient } from "../http.js";
import { ConversationsNamespace } from "../namespaces/conversations.js";

function setup() {
  const request = vi.fn(async () => ({
    items: [],
    limit: 20,
    next_page_token: null,
  }));
  const stream = vi.fn(async () => new ReadableStream<Uint8Array>());
  const http = { request, stream } as unknown as HttpClient;
  return {
    conversations: new ConversationsNamespace(http, () => "pod-1"),
    request,
    stream,
  };
}

describe("ConversationsNamespace.list", () => {
  it("omits agent_name when listing conversations across the pod", async () => {
    const { conversations, request } = setup();

    await conversations.list();

    expect(request).toHaveBeenCalledWith(
      "GET",
      "/pods/pod-1/conversations",
      expect.objectContaining({
        params: expect.objectContaining({ agent_name: undefined }),
      }),
    );
  });

  it("encodes explicit null as POD_DEFAULT for the default assistant", async () => {
    const { conversations, request } = setup();

    await conversations.list({ agent_name: null });

    expect(request).toHaveBeenCalledWith(
      "GET",
      "/pods/pod-1/conversations",
      expect.objectContaining({
        params: expect.objectContaining({ agent_name: "POD_DEFAULT" }),
      }),
    );
  });

  it("keeps named-agent filtering explicit", async () => {
    const { conversations, request } = setup();

    await conversations.listByAgent("researcher");

    expect(request).toHaveBeenCalledWith(
      "GET",
      "/pods/pod-1/conversations",
      expect.objectContaining({
        params: expect.objectContaining({ agent_name: "researcher" }),
      }),
    );
  });

  it("lists default-assistant conversations through listDefault", async () => {
    const { conversations, request } = setup();

    await conversations.listDefault();

    expect(request).toHaveBeenCalledWith(
      "GET",
      "/pods/pod-1/conversations",
      expect.objectContaining({
        params: expect.objectContaining({ agent_name: "POD_DEFAULT" }),
      }),
    );
  });

  it("starts a failed-run retry and returns the run identity", async () => {
    const { conversations, request } = setup();

    await conversations.retryFailedRun("conversation-1");

    expect(request).toHaveBeenCalledWith(
      "POST",
      "/pods/pod-1/conversations/conversation-1/retry",
      expect.objectContaining({ signal: undefined }),
    );
  });

  it("filters a resumed stream to the requested run", async () => {
    const { conversations, stream } = setup();

    await conversations.resumeStream("conversation-1", { agent_run_id: "run-1" });

    expect(stream).toHaveBeenCalledWith(
      "/pods/pod-1/conversations/conversation-1/stream",
      expect.objectContaining({ params: { agent_run_id: "run-1" } }),
    );
  });
});
