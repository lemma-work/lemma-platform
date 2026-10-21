// @vitest-environment node
import { afterEach, describe, expect, it, vi } from "vitest";
import { clearTestingToken, setTestingToken } from "../auth.js";
import { LemmaClient } from "../client.js";

// Sharing an agent's answer with the channel goes through the real client;
// only `fetch` is stubbed.

function signedInClient(): LemmaClient {
  setTestingToken("TESTTOKEN");
  return new LemmaClient({
    apiUrl: "https://api.example.test",
    authUrl: "https://auth.example.test",
    podId: "pod-1",
  });
}

afterEach(() => {
  clearTestingToken();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("channels.share", () => {
  it("posts to the line's share route and returns the line", async () => {
    const line = { id: "line-1", text: "Within 30 days.", visibility: "channel" };
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify(line), { status: 200, headers: { "content-type": "application/json" } }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const shared = await signedInClient().channels.share("pod-1", "channel-1", "line-1");

    expect(shared).toEqual(line);
    const [url, init] = fetchMock.mock.calls[0] ?? [];
    expect(url).toBe("https://api.example.test/pods/pod-1/channels/channel-1/messages/line-1/share");
    expect((init as RequestInit).method).toBe("POST");
  });
});
