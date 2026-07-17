import { describe, expect, it, vi } from "vitest";
import type { HttpClient } from "../http.js";
import { ConversationsNamespace } from "../namespaces/conversations.js";

function setup() {
  const request = vi.fn(async () => ({
    items: [],
    limit: 20,
    next_page_token: null,
  }));
  const http = { request } as unknown as HttpClient;
  return {
    conversations: new ConversationsNamespace(http, () => "pod-1"),
    request,
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

  it("encodes explicit null as an empty agent_name for the default assistant", async () => {
    const { conversations, request } = setup();

    await conversations.list({ agent_name: null });

    expect(request).toHaveBeenCalledWith(
      "GET",
      "/pods/pod-1/conversations",
      expect.objectContaining({
        params: expect.objectContaining({ agent_name: "" }),
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
        params: expect.objectContaining({ agent_name: "" }),
      }),
    );
  });
});
