import { afterEach, describe, expect, it, vi } from "vitest";
import { LemmaClient } from "../client.js";
import { clearTestingToken, setTestingToken } from "../auth.js";

/** The Connections page's calls that are not in the generated client yet go
 *  through the raw request helper; lock the paths and bodies they send. */

afterEach(() => {
  clearTestingToken();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

function answering(body: unknown) {
  const fetch = vi.fn().mockImplementation(async () =>
    new Response(JSON.stringify(body), { status: 200, headers: { "content-type": "application/json" } }));
  vi.stubGlobal("fetch", fetch);
  setTestingToken("TOKEN");
  return fetch;
}

function sent(fetch: ReturnType<typeof vi.fn>): { url: string; method: string; body: unknown } {
  const [url, init] = fetch.mock.calls[0] as [string, RequestInit];
  return { url: String(url), method: String(init.method), body: init.body ? JSON.parse(String(init.body)) : undefined };
}

const client = () => new LemmaClient({ apiUrl: "https://api.test", authUrl: "https://auth.test", podId: "p1" });

describe("pod connector routes", () => {
  it("reads a connector's coverage", async () => {
    const fetch = answering({ eligible: 2, connected: 1, waiting_on: [], truncated: false });
    const coverage = await client().connectors.pod.coverage("gmail");
    expect(coverage.connected).toBe(1);
    expect(sent(fetch)).toMatchObject({ url: "https://api.test/pods/p1/connectors/gmail/coverage", method: "GET" });
  });

  it("asks members, everyone or only some", async () => {
    const fetch = answering({ asked: [], asked_recently: [], not_asked: [], connect_url: "x" });
    await client().connectors.pod.askMembers("gmail", { userIds: ["u1"] });
    expect(sent(fetch)).toMatchObject({
      url: "https://api.test/pods/p1/connectors/gmail/ask-members", method: "POST", body: { user_ids: ["u1"] },
    });
  });

  it("shares an account as a connector in one call", async () => {
    const fetch = answering({ slug: "gmail-shared" });
    await client().connectors.pod.accounts.shareAs("pa1", "gmail-shared", { purpose: "Support inbox" });
    expect(sent(fetch)).toMatchObject({
      url: "https://api.test/pods/p1/connector-accounts/pa1/share-as",
      method: "POST",
      body: { slug: "gmail-shared", purpose: "Support inbox" },
    });
  });

  it("reads my organization permissions", async () => {
    const fetch = answering({ organization_id: "o1", actions: ["org.read"] });
    const mine = await client().organizations.permissionsMe("o1");
    expect(mine.actions).toEqual(["org.read"]);
    expect(sent(fetch)).toMatchObject({ url: "https://api.test/organizations/o1/permissions/me", method: "GET" });
  });
});
