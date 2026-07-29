import { afterEach, describe, expect, it, vi } from "vitest";
import type { GeneratedClientAdapter } from "../generated.js";
import { AgentsNamespace } from "../namespaces/agents.js";
import { AgentHostNamespace } from "../namespaces/agent-host.js";
import { FunctionsNamespace } from "../namespaces/functions.js";
import type { ConversationsNamespace } from "../namespaces/conversations.js";
import { FunctionsService } from "../openapi_client/services/FunctionsService.js";
import { AgentHostService } from "../openapi_client/services/AgentHostService.js";

// A pass-through adapter: invoke the thunk and return its result (no retry/timeout needed here).
const passthroughAdapter = { request: (op: () => unknown) => op() } as unknown as GeneratedClientAdapter;

afterEach(() => vi.restoreAllMocks());

describe("AgentHostNamespace", () => {
  it("exposes only authenticated management operations", async () => {
    const listSpy = vi
      .spyOn(AgentHostService, "agentHostList")
      .mockResolvedValue({ items: [] } as never);
    const harnessSpy = vi
      .spyOn(AgentHostService, "agentHostHarnessesList")
      .mockResolvedValue({ items: [] } as never);
    const hosts = new AgentHostNamespace(passthroughAdapter);

    await expect(hosts.list()).resolves.toEqual({ items: [] });
    await expect(hosts.listHarnesses("host-1")).resolves.toEqual({ items: [] });
    expect(listSpy).toHaveBeenCalledOnce();
    expect(harnessSpy).toHaveBeenCalledWith("host-1");
    expect("poll" in hosts).toBe(false);
  });

  it("delegates pairing and revocation", async () => {
    const pairingSpy = vi
      .spyOn(AgentHostService, "agentHostPairingCreate")
      .mockResolvedValue({
        pairing_id: "pair-1",
        pairing_code: "ABCD-EFGH",
        expires_at: "2026-07-27T00:00:00Z",
      } as never);
    const revokeSpy = vi
      .spyOn(AgentHostService, "agentHostRevoke")
      .mockResolvedValue({ id: "host-1" } as never);
    const hosts = new AgentHostNamespace(passthroughAdapter);

    await hosts.createPairing({
      display_name: "My computer",
      organization_id: "org-1",
    });
    await hosts.revoke("host-1");

    expect(pairingSpy).toHaveBeenCalledWith({
      display_name: "My computer",
      organization_id: "org-1",
    });
    expect(revokeSpy).toHaveBeenCalledWith("host-1");
  });
});

describe("FunctionsNamespace.run", () => {
  it("delegates to runs.create with input wrapped as input_data", async () => {
    const spy = vi
      .spyOn(FunctionsService, "functionRun")
      .mockResolvedValue({ id: "run1" } as never);
    const fns = new FunctionsNamespace(passthroughAdapter, () => "pod1");

    const result = await fns.run("my_fn", { input: { a: 1 } });

    expect(result).toEqual({ id: "run1" });
    expect(spy).toHaveBeenCalledWith("pod1", "my_fn", { input_data: { a: 1 } });
  });
});

describe("AgentsNamespace.run", () => {
  function fakeConversations() {
    return {
      createForAgent: vi.fn(async () => ({ id: "conv1" })),
      sendMessageStream: vi.fn(async () => "STREAM"),
      messages: { send: vi.fn(async () => undefined) },
    } as unknown as ConversationsNamespace;
  }

  it("opens a conversation, sends the message, and returns the conversation", async () => {
    const conversations = fakeConversations();
    const agents = new AgentsNamespace(passthroughAdapter, () => "pod1", () => conversations);

    const conv = await agents.run("my_agent", "hello", { title: "T" });

    expect(conv).toEqual({ id: "conv1" });
    expect(conversations.createForAgent).toHaveBeenCalledWith("my_agent", {
      title: "T",
      metadata: undefined,
    });
    expect(conversations.messages.send).toHaveBeenCalledWith("conv1", { content: "hello" });
  });

  it("returns the SSE stream when stream: true", async () => {
    const conversations = fakeConversations();
    const agents = new AgentsNamespace(passthroughAdapter, () => "pod1", () => conversations);

    const stream = await agents.run("my_agent", "hello", { stream: true });

    expect(stream).toBe("STREAM");
    expect(conversations.sendMessageStream).toHaveBeenCalledWith(
      "conv1",
      { content: "hello" },
      { signal: undefined },
    );
    expect(conversations.messages.send).not.toHaveBeenCalled();
  });

  it("throws a clear error when the conversations namespace is unavailable", async () => {
    const agents = new AgentsNamespace(passthroughAdapter, () => "pod1");
    await expect(agents.run("my_agent", "hello")).rejects.toThrow(/conversations namespace/);
  });
});
