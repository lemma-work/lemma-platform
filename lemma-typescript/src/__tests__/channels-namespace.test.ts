import { afterEach, describe, expect, it, vi } from "vitest";
import type { GeneratedClientAdapter } from "../generated.js";
import type { HttpClient } from "../http.js";
import { ChannelsNamespace } from "../namespaces/channels.js";
import { ChannelsService } from "../openapi_client/services/ChannelsService.js";

const passthroughAdapter = { request: (op: () => unknown) => op() } as unknown as GeneratedClientAdapter;
const noHttp = {} as HttpClient;

afterEach(() => vi.restoreAllMocks());

describe("ChannelsNamespace", () => {
  // The pod and the channel are both strings: a swap type-checks and asks
  // for the wrong channel.
  it("passes the pod before the channel, in path order", async () => {
    const get = vi.spyOn(ChannelsService, "channelGet").mockResolvedValue({} as never);
    const remove = vi.spyOn(ChannelsService, "channelMemberRemove").mockResolvedValue(undefined as never);
    const leave = vi.spyOn(ChannelsService, "channelMemberLeave").mockResolvedValue(undefined as never);
    const channels = new ChannelsNamespace(passthroughAdapter, noHttp);

    await channels.get("pod-1", "channel-1");
    await channels.removeMember("pod-1", "channel-1", "AGENT", "agent-1");
    await channels.leave("pod-1", "channel-1");

    expect(get).toHaveBeenCalledWith("pod-1", "channel-1");
    expect(remove).toHaveBeenCalledWith("pod-1", "channel-1", "AGENT", "agent-1");
    expect(leave).toHaveBeenCalledWith("pod-1", "channel-1");
  });

  it("marks a channel read up to a line", async () => {
    const read = vi.spyOn(ChannelsService, "channelRead").mockResolvedValue({ read_seq: 7 } as never);

    await expect(new ChannelsNamespace(passthroughAdapter, noHttp).markRead("pod-1", "channel-1", 7)).resolves.toEqual({ read_seq: 7 });

    expect(read).toHaveBeenCalledWith("pod-1", "channel-1", { seq: 7 });
  });

  it("switches a contact's agent by changing only agents_answer", async () => {
    const update = vi.spyOn(ChannelsService, "channelUpdate").mockResolvedValue({} as never);
    const channels = new ChannelsNamespace(passthroughAdapter, noHttp);

    await channels.setAgentAnswers("pod-1", "channel-1", false);
    await channels.setAgentAnswers("pod-1", "channel-1", true, { handBackAfterHours: null });

    expect(update).toHaveBeenNthCalledWith(1, "pod-1", "channel-1", { agents_answer: false });
    expect(update).toHaveBeenNthCalledWith(2, "pod-1", "channel-1", { agents_answer: true, hand_back_after_hours: null });
  });

  it("pages a timeline by seq", async () => {
    const list = vi.spyOn(ChannelsService, "channelMessageList").mockResolvedValue({ items: [] } as never);

    await new ChannelsNamespace(passthroughAdapter, noHttp).messages("pod-1", "channel-1", { beforeSeq: 40, limit: 20 });

    expect(list).toHaveBeenCalledWith("pod-1", "channel-1", 40, undefined, 20);
  });
});
