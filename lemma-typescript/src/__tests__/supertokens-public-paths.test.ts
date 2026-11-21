import { afterAll, beforeAll, describe, expect, it, vi } from "vitest";

import { ensureCookieSessionSupport, isPublicApiPath } from "../supertokens.js";

/*
 * The real SuperTokens interceptor, not a mock of it: what broke the web widget
 * on a hosted Public app was the interceptor patching `window.fetch` for every
 * script on the page, so the proof has to go through the patched fetch.
 */

const API = "https://api.x.test";

/** What reached the network for `url`, past anything SuperTokens sent itself. */
function sent(network: ReturnType<typeof vi.fn>, url: string): RequestInit {
  const call = network.mock.calls.find(([target]) => String(target) === url);
  if (!call) throw new Error(`nothing was sent to ${url}`);
  return (call[1] ?? {}) as RequestInit;
}

describe("SuperTokens leaves the API's /public/ endpoints alone", () => {
  const network = vi.fn(async () => new Response("{}", { status: 200, headers: { "content-type": "application/json" } }));
  const realFetch = window.fetch;

  beforeAll(() => {
    window.fetch = network as unknown as typeof window.fetch;
    ensureCookieSessionSupport(API);
  });

  afterAll(() => {
    window.fetch = realFetch;
  });

  it("sends a widget's call with only the headers the widget set", async () => {
    network.mockClear();
    const url = `${API}/public/web/pk_abc/session`;
    await window.fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: "{}",
      credentials: "omit",
    });

    const init = sent(network, url);
    const headers = new Headers(init.headers);
    expect(headers.has("rid")).toBe(false);
    expect(headers.has("st-auth-mode")).toBe(false);
    expect(init.credentials).toBe("omit");
    // Nothing else either: no session refresh on the page's behalf.
    expect(network).toHaveBeenCalledTimes(1);
  });

  it("still signs a member's own calls to the API", async () => {
    network.mockClear();
    const url = `${API}/pods/p/datastore/tables`;
    await window.fetch(url);

    const headers = new Headers(sent(network, url).headers);
    expect(headers.get("rid")).toBe("anti-csrf");
    expect(headers.get("st-auth-mode")).toBe("cookie");
  });
});

describe("isPublicApiPath", () => {
  it.each([
    ["https://api.x.test/public/web/pk_abc/rows?table=t", "", true],
    ["/public/web/pk_abc/session", "", true],
    ["https://app.x.test/_lemma/public/web/pk_abc/session", "/_lemma", true],
    ["https://api.x.test/public/web/pk_abc/session", "/_lemma", true],
    ["https://api.x.test/pods/p/apps", "", false],
    ["https://api.x.test/publicity", "", false],
    ["https://app.x.test/_lemma/pods/p/apps", "/_lemma", false],
    ["https://app.x.test/_lemma/app-access/redeem", "/_lemma", false],
  ])("%s under %j is %s", (url, prefix, expected) => {
    expect(isPublicApiPath(url, prefix)).toBe(expected);
  });
});
